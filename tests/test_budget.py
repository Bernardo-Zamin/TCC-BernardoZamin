"""The spending cap with two processes on the same file."""

import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path

from harness.budget import Budget, BudgetExceeded, LedgerCorrupted


def _spend(path, times, value):
    """Runs in another process: its own instance, the same file."""
    b = Budget(path, cap_usd=1000.0)
    for _ in range(times):
        b.record(value, 10, 5)


class SumAcrossProcesses(unittest.TestCase):
    def test_two_processes_lose_no_spend(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spend.json"
            times, value = 60, 0.01
            ps = [multiprocessing.Process(target=_spend, args=(str(path), times, value))
                  for _ in range(2)]
            for p in ps:
                p.start()
            for p in ps:
                p.join(timeout=60)

            led = json.loads(path.read_text())
            self.assertEqual(led["calls"], 2 * times)
            self.assertAlmostEqual(led["spent_usd"], 2 * times * value, places=6)
            self.assertEqual(led["prompt_tokens"], 2 * times * 10)

    def test_no_temp_file_left(self):
        with tempfile.TemporaryDirectory() as d:
            _spend(str(Path(d) / "spend.json"), 5, 0.01)
            self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])


class CheckReadsTheFile(unittest.TestCase):
    def test_sees_the_spend_of_another_process(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spend.json"
            a = Budget(path, cap_usd=1.00)
            a.check(0.10)
            p = multiprocessing.Process(target=_spend, args=(str(path), 95, 0.01))
            p.start()
            p.join(timeout=60)
            with self.assertRaises(BudgetExceeded):
                a.check(0.10)

    def test_cap_stops_before_spending(self):
        with tempfile.TemporaryDirectory() as d:
            b = Budget(Path(d) / "spend.json", cap_usd=0.05)
            b.record(0.04, 10, 5)
            with self.assertRaises(BudgetExceeded):
                b.check(0.02)


class CorruptedLedger(unittest.TestCase):
    def test_stops_instead_of_resetting(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "spend.json"
            path.write_text('{"spent_usd": 1.7, "calls":')  # truncated
            with self.assertRaises(LedgerCorrupted):
                Budget(path, cap_usd=5.0)


if __name__ == "__main__":
    unittest.main()
