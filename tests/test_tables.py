"""Tests for the counts that the article cites (scripts/make_tables.py)."""

import unittest

from scripts.make_tables import counts_section, utility_failures


def ep(suite, task, injection="injection_task_0", won=False, utility=True,
       model="qwen3-32b", valid=True):
    return {"model": model, "suite": suite, "task": task, "injection": injection,
            "attack": "direct", "rep": 1, "won": won, "utility": utility, "valid": valid}


class UtilityFailuresTest(unittest.TestCase):
    def test_top_three_are_the_tasks_with_most_failures(self):
        eps = ([ep("banking", "user_task_0", utility=False)] * 3
               + [ep("travel", "user_task_1", utility=False)] * 2
               + [ep("workspace", "user_task_2", utility=False)]
               + [ep("slack", "user_task_0")] * 5)
        tasks, fails, top = utility_failures(eps)
        self.assertEqual(len(tasks), 4)
        self.assertEqual(fails[("slack", "user_task_0")], 0)
        self.assertEqual(top, [("banking", "user_task_0"), ("travel", "user_task_1"),
                               ("workspace", "user_task_2")])


class CountsSectionTest(unittest.TestCase):
    def test_counts_and_invalid_episodes_left_out(self):
        eps = [
            ep("banking", "user_task_0", won=True, utility=True),
            ep("banking", "user_task_0", won=True, utility=False),
            ep("travel", "user_task_0", injection="injection_task_6", won=True, utility=True),
            ep("travel", "user_task_0", won=False, utility=False),
            ep("travel", "user_task_0", won=True, utility=True, valid=False),
        ]
        text = "\n".join(counts_section(eps, ["qwen3-32b"]))
        # 4 valid episodes, 3 wins, 2 of them with the task completed, 1 from the hotel injection.
        self.assertIn("| 4 | 3 | 2 | 1 |", text)
        self.assertIn("| `banking` | `user_task_0` | 1 of 2 |", text)
        self.assertIn("over the 2 tasks of the grid", text)


if __name__ == "__main__":
    unittest.main()
