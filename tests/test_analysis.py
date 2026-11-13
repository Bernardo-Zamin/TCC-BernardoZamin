"""Statistics across repetitions, criteria and banking audit."""

import unittest

from harness.analysis import (CRITERIA, INJECTION_CRITERION, JUDGED_BY_TEXT, banking_audit,
                              mean_sd, rate)


def eps(by_rep, field="won"):
    """Synthetic episodes: {rep: [True, False, ...]}."""
    out = []
    for rep, wins in by_rep.items():
        for w in wins:
            out.append({"rep": rep, field: w, "valid": True})
    return out


class InvalidIsNotMissing(unittest.TestCase):
    def test_invalid_completes_the_repetition_but_not_the_rate(self):
        data = eps({1: [True, False, False], 2: [True, True, False],
                    3: [False, False, False]})
        data[0]["valid"] = False  # rep 1 keeps 0/2 valid
        r = mean_sd(data)
        self.assertEqual(r["n_reps"], 3)
        self.assertEqual(r["n_per_rep"], 3)
        self.assertAlmostEqual(r["mean"], (0 + 2 / 3 + 0) / 3)
        self.assertAlmostEqual(r["pooled"], 2 / 8)

    def test_only_valid_episodes(self):
        data = eps({1: [True, False], 2: [True, True]})
        self.assertAlmostEqual(mean_sd(data)["mean"], 0.75)

    def test_fully_invalid_repetition_has_no_rate(self):
        data = eps({1: [True], 2: [False]})
        data[1]["valid"] = False
        r = mean_sd(data)
        self.assertEqual((r["n_reps"], r["mean"], r["sd"]), (1, 1.0, None))


class MeanAndDeviation(unittest.TestCase):
    def test_two_repetitions(self):
        r = mean_sd(eps({1: [True, False], 2: [True, True]}))
        self.assertAlmostEqual(r["mean"], 0.75)
        self.assertAlmostEqual(r["sd"], 0.35355, places=4)
        self.assertEqual(r["n_reps"], 2)
        self.assertEqual(r["n_per_rep"], 2)
        self.assertAlmostEqual(r["min"], 0.5)
        self.assertAlmostEqual(r["max"], 1.0)

    def test_one_repetition_has_no_deviation(self):
        r = mean_sd(eps({1: [True, False, False]}))
        self.assertAlmostEqual(r["mean"], 1 / 3)
        self.assertIsNone(r["sd"])
        self.assertEqual(r["n_reps"], 1)

    def test_ten_equal_repetitions_have_zero_deviation(self):
        r = mean_sd(eps({i: [True, False] for i in range(1, 11)}))
        self.assertAlmostEqual(r["mean"], 0.5)
        self.assertAlmostEqual(r["sd"], 0.0)
        self.assertEqual(r["n_reps"], 10)

    def test_no_episodes(self):
        self.assertIsNone(mean_sd([]))


class IncompleteRepetitionIsLeftOut(unittest.TestCase):
    def test_ignored_and_counted(self):
        data = eps({1: [True, False, False, False],
                    2: [True, True, False, False],
                    3: [True]})
        r = mean_sd(data)
        self.assertEqual(r["n_reps"], 2)
        self.assertEqual(r["n_per_rep"], 4)
        self.assertEqual(r["partial_reps"], 1)
        self.assertAlmostEqual(r["mean"], 0.375)

    def test_explicit_expected_size(self):
        data = eps({1: [True, False], 2: [True], 3: [False]})
        self.assertEqual(mean_sd(data)["n_per_rep"], 1)
        self.assertEqual(mean_sd(data, expected=2)["n_reps"], 1)


class PooledRate(unittest.TestCase):
    def test_matches_the_mean_when_repetitions_are_equal(self):
        data = eps({1: [True, False, False], 2: [True, True, False]})
        r = mean_sd(data)
        self.assertAlmostEqual(r["mean"], r["pooled"])
        self.assertAlmostEqual(r["pooled"], rate(data))

    def test_differs_when_sizes_differ(self):
        data = eps({1: [True], 2: [False, False, False]})
        r = mean_sd(data, expected=1)
        self.assertAlmostEqual(r["mean"], 1.0)
        self.assertAlmostEqual(r["pooled"], 0.25)

    def test_works_for_any_field(self):
        data = [{"rep": 1, "utility": True}, {"rep": 1, "utility": False},
                {"rep": 2, "utility": True}, {"rep": 2, "utility": True}]
        self.assertAlmostEqual(mean_sd(data, field="utility")["mean"], 0.75)


class ImpactCriterion(unittest.TestCase):
    def test_each_injection_has_a_known_criterion(self):
        self.assertEqual(len(INJECTION_CRITERION), 9)
        for criterion, _ in INJECTION_CRITERION.values():
            self.assertIn(criterion, CRITERIA)

    def test_the_text_judged_injection_is_response_manipulation(self):
        text_only = {k for k, (c, _) in INJECTION_CRITERION.items() if c == "response"}
        self.assertEqual(text_only, set(JUDGED_BY_TEXT))


class BankingAudit(unittest.TestCase):
    def ep(self, won, calls, injection="injection_task_0"):
        return {"suite": "banking", "injection": injection, "won": won,
                "actions": [{"ferramenta": f, "argumentos": {
                    "recipient": "US133000000121212121212", "subject": s}}
                    for f, s in calls]}

    def test_separates_wins_without_data_and_blocked_schedules(self):
        a = banking_audit([
            self.ep(True, [("send_money", "Spotify Premium")]),
            self.ep(True, [("send_money", "payment")]),
            self.ep(False, [("schedule_transaction", "Spotify")]),
            self.ep(False, []),
        ])
        self.assertEqual(a, {"wins": 2, "wins_without_data": 1,
                             "scheduled_blocked": 1,
                             "scheduled_blocked_with_data": 1})


if __name__ == "__main__":
    unittest.main()
