"""Unit tests for post-filing price moves."""

import unittest
from datetime import date

import pandas as pd

from src.price_reaction import (better_since, classify_pattern, measure, past_quarters, since_sentence,
                                summarize)


def series(start: str, closes: list[float]) -> pd.Series:
    idx = pd.bdate_range(start, periods=len(closes))
    return pd.Series(closes, index=idx, dtype=float)


class TestMeasure(unittest.TestCase):
    def test_dip_then_up(self):
        # Mon Jan 5 2026 ... filing on Saturday Jan 3 starts at Monday's close
        prices = series("2026-01-05", [100, 95, 90, 96, 104, 108, 106])
        m = measure(prices, "2026-01-03")
        self.assertEqual(m.start_date, date(2026, 1, 5))
        self.assertAlmostEqual(m.dip, -0.10)
        self.assertEqual(m.low_date, date(2026, 1, 7))
        self.assertEqual(m.days_to_dip(date(2026, 1, 3)), 4)
        self.assertAlmostEqual(m.rise, 0.08)
        self.assertAlmostEqual(m.change, 0.06)
        self.assertAlmostEqual(m.rebound, 108 / 90 - 1)
        self.assertEqual(classify_pattern(m), "Dip then up")

    def test_window_stops_before_next_filing(self):
        prices = series("2026-01-05", [100, 102, 104, 50, 50])  # crash on Jan 8 is the next filing day
        m = measure(prices, "2026-01-05", "2026-01-08")
        self.assertEqual(m.end_date, date(2026, 1, 7))
        self.assertAlmostEqual(m.change, 0.04)
        self.assertEqual(m.dip, 0.0)
        self.assertEqual(classify_pattern(m), "Straight up")

    def test_patterns(self):
        self.assertEqual(classify_pattern(measure(series("2026-01-05", [100, 110, 95]), "2026-01-05")),
                         "Up then down")
        self.assertEqual(classify_pattern(measure(series("2026-01-05", [100, 97, 92]), "2026-01-05")),
                         "Straight down")
        self.assertEqual(classify_pattern(measure(series("2026-01-05", [100, 101, 100.5]), "2026-01-05")),
                         "Straight up")

    def test_missing_data(self):
        prices = series("2026-01-05", [100, 101])
        self.assertIsNone(measure(prices, "2025-12-01"))  # history starts after the filing
        self.assertIsNone(measure(None, "2026-01-05"))
        self.assertIsNone(measure(prices, None))


class TestPastQuarters(unittest.TestCase):
    def test_rows_newest_first_with_na(self):
        prices = series("2026-01-05", [100 + i for i in range(60)])
        filings = [
            {"form": "10-Q", "filing_date": "2026-03-02"},
            {"form": "10-K", "filing_date": "2026-02-02"},
            {"form": "10-Q", "filing_date": "2025-11-03"},  # before price history -> n/a
            {"form": "10-Q", "filing_date": "2025-08-04"},
        ]
        rows = past_quarters(prices, filings, count=8)
        self.assertEqual([r["filing_date"] for r in rows],
                         [date(2026, 2, 2), date(2025, 11, 3), date(2025, 8, 4)])  # latest has no next filing
        self.assertEqual(rows[0]["form"], "10-K")
        self.assertIsNotNone(rows[0]["move"])
        self.assertIsNone(rows[1]["move"])
        self.assertIsNone(rows[1]["pattern"])
        self.assertEqual(len(past_quarters(prices, filings, count=2)), 2)

    def test_summary(self):
        dip_up = measure(series("2026-01-05", [100, 94, 110]), "2026-01-05")
        down = measure(series("2026-01-05", [100, 99, 90]), "2026-01-05")
        rows = [{"move": dip_up, "pattern": "Dip then up"}, {"move": down, "pattern": "Straight down"},
                {"move": None, "pattern": None}]
        text = summarize(rows)
        self.assertIn("In 1 of the last 2 quarters it dipped first, by 6% on average", text)
        self.assertIn("rose 17% on average from that low", text)
        self.assertIn("ended higher by the next report in 1 of 2", text)
        self.assertEqual(summarize([{"move": None, "pattern": None}]),
                         "Not enough price data to summarize past quarters.")


class TestSentences(unittest.TestCase):
    def test_since_sentence(self):
        m = measure(series("2026-02-02", [100, 92, 95, 103]), "2026-02-02")
        text = since_sentence("10-K", "2026-02-02", m)
        self.assertEqual(text, "Since the 10-K (Feb 2, 2026): up 3.0%. It first dropped 8.0% "
                               "(lowest on Feb 3, 2026), then rose 12.0% from that low.")
        flat = measure(series("2026-02-02", [100, 101, 102]), "2026-02-02")
        self.assertIn("never closed below", since_sentence("10-Q", "2026-02-02", flat))
        self.assertIn("n/a", since_sentence("10-Q", "2026-02-02", None))

    def test_better_since(self):
        self.assertEqual(better_since(0.05, -0.02), "10-K")
        self.assertEqual(better_since(-0.05, 0.01), "10-Q")
        self.assertEqual(better_since(-0.05, -0.01), "Neither (down since both)")
        self.assertEqual(better_since(None, None), "n/a")


if __name__ == "__main__":
    unittest.main()
