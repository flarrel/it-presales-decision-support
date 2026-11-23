"""Ticket intake, SQL metrics, demand backtesting, and staffing decisions."""

from __future__ import annotations

import csv
import math
import random
import sqlite3
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from statistics import mean


TICKET_FIELDS = (
    "ticket_id", "opened_at", "resolved_at", "queue", "priority", "sla_minutes",
    "handling_minutes",
)
PRIORITIES = ("critical", "high", "normal", "low")
WEEK = timedelta(weeks=1)
HORIZON = 4
SEASON = 52
EVALUATION_WEEKS = 12
MODELS = ("seasonal_naive", "trailing_average")
SCHEMA = Path(__file__).with_name("schema.sql")


def generate_fixture(csv_path: Path, *, seed: int = 581) -> int:
    """Write synthetic tickets, including a few intentionally invalid records."""
    rng = random.Random(seed)
    queues = {"Cloud": (64, 37), "Endpoints": (82, 24), "Network": (72, 33)}
    first_week = date(2024, 1, 1)
    count = 0
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(TICKET_FIELDS)
        for week_number in range(96):
            monday = datetime.combine(first_week + week_number * WEEK, time.min)
            for offset, (queue, (volume, average_handling)) in enumerate(queues.items()):
                seasonality = 16 * math.sin(2 * math.pi * (week_number + offset * 8) / SEASON)
                weekly_volume = max(1, round(volume + seasonality + rng.gauss(0, 7)))
                for _ in range(weekly_volume):
                    count += 1
                    opened = monday + timedelta(minutes=rng.randrange(7 * 24 * 60))
                    priority = rng.choices(PRIORITIES, weights=(4, 18, 60, 18))[0]
                    sla = {"critical": 120, "high": 240, "normal": 480, "low": 720}[priority]
                    handling = max(5, round(rng.gauss(average_handling, 12)))
                    delay = round(rng.expovariate(1 / (80 + 4 * max(0, weekly_volume - volume))))
                    resolved = opened + timedelta(minutes=handling + delay)
                    if week_number == 95 and rng.random() < 0.12:
                        resolved, handling = None, None
                    row = (
                        f"T-{count:05d}", opened.isoformat(),
                        resolved.isoformat() if resolved else "", queue, priority, sla, handling,
                    )
                    if count == 1:
                        first_ticket = row
                    writer.writerow(row)
        writer.writerow(first_ticket)
        writer.writerow(("BAD-QUEUE", "2024-01-03T12:00:00", "", "", "normal", 480, ""))
        writer.writerow(("BAD-TIME", "bad-time", "", "Cloud", "normal", 480, ""))
        writer.writerow(("BAD-SLA", "2024-01-03T12:00:00", "", "Cloud", "normal", 0, ""))
    return count + 4


def _timestamp(value: str) -> datetime:
    if "T" not in value and " " not in value:
        raise ValueError("expected date and time")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _validated_ticket(row: dict, seen: set[str]) -> tuple[tuple | None, str | None]:
    if None in row or any(row[field] is None for field in TICKET_FIELDS):
        return None, "column_count"
    ticket_id, opened_at, resolved_at, queue, priority, sla_text, handling_text = (
        row[field].strip() for field in TICKET_FIELDS
    )
    if not ticket_id:
        return None, "missing_ticket_id"
    if ticket_id in seen:
        return None, "duplicate_ticket_id"
    if not queue:
        return None, "missing_queue"
    if priority.lower() not in PRIORITIES:
        return None, "invalid_priority"
    try:
        opened = _timestamp(opened_at)
        resolved = _timestamp(resolved_at) if resolved_at else None
    except (ValueError, OverflowError):
        return None, "invalid_timestamp"
    if resolved is not None and resolved <= opened:
        return None, "resolution_precedes_open"
    try:
        sla = int(sla_text)
        if not 0 < sla <= 525_600:
            raise ValueError
    except ValueError:
        return None, "invalid_sla_minutes"
    try:
        handling = int(handling_text) if handling_text else None
        if resolved is None and handling is not None:
            raise ValueError
        if resolved is not None and (
            handling is None or not 0 < handling <= (resolved - opened).total_seconds() / 60
        ):
            raise ValueError
    except ValueError:
        return None, "invalid_handling_minutes"
    return (
        ticket_id, opened.isoformat(timespec="microseconds"),
        resolved.isoformat(timespec="microseconds") if resolved else None,
        queue, priority.lower(), sla, handling,
    ), None


