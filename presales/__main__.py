"""Build and serve the local service desk analysis."""

import argparse
import csv
from contextlib import closing
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from urllib.parse import parse_qs, urlsplit

from .analytics import forecast, generate_fixture, load_tickets, query_rows

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent
DATA = ROOT / "run"
WEB = PACKAGE / "web"
EXPORTS = {
    "tickets": "tickets.csv",
    "weekly": "weekly.csv",
    "staffing": "staffing.csv",
    "sensitivity": "sensitivity.csv",
}
TICKET_COLUMNS = (
    "ticket_id", "opened_at", "resolved_at", "queue", "priority", "sla_minutes",
    "handling_minutes", "week", "sla_breached",
)
WEEKLY_COLUMNS = (
    "queue", "week", "tickets", "resolved_tickets", "open_tickets", "sla_breaches",
    "total_handling_minutes",
)
STAFFING_COLUMNS = (
    "scenario", "queue", "week", "predicted_tickets", "scheduled_hours",
    "baseline_hours", "extra_hours", "workload_hours", "unmet_hours", "extra_cost",
)
SENSITIVITY_COLUMNS = (
    "scenario", "queue", "week", "assumption", "source", "observations",
    "handling_minutes", "predicted_tickets", "scheduled_hours", "required_hours",
    "shortfall_hours", "covered",
)


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def scenario_plans(weekly, predictions, settings):
    from .analytics import recommend

    queues = sorted({row["queue"] for row in predictions})
    if not queues:
        return []
    if settings is not None:
        names = [item.get("name") for item in settings]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("Each staffing scenario must have a name")
        if len(names) != len(set(names)):
            raise ValueError("Staffing scenario names must be unique")
        for item in settings:
            for key in ("baseline_hours", "minutes_per_ticket", "max_extra_hours"):
                if set(item[key]) != set(queues):
                    raise ValueError(f"{key} must specify every forecasted queue")
        return [recommend(predictions, item) for item in settings]

    observed = {}
    baseline = {}
    for queue in queues:
        rows = [row for row in weekly if row["queue"] == queue][-12:]
        minutes = sum(row["total_handling_minutes"] for row in rows)
        resolved = sum(row["resolved_tickets"] for row in rows)
        if resolved == 0 or minutes <= 0:
            raise ValueError(
                f"No positive handling duration for {queue}. Supply scenario assumptions."
            )
        observed[queue] = round(minutes / resolved, 2)
        weekly_demand = sum(row["tickets"] for row in rows) / len(rows)
        baseline[queue] = max(1, round(weekly_demand * observed[queue] / 60 * 0.8))

    shared = {
        "hourly_rate": 25,
        "baseline_hours": baseline,
        "max_extra_hours": {queue: max(8, baseline[queue] // 2) for queue in queues},
        "minutes_per_ticket": observed,
    }
    scenarios = [
        {"name": "Fixed budget", "budget": 2000, "minimum_coverage": 0.85},
        {"name": "Higher service target", "budget": 4800, "minimum_coverage": 0.97},
    ]
    return [recommend(predictions, shared | scenario) for scenario in scenarios]


def publish_reporting_tables(connection, weekly, staffing, sensitivity):
    """Publish stable, typed reporting tables for BI consumers."""
    connection.executescript("""
        CREATE TABLE reporting_weekly (
            queue TEXT, week DATE, tickets INTEGER, resolved_tickets INTEGER,
            open_tickets INTEGER, sla_breaches INTEGER, total_handling_minutes REAL
        );
        CREATE TABLE reporting_staffing (
            scenario TEXT, queue TEXT, week DATE, predicted_tickets REAL, scheduled_hours REAL,
            baseline_hours REAL, extra_hours REAL, workload_hours REAL, unmet_hours REAL,
            extra_cost REAL
        );
        CREATE TABLE reporting_sensitivity (
            scenario TEXT, queue TEXT, week DATE, assumption TEXT, source TEXT,
            observations INTEGER, handling_minutes REAL, predicted_tickets REAL,
            scheduled_hours REAL, required_hours REAL, shortfall_hours REAL, covered BOOLEAN
        );
    """)
    for table, columns, rows in (
        ("reporting_weekly", WEEKLY_COLUMNS, weekly),
        ("reporting_staffing", STAFFING_COLUMNS, staffing),
        ("reporting_sensitivity", SENSITIVITY_COLUMNS, sensitivity),
    ):
        placeholders = ", ".join("?" for _ in columns)
        connection.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",
            ([row.get(column) for column in columns] for row in rows),
        )


def run_sensitivity(output, staffing, weekly, rscript):
    if not staffing:
        return [], "No forecast-based staffing rows are available"
    if not any(row["total_handling_minutes"] for row in weekly):
        return [], "No resolved-ticket handling times are available"
    local_r = ROOT / "runtime" / "r" / "bin" / "Rscript"
    executable = str(Path(rscript).expanduser().resolve()) if rscript else shutil.which("Rscript")
    if not executable and local_r.is_file():
        executable = str(local_r)
    if not executable:
        raise ValueError("Rscript was not found. Set --rscript to the Rscript executable.")

    output = output.resolve()
    # R rejects spaces in TMPDIR. Keep a relative temp path inside the project.
    (ROOT / "runtime" / "tmp").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [executable, "--vanilla", str(PACKAGE / "staffing_sensitivity.R"),
         str(output / "tickets.csv"), str(output / "staffing.csv"),
         str(output / "sensitivity.csv")],
        cwd=ROOT, env=os.environ | {"TMPDIR": "runtime/tmp"}, check=True,
    )
    with (output / "sensitivity.csv").open(encoding="utf-8", newline="") as stream:
        rows = []
        for row in csv.DictReader(stream):
            for column in ("handling_minutes", "predicted_tickets", "scheduled_hours",
                           "required_hours", "shortfall_hours"):
                row[column] = float(row[column]) if row[column] not in ("", "NA") else None
            row["observations"] = int(row["observations"])
            row["covered"] = {"true": True, "false": False}.get(row["covered"].casefold())
            rows.append(row)
    return rows, "Calculated with R"


