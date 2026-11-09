"""Unit tests for scoring, options delta, and earnings risk."""

import unittest
from datetime import date

import pandas as pd

from src.earnings_tracker import (CLEAR, HIGH, MEDIUM, UNKNOWN, build_earnings_calendar, classify_earnings_risk,
                                  last_and_next, past_earnings, results_announced_before)
from src.options_engine import bs_call_delta, no_setup_reason, payoff_curve, select_covered_call
from src.paper_trader import mark_to_market, simulate_closeout
from src.piotroski import compute_fscore, score_quarterly_statements, score_statements, year_ago_quarters
from src.universe import bottom_pb_by_sector


def items(**overrides):
    base = dict(net_income=100.0, revenue=1000.0, gross_profit=400.0, cost_of_revenue=600.0,
                total_assets=2000.0, current_assets=600.0, current_liabilities=400.0,
                long_term_debt=500.0, shares_outstanding=100.0, cfo=150.0)
    base.update(overrides)
    return base


def strong_pair():
    """Current period improves on every test vs prior."""
    current = items(net_income=120.0, revenue=1100.0, gross_profit=462.0, current_assets=660.0,
                    long_term_debt=450.0, shares_outstanding=98.0, cfo=180.0)
    prior = items()
    return current, prior


class TestPiotroski(unittest.TestCase):
    def test_perfect_score(self):
        result = compute_fscore(*strong_pair())
        self.assertEqual(result.score, 9)
        self.assertTrue(all(ch.passed for ch in result.checks))
        self.assertEqual([ch.number for ch in result.checks], list(range(1, 10)))

    def test_point2_is_delta_roa_not_level(self):
        # Positive ROA that fell vs prior must FAIL point 2 ...
        current = items(net_income=80.0)          # ROA 4.0%
        prior = items(net_income=100.0)           # ROA 5.0%
        check = compute_fscore(current, prior).checks[1]
        self.assertEqual(check.number, 2)
        self.assertTrue(check.current > 0)
        self.assertFalse(check.passed)
        # ... while a negative ROA that improved must PASS it.
        current = items(net_income=-20.0)         # ROA -1.0%
        prior = items(net_income=-60.0)           # ROA -3.0%
        result = compute_fscore(current, prior)
        self.assertTrue(result.checks[1].passed)
        self.assertFalse(result.checks[0].passed)  # point 1 still Net Income > 0

    def test_accrual_check(self):
        result = compute_fscore(items(cfo=90.0), items())
        self.assertFalse(result.checks[3].passed)
        result = compute_fscore(items(cfo=110.0), items())
        self.assertTrue(result.checks[3].passed)

    def test_leverage_and_dilution(self):
        result = compute_fscore(items(long_term_debt=500.0, shares_outstanding=100.0), items())
        self.assertTrue(result.checks[4].passed)   # unchanged leverage counts (≤ 0)
        self.assertTrue(result.checks[6].passed)   # unchanged share count counts (≤)
        result = compute_fscore(items(long_term_debt=600.0, shares_outstanding=105.0), items())
        self.assertFalse(result.checks[4].passed)
        self.assertFalse(result.checks[6].passed)

    def test_missing_data_scores_zero(self):
        current, prior = strong_pair()
        current["cfo"] = None
        result = compute_fscore(current, prior)
        self.assertEqual(result.score, 7)          # points 3 and 4 need CFO
        self.assertIn("Operating Cash Flow > 0", result.missing)

    def test_score_statements_from_yfinance_layout(self):
        current, prior = strong_pair()
        cols = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")]

        def frame(mapping):
            return pd.DataFrame({cols[0]: [current[k] for k in mapping.values()],
                                 cols[1]: [prior[k] for k in mapping.values()]}, index=list(mapping))

        statements = {
            "income": frame({"Net Income": "net_income", "Total Revenue": "revenue",
                             "Cost Of Revenue": "cost_of_revenue"}),  # gross profit derived
            "balance": frame({"Total Assets": "total_assets", "Current Assets": "current_assets",
                              "Current Liabilities": "current_liabilities", "Long Term Debt": "long_term_debt",
                              "Ordinary Shares Number": "shares_outstanding"}),
            "cashflow": frame({"Operating Cash Flow": "cfo"}),
        }
        statements["income"].loc["Cost Of Revenue"] = [1100.0 - 462.0, 600.0]
        result = score_statements(statements)
        self.assertEqual(result.score, 9)
        self.assertEqual(result.period_current, "2025-12-31")


    def test_quarterly_uses_same_quarter_a_year_earlier(self):
        q = [pd.Timestamp(d) for d in ("2026-09-30", "2026-06-30", "2026-03-31", "2025-12-31",
                                        "2025-09-30", "2025-06-30")]
        current, prior = strong_pair()
        weak = items(net_income=-50.0)  # quarters in between should be ignored

        def frame(mapping):
            data = {}
            for c in q:
                src = current if c == q[0] else prior if c == q[4] else weak
                data[c] = [src[k] for k in mapping.values()]
            df = pd.DataFrame(data, index=list(mapping))
            df[q[5]] = float("nan")  # Yahoo leaves older quarters empty
            return df

        statements = {
            "income": frame({"Net Income": "net_income", "Total Revenue": "revenue", "Gross Profit": "gross_profit"}),
            "balance": frame({"Total Assets": "total_assets", "Current Assets": "current_assets",
                              "Current Liabilities": "current_liabilities", "Long Term Debt": "long_term_debt",
                              "Ordinary Shares Number": "shares_outstanding"}),
            "cashflow": frame({"Operating Cash Flow": "cfo"}),
        }
        self.assertEqual(year_ago_quarters(statements), (q[0], q[4]))
        result = score_quarterly_statements(statements)
        self.assertEqual(result.score, 9)
        self.assertEqual((result.period_current, result.period_prior), ("2026-09-30", "2025-09-30"))

    def test_quarterly_without_year_ago_quarter(self):
        cols = [pd.Timestamp("2026-09-30"), pd.Timestamp("2026-06-30")]
        df = pd.DataFrame({c: [1.0] for c in cols}, index=["Total Assets"])
        self.assertIsNone(score_quarterly_statements({"income": df, "balance": df, "cashflow": df}))