def load_tickets(csv_path: Path, db_path: Path) -> dict:
    """Replace tickets atomically with valid rows and count each rejection once."""
    issues: Counter = Counter()
    rows = []
    seen: set[str] = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        fields = reader.fieldnames or []
        if len(fields) != len(TICKET_FIELDS) or set(fields) != set(TICKET_FIELDS):
            raise ValueError(f"Ticket CSV must contain these columns: {', '.join(TICKET_FIELDS)}")
        for row in reader:
            ticket, issue = _validated_ticket(row, seen)
            if ticket is None:
                issues[issue] += 1
                continue
            seen.add(ticket[0])
            rows.append(ticket)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript(SCHEMA.read_text(encoding="utf-8"))
        with connection:
            connection.execute("DELETE FROM tickets")
            connection.executemany("INSERT INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return {"accepted": len(rows), "rejected": sum(issues.values()), "issues": dict(issues)}


def query_rows(
    db_path: Path, dataset: str, *, queue: str | None = None, start: str | None = None,
    end: str | None = None,
) -> list[dict]:
    """Extract ticket rows or Monday-based weekly metrics, with inclusive dates."""
    if dataset == "weekly":
        query, day, order = "SELECT * FROM weekly_metrics", "week", "week, queue"
    elif dataset == "tickets":
        query, day, order = "SELECT * FROM ticket_details", "substr(opened_at, 1, 10)", "opened_at, ticket_id"
    else:
        raise ValueError(f"Unknown dataset: {dataset}")
    if start is not None:
        start = date.fromisoformat(start).isoformat()
    if end is not None:
        end = date.fromisoformat(end).isoformat()
    if start and end and start > end:
        raise ValueError("Start date must be on or before end date")
    filters, parameters = [], []
    for expression, value in (("queue = ?", queue), (f"{day} >= ?", start), (f"{day} <= ?", end)):
        if value is not None:
            filters.append(expression)
            parameters.append(value)
    if filters:
        query += " WHERE " + " AND ".join(filters)
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(f"{query} ORDER BY {order}", parameters)]


def _week_predictions(counts: list[int], target: int, history_end: int) -> dict:
    available = {"trailing_average": mean(counts[history_end - HORIZON:history_end])}
    seasonal_index = target - SEASON
    if 0 <= seasonal_index < history_end:
        available["seasonal_naive"] = counts[seasonal_index]
    return available


def _rolling_predictions(
    queue: str, first: date, counts: list[int], start: int, stop: int,
) -> list[dict]:
    return [
        {
            "queue": queue,
            "week": (first + target * WEEK).isoformat(),
            "origin_week": (first + (history_end - 1) * WEEK).isoformat(),
            "horizon": target - history_end + 1,
            "actual": counts[target],
            **_week_predictions(counts, target, history_end),
        }
        for target in range(start, stop)
        for history_end in range(max(HORIZON, target - HORIZON + 1), target + 1)
    ]


def _mae(rows: list[dict], model: str) -> float:
    return mean(abs(row[model] - row["actual"]) for row in rows)