def build(csv_path, output, rscript, settings):
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".build-", dir=output) as scratch:
        work = Path(scratch)
        demo = csv_path is None
        if demo:
            csv_path = work / "demo-input.csv"
            generate_fixture(csv_path)
        elif settings is None:
            raise ValueError("Real ticket imports require explicit --scenarios assumptions")

        database = work / "service-desk.sqlite"
        validation = load_tickets(csv_path, database)
        if not validation["accepted"]:
            raise ValueError(f"No valid tickets imported: {validation['issues']}")

        weekly = query_rows(database, "weekly")
        tickets = query_rows(database, "tickets")
        forecasts = forecast(database)
        scenarios = scenario_plans(weekly, forecasts["predictions"], settings)
        staffing = [
            row | {
                "scenario": plan["name"],
                "predicted_tickets": row["predicted"],
                "scheduled_hours": row["staffed_hours"],
            }
            for plan in scenarios for row in plan["rows"]
        ]
        write_csv(work / "tickets.csv", TICKET_COLUMNS, tickets)
        write_csv(work / "weekly.csv", WEEKLY_COLUMNS, weekly)
        write_csv(work / "staffing.csv", STAFFING_COLUMNS, staffing)
        sensitivity, sensitivity_status = run_sensitivity(work, staffing, weekly, rscript)
        if not (work / "sensitivity.csv").exists():
            write_csv(work / "sensitivity.csv", SENSITIVITY_COLUMNS, [])

        with closing(sqlite3.connect(database)) as connection, connection:
            publish_reporting_tables(connection, weekly, staffing, sensitivity)
        report = {
            "weekly": weekly,
            "forecast": forecasts,
            "scenarios": scenarios,
            "sensitivity": sensitivity,
            "sensitivity_status": sensitivity_status,
            "validation": validation,
            "as_of": max(row["week"] for row in weekly),
            "simulated": demo,
            "exports": {key: f"/exports/{file}" for key, file in EXPORTS.items()},
        }
        (work / "report.json").write_text(
            json.dumps(report, allow_nan=False, indent=2) + "\n", encoding="utf-8"
        )
        files = [path for path in work.iterdir() if path.name != "report.json"]
        for path in [*files, work / "report.json"]:
            os.replace(path, output / path.name)
        if not demo:
            (output / "demo-input.csv").unlink(missing_ok=True)
        return report


def make_handler(output):
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            if request.path == "/tickets.json":
                self.send_tickets(parse_qs(request.query))
                return
            assets = {
                "/": WEB / "index.html",
                "/styles.css": WEB / "styles.css",
                "/app.js": WEB / "app.js",
                "/report.json": output / "report.json",
            }
            assets.update({f"/exports/{file}": output / file for file in EXPORTS.values()})
            path = assets.get(request.path)
            if path is None:
                self.send_error(404)
                return
            try:
                payload = path.read_bytes()
            except FileNotFoundError:
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_payload(payload, content_type)

        def send_tickets(self, params):
            def param(name, default=None):
                return params.get(name, [default])[0]

            try:
                offset = max(0, int(param("offset", 0)))
                limit = min(100, max(1, int(param("limit", 20))))
                start, end = param("start"), param("end")
                if start is not None:
                    date.fromisoformat(start)
                if end is not None:
                    date.fromisoformat(end)
                if start and end and start > end:
                    raise ValueError("Start must be on or before end")
            except ValueError as error:
                self.send_error(400, str(error))
                return
            rows = query_rows(
                output / "service-desk.sqlite", "tickets",
                queue=param("queue"), start=start, end=end,
            )
            payload = json.dumps({"rows": rows[offset:offset + limit], "total": len(rows)})
            self.send_payload(payload.encode("utf-8"), "application/json")

        def send_payload(self, payload, content_type):
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

    return DashboardHandler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA)
    subcommands = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "demo"):
        run = subcommands.add_parser(command)
        inputs = run.add_mutually_exclusive_group(required=command == "build")
        inputs.add_argument("--csv", type=Path)
        inputs.add_argument("--synthetic", action="store_true")
        run.add_argument("--rscript")
        run.add_argument("--scenarios", type=Path)
        if command == "demo":
            run.add_argument("--port", type=int, default=8765)
    serve = subcommands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    try:
        if args.command != "serve":
            settings = None
            if args.scenarios:
                settings = json.loads(args.scenarios.read_text(encoding="utf-8"))
                if not isinstance(settings, list) or not settings:
                    raise ValueError("Scenarios must be a nonempty JSON list")
            report = build(args.csv, args.output, args.rscript, settings)
            counts = report["validation"]
            print(f"Imported {counts['accepted']:,}; rejected {counts['rejected']:,}. ", end="")
            print(f"Report: {args.output / 'report.json'}", flush=True)
        if args.command != "build":
            if not (args.output / "report.json").is_file():
                raise ValueError("Generate a report before starting the dashboard")
            with ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.output)) as http:
                print(f"Dashboard: http://127.0.0.1:{http.server_port}", flush=True)
                http.serve_forever()
    except KeyboardInterrupt:
        return
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, csv.Error, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
