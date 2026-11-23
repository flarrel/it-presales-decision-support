"""Cross-check the fixed reconciliation controls and exact SLA boundary."""

import csv
from datetime import datetime, timedelta
from pathlib import Path
import unittest

FIXTURE = Path(__file__).resolve().parent.parent / "excel" / "fixture"


def read(name):
    with (FIXTURE / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


class ReconciliationFixtureTest(unittest.TestCase):
    def test_detail_controls_reconcile(self):
        controls = {
            (row["queue"], row["week"]): [
                int(row[name]) for name in (
                    "tickets", "resolved_tickets", "sla_breaches", "total_handling_minutes",
                )
            ]
            for row in read("weekly.csv")
        }
        actual = {}
        flags = {}
        for row in read("tickets.csv"):
            opened = datetime.fromisoformat(row["opened_at"])
            monday = (opened.date() - timedelta(days=opened.weekday())).isoformat()
            self.assertEqual(monday, row["week"])
            count = actual.setdefault((row["queue"], monday), [0, 0, 0, 0])
            count[0] += 1
            if row["resolved_at"]:
                resolved = datetime.fromisoformat(row["resolved_at"])
                breach = int(resolved - opened > timedelta(minutes=int(row["sla_minutes"])))
                self.assertEqual(breach, int(row["sla_breach"]))
                flags[row["ticket_id"]] = breach
                count[1] += 1
                count[2] += breach
                count[3] += int(row["handling_minutes"])
        self.assertEqual(actual, controls)
        self.assertEqual(flags["EX-004"], 0)
        self.assertEqual(flags["EX-005"], 1)


if __name__ == "__main__":
    unittest.main()