class TestOptions(unittest.TestCase):
    def test_bs_delta_known_value(self):
        # ATM, 1 year, r=5%, vol=20% -> d1 = 0.35, N(d1) ≈ 0.6368
        self.assertAlmostEqual(bs_call_delta(100, 100, 1.0, 0.05, 0.20), 0.6368, places=3)

    def test_bs_delta_monotonic_in_strike(self):
        deltas = [bs_call_delta(100, k, 35 / 365, 0.04, 0.3) for k in (95, 100, 105, 110, 120)]
        self.assertEqual(deltas, sorted(deltas, reverse=True))
        self.assertTrue(all(0 < d < 1 for d in deltas))

    def _chain(self):
        return pd.DataFrame({
            "strike": [95, 102, 105, 108, 110, 115],
            "bid": [6.0, 2.4, 1.6, 1.0, 0.8, 0.3],
            "ask": [6.4, 2.6, 1.8, 1.2, 0.9, 0.4],
            "volume": [500, 300, 400, 50, 250, 400],
            "openInterest": [2000, 1500, 1800, 3000, 400, 900],
            "impliedVolatility": [0.30] * 6,
        })

    def test_select_covered_call_filters(self):
        today = date(2026, 1, 1)
        exps = ["2026-01-16", "2026-02-06", "2026-03-20"]  # 15, 36, 78 DTE
        calls = []
        setup = select_covered_call("TEST", 100.0, exps, lambda e: calls.append(e) or self._chain(), today)
        self.assertEqual(calls, ["2026-02-06"])              # only the 30-45 DTE expiry is queried
        self.assertIsNotNone(setup)
        self.assertGreater(setup.strike, 100.0)              # OTM only
        self.assertNotIn(setup.strike, (108, 110))           # volume / OI filters
        self.assertGreater(setup.volume, 100)
        self.assertGreater(setup.open_interest, 500)
        self.assertEqual(setup.strike, 105)                  # delta closest to 0.30
        self.assertAlmostEqual(setup.premium, 1.7)
        self.assertAlmostEqual(setup.monthly_yield, 1.7 / 100 * 30 / 36)

    def test_no_eligible_expiration(self):
        self.assertIsNone(select_covered_call("T", 100.0, ["2026-01-10"], lambda e: self._chain(),
                                              date(2026, 1, 1)))

    def test_payoff_curve_caps_upside(self):
        curve = payoff_curve(100.0, 105.0, 2.0)
        self.assertAlmostEqual(curve["covered_call"].max(), (105 - 100 + 2) * 100)
        low = curve.iloc[0]
        self.assertAlmostEqual(low["covered_call"] - low["stock_only"], 200.0)

    def test_no_setup_reason(self):
        today = date(2026, 1, 1)
        self.assertEqual(no_setup_reason([], today), "no options listed")
        self.assertEqual(no_setup_reason(["2026-01-16"], today), "no option expiring in 30–45 days")
        self.assertEqual(no_setup_reason(["2026-02-06"], today), "options not traded enough")

    def test_mark_to_market(self):
        pos = {"entry_price": 100.0, "strike": 105.0, "premium": 2.0, "shares": 100}
        m = mark_to_market(pos, 103.0, call_price=1.5)
        self.assertEqual(m["cost"], 9800.0)
        self.assertEqual(m["value"], 10150.0)
        self.assertAlmostEqual(m["pnl_pct"], 350.0 / 9800.0)
        self.assertEqual(mark_to_market(pos, 110.0)["value"], 10500.0)  # call at intrinsic value

    def test_closeout(self):
        pos = {"entry_price": 100.0, "strike": 105.0, "premium": 2.0, "shares": 100}
        self.assertEqual(simulate_closeout(pos, 110.0)["pnl"], 700.0)
        out = simulate_closeout(pos, 95.0)
        self.assertFalse(out["assigned"])
        self.assertEqual(out["pnl"], -300.0)