def forecast(db_path: Path) -> dict:
    """Choose on earlier target weeks and report a separate rolling holdout."""
    result = {"horizon_weeks": HORIZON, "metrics": [], "backtest": [], "predictions": [],
              "unavailable": []}
    weekly = query_rows(db_path, "weekly")
    if not weekly:
        return result
    last = date.fromisoformat(weekly[-1]["week"])
    groups: dict[str, dict[date, int]] = defaultdict(dict)
    for row in weekly:
        groups[row["queue"]][date.fromisoformat(row["week"])] = row["tickets"]
    for queue, observed in sorted(groups.items()):
        first = min(observed)
        rows = (last - first) // WEEK + 1
        counts = [observed.get(first + week * WEEK, 0) for week in range(rows)]
        if len(counts) < HORIZON:
            result["unavailable"].append(queue)
            continue
        holdout_size = min(EVALUATION_WEEKS, max(0, (rows - HORIZON) // 2))
        holdout_start = rows - holdout_size
        selection = _rolling_predictions(queue, first, counts, holdout_start - holdout_size, holdout_start)
        holdout = _rolling_predictions(queue, first, counts, holdout_start, rows)
        comparable_selection = [row for row in selection if "seasonal_naive" in row]
        comparable_holdout = [row for row in holdout if "seasonal_naive" in row]
        candidates = ("trailing_average",)
        selected = candidates[0]
        if len(comparable_selection) >= HORIZON and comparable_holdout:
            candidates = MODELS
            selected = min(candidates, key=lambda model: (_mae(comparable_selection, model), model))
        scored = comparable_holdout if len(candidates) > 1 else holdout
        for model in candidates:
            result["metrics"].append({
                "queue": queue, "model": model, "n": len(scored),
                "mae": _mae(scored, model) if scored else None, "selected": model == selected,
            })
        result["backtest"].extend(scored)
        for target in range(rows, rows + HORIZON):
            predicted = _week_predictions(counts, target, rows)[selected]
            result["predictions"].append({
                "queue": queue, "week": (first + target * WEEK).isoformat(),
                "predicted": predicted, "model": selected,
            })
    return result


def _nonnegative_number(value: object, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a finite nonnegative number") from exc
    if isinstance(value, bool) or not math.isfinite(number) or number < 0:
        raise ValueError(f"{field} must be a finite nonnegative number")
    return number


def _staffing_rows(predictions: list[dict], scenario: dict, coverage: float) -> tuple[list[dict], list[str]]:
    rows, reasons = [], []
    for prediction in sorted(predictions, key=lambda item: (item["week"], item["queue"])):
        queue, week = prediction["queue"], prediction["week"]
        demands = _nonnegative_number(prediction["predicted"], "predicted tickets")
        minutes = _nonnegative_number(scenario.get("minutes_per_ticket", {}).get(queue, 30), "minutes per ticket")
        baseline = _nonnegative_number(scenario.get("baseline_hours", {}).get(queue), f"baseline hours for {queue}")
        cap = _nonnegative_number(scenario.get("max_extra_hours", {}).get(queue), f"extra-hours cap for {queue}")
        if minutes == 0 or not cap.is_integer():
            raise ValueError("Handling time must be positive and extra-hours caps must be integers")
        workload = demands * minutes / 60
        minimum = math.ceil(max(0.0, workload * coverage - baseline) - 1e-9)
        if minimum > cap:
            reasons.append(f"{queue} on {week} needs {minimum} extra hours for coverage; its cap is {int(cap)}.")
        rows.append({
            "queue": queue, "week": week, "predicted": demands, "minutes_per_ticket": minutes,
            "workload_hours": workload, "baseline_hours": baseline, "max_extra_hours": int(cap),
            "extra_hours": minimum,
        })
    return rows, reasons


def _allocate_hours(rows: list[dict], remaining: int) -> None:
    """Minimize unmet hours, then cost, with identical cost per extra hour."""
    fractional = []
    for row in rows:
        spare = row["max_extra_hours"] - row["extra_hours"]
        deficit = max(0.0, row["workload_hours"] - row["baseline_hours"] - row["extra_hours"])
        whole = min(math.floor(deficit), spare, remaining)
        row["extra_hours"] += whole
        remaining -= whole
        benefit = deficit - whole
        if spare > whole and benefit > 1e-9:
            fractional.append((benefit, row))
    fractional.sort(key=lambda candidate: -candidate[0])
    for _, row in fractional[:remaining]:
        row["extra_hours"] += 1


def recommend(predictions: list[dict], scenario: dict) -> dict:
    """Compare baseline staffing with a least-unmet-workload feasible option."""
    budget = _nonnegative_number(scenario.get("budget"), "budget")
    rate = _nonnegative_number(scenario.get("hourly_rate"), "hourly rate")
    coverage = _nonnegative_number(scenario.get("minimum_coverage"), "minimum coverage")
    if rate == 0 or coverage > 1:
        raise ValueError("Hourly rate must be positive and minimum coverage must be between 0 and 1")
    rows, reasons = _staffing_rows(predictions, scenario, coverage)
    rate_decimal = Decimal(str(rate))
    budget_hours = int(Decimal(str(budget)) // rate_decimal)
    mandatory_hours = sum(row["extra_hours"] for row in rows)
    if mandatory_hours > budget_hours:
        reasons.append(f"Minimum coverage requires {mandatory_hours} extra hours; budget covers {budget_hours}.")
    if not rows:
        reasons.append("Demand forecasts are unavailable.")
    if reasons:
        for row in rows:
            row["extra_hours"] = 0
    else:
        _allocate_hours(rows, budget_hours - mandatory_hours)
    for row in rows:
        staffing = row["baseline_hours"] + row["extra_hours"]
        workload = row["workload_hours"]
        row.update({
            "staffed_hours": staffing, "unmet_hours": max(0.0, workload - staffing),
            "coverage": min(1.0, staffing / workload) if workload else 1.0,
            "extra_cost": float(row["extra_hours"] * rate_decimal),
        })
    extra_hours = sum(row["extra_hours"] for row in rows)
    return {
        "name": str(scenario.get("name", "Staffing scenario")), "feasible": not reasons,
        "reasons": reasons, "budget": budget, "hourly_rate": rate, "minimum_coverage": coverage,
        "baseline_unmet_hours": sum(max(0.0, row["workload_hours"] - row["baseline_hours"]) for row in rows),
        "recommended_unmet_hours": sum(row["unmet_hours"] for row in rows) if not reasons else None,
        "extra_hours": extra_hours, "extra_cost": float(extra_hours * rate_decimal), "rows": rows,
    }
