import unittest

from lsrp.stats import distribution, summarize


class StatisticsTests(unittest.TestCase):
    def test_missing_values_do_not_become_zero(self):
        result = distribution([None, 2, 4, float("nan"), True])
        self.assertEqual(result["n"], 2)
        self.assertEqual(result["missing"], 3)
        self.assertEqual(result["median"], 3)
        self.assertIsNone(result["p95"])

    def test_tail_requires_twenty_available_values(self):
        self.assertIsNone(distribution(range(19))["p95"])
        self.assertEqual(distribution(range(1, 21))["p95"], 19)

    def test_failed_and_inconclusive_trials_remain_visible(self):
        records = [{"scenario": "crash", "outcome": outcome,
                    "metrics": {"recovery_time_s": value}}
                   for outcome, value in (("recovered", 2), ("failed", None), ("inconclusive", None))]
        result = summarize(records)["scenarios"]["crash"]
        self.assertEqual(result["n"], 3)
        self.assertEqual(result["outcomes"]["failed"], 1)
        self.assertEqual(result["outcomes"]["inconclusive"], 1)
        self.assertEqual(result["metrics"]["recovery_time_s"]["missing"], 2)


if __name__ == "__main__":
    unittest.main()
