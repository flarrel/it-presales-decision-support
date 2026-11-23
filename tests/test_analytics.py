import csv
import itertools
import tempfile
import unittest
from datetime import date
from pathlib import Path

from presales.analytics import TICKET_FIELDS, forecast, generate_fixture, load_tickets, query_rows, recommend


class TicketPipelineTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(directory.cleanup)
        self.source = Path(directory.name) / "tickets.csv"
        self.database = Path(directory.name) / "tickets.sqlite"

    def test_rejects_invalid_rows_and_reconciles_sql_filters(self):
        first = ("1", "2025-01-06T01:00:00+01:00", "2025-01-06T01:01:00.000001+01:00", "Cloud", "High", 1, 1)
        on_target = ("2", "2025-01-06T12:00:00", "2025-01-06T13:00:00", "Cloud", "normal", 60, 11)
        still_open = ("3", "2025-01-12T23:59:59.999999", "", "Cloud", "normal", 30, "")
        other_queue = ("4", "2025-01-06T00:00:00", "", "Network", "normal", 10, "")
        with self.source.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(TICKET_FIELDS)
            writer.writerows((first, on_target, still_open, other_queue, first))
            writer.writerow(("5", "2025-01-06T11:00:00", "", "", "normal", 30, ""))
        self.assertEqual(load_tickets(self.source, self.database), {
            "accepted": 4, "rejected": 2, "issues": {"duplicate_ticket_id": 1, "missing_queue": 1},
        })
        weekly = query_rows(self.database, "weekly", queue="Cloud", start="2025-01-06", end="2025-01-06")
        self.assertEqual(weekly, [{
            "queue": "Cloud", "week": "2025-01-06", "tickets": 3, "resolved_tickets": 2,
            "open_tickets": 1, "sla_breaches": 1, "total_handling_minutes": 12,
        }])
        tickets = query_rows(self.database, "tickets", queue="Cloud", end="2025-01-06")
        self.assertEqual([(row["ticket_id"], row["sla_breached"]) for row in tickets], [("1", 1), ("2", 0)])
        self.source.write_text("bad,header\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Ticket CSV must contain"):
            load_tickets(self.source, self.database)
        self.assertEqual(len(query_rows(self.database, "tickets")), 4)

    def test_fixture_backtest_is_chronological_and_has_an_equal_score_set(self):
        written = generate_fixture(self.source)
        quality = load_tickets(self.source, self.database)
        self.assertEqual(written, quality["accepted"] + quality["rejected"])
        self.assertEqual(quality["rejected"], 4)
        result = forecast(self.database)
        self.assertEqual(len(result["predictions"]), 12)
        for row in result["backtest"]:
            lead = date.fromisoformat(row["week"]) - date.fromisoformat(row["origin_week"])
            self.assertEqual(lead.days, row["horizon"] * 7)
        self.assertTrue(all(row["n"] == 48 for row in result["metrics"]))
        for queue in {row["queue"] for row in result["metrics"]}:
            metrics = [row for row in result["metrics"] if row["queue"] == queue]
            self.assertEqual(sum(row["selected"] for row in metrics), 1)
            for row in metrics:
                holdout = [item for item in result["backtest"] if item["queue"] == queue]
                self.assertAlmostEqual(row["mae"], sum(abs(item["actual"] - item[row["model"]]) for item in holdout) / len(holdout))


class StaffingTest(unittest.TestCase):
    def setUp(self):
        week = "2025-01-06"
        self.predictions = [{"queue": queue, "week": week, "predicted": hours} for queue, hours in
                            (("Cloud", 5.6), ("Network", 3.25), ("Endpoints", 4.8))]
        self.scenario = {
            "name": "Productive staffing", "budget": 40, "hourly_rate": 10, "minimum_coverage": 0.5,
            "baseline_hours": {"Cloud": 2, "Network": 3, "Endpoints": 3},
            "max_extra_hours": {"Cloud": 4, "Network": 4, "Endpoints": 3},
            "minutes_per_ticket": {"Cloud": 60, "Network": 60, "Endpoints": 60},
        }

    def test_matches_small_exhaustive_search(self):
        result = recommend(self.predictions, self.scenario)
        self.assertTrue(result["feasible"])
        self.assertLessEqual(result["extra_cost"], self.scenario["budget"])
        self.assertTrue(all(row["coverage"] >= 0.5 for row in result["rows"]))
        alternatives = []
        for hours in itertools.product(*(range(row["max_extra_hours"] + 1) for row in result["rows"])):
            cost = sum(hours) * self.scenario["hourly_rate"]
            staffed = [row["baseline_hours"] + extra for row, extra in zip(result["rows"], hours)]
            required = [row["workload_hours"] for row in result["rows"]]
            if cost <= self.scenario["budget"] and all(have >= 0.5 * need for have, need in zip(staffed, required)):
                alternatives.append((sum(max(0, need - have) for have, need in zip(staffed, required)), cost))
        expected_unmet, expected_cost = min(alternatives)
        self.assertAlmostEqual(result["recommended_unmet_hours"], expected_unmet)
        self.assertEqual(result["extra_cost"], expected_cost)

    def test_infeasible_budget_leaves_baseline(self):
        result = recommend(self.predictions, self.scenario | {"budget": 0, "minimum_coverage": 0.9})
        self.assertFalse(result["feasible"])
        self.assertTrue(result["reasons"])
        self.assertIsNone(result["recommended_unmet_hours"])
        self.assertEqual(result["extra_hours"], 0)


if __name__ == "__main__":
    unittest.main()