class TestEarnings(unittest.TestCase):
    def test_risk_bands(self):
        self.assertEqual(classify_earnings_risk(0), HIGH)
        self.assertEqual(classify_earnings_risk(7), HIGH)
        self.assertEqual(classify_earnings_risk(8), MEDIUM)
        self.assertEqual(classify_earnings_risk(14), MEDIUM)
        self.assertEqual(classify_earnings_risk(15), CLEAR)
        self.assertEqual(classify_earnings_risk(None), UNKNOWN)

    def test_calendar_sorted_and_stale_dates_dropped(self):
        today = date(2026, 1, 1)
        cal = build_earnings_calendar([
            {"ticker": "A", "earnings_date": date(2026, 1, 20)},
            {"ticker": "B", "earnings_date": date(2026, 1, 5)},
            {"ticker": "C", "earnings_date": date(2025, 12, 1)},
            {"ticker": "D", "earnings_date": None},
        ], today)
        self.assertEqual(list(cal["Ticker"][:2]), ["B", "A"])
        self.assertEqual(cal.set_index("Ticker").loc["B", "Event Risk"], HIGH)
        self.assertEqual(cal.set_index("Ticker").loc["C", "Event Risk"], UNKNOWN)


    def test_last_and_next_earnings(self):
        dates = [date(2026, 2, 2), date(2026, 5, 5), date(2026, 11, 2), date(2027, 2, 1)]
        self.assertEqual(last_and_next(dates, date(2026, 11, 8)), (date(2026, 11, 2), date(2027, 2, 1)))
        self.assertEqual(last_and_next(dates, date(2026, 11, 2)), (date(2026, 11, 2), date(2027, 2, 1)))
        self.assertEqual(last_and_next([], date(2026, 11, 8)), (None, None))

    def test_past_earnings_quarters_back(self):
        dates = [date(2026, 2, 2), date(2026, 5, 5), date(2026, 8, 4), date(2026, 11, 2), date(2027, 2, 1)]
        today = date(2026, 11, 8)
        self.assertEqual(past_earnings(dates, today), date(2026, 11, 2))
        self.assertEqual(past_earnings(dates, today, 2), date(2026, 5, 5))
        self.assertIsNone(past_earnings(dates[:2], today, 2))

    def test_results_announced_before_filing(self):
        dates = [date(2025, 10, 30), date(2026, 2, 2), date(2026, 5, 5)]
        self.assertEqual(results_announced_before(dates, date(2026, 2, 6)), date(2026, 2, 2))
        self.assertIsNone(results_announced_before(dates, date(2026, 9, 30)))


class TestPriceToBook(unittest.TestCase):
    def test_bottom_quintile_per_sector(self):
        profiles = [{"ticker": f"T{i}", "sector": "Tech", "price_to_book": float(i)} for i in range(1, 11)]
        profiles += [{"ticker": f"E{i}", "sector": "Energy", "price_to_book": float(i * 10)} for i in range(1, 6)]
        profiles += [{"ticker": "NEG", "sector": "Tech", "price_to_book": -3.0}]
        kept = {p["ticker"] for p in bottom_pb_by_sector(profiles)}
        self.assertEqual(kept, {"T1", "T2", "E1"})


if __name__ == "__main__":
    unittest.main()
