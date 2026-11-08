"""Integration test: the full 7-stage pipeline on an offline fake data provider."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from src.options_engine import robinhood_order
from src.paper_trader import PaperPortfolio, simulate_closeout
from src.universe import DISPLAY_COLUMNS, STAGES, run_screen, signals_table

TODAY = date(2026, 1, 1)
COLS = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31")]


def statements(improving: bool):
    cur = dict(ni=120, rev=1100, cogs=638, ta=2000, ca=660, cl=400, ltd=450, sh=98, cfo=180)
    pri = dict(ni=100, rev=1000, cogs=600, ta=2000, ca=600, cl=400, ltd=500, sh=100, cfo=150)
    if not improving:
        cur, pri = pri, cur

    def frame(rows):
        return pd.DataFrame({COLS[0]: [cur[k] for k in rows.values()], COLS[1]: [pri[k] for k in rows.values()]},
                            index=list(rows))

    return {
        "income": frame({"Net Income": "ni", "Total Revenue": "rev", "Cost Of Revenue": "cogs"}),
        "balance": frame({"Total Assets": "ta", "Current Assets": "ca", "Current Liabilities": "cl",
                          "Long Term Debt": "ltd", "Ordinary Shares Number": "sh"}),
        "cashflow": frame({"Operating Cash Flow": "cfo"}),
    }


def profile(ticker, sector, pb, cap=5e9, exchange="NYQ", price=50.0, volume=1_000_000):
    return {"ticker": ticker, "name": ticker, "exchange": exchange, "sector": sector, "market_cap": cap,
            "price": price, "avg_volume": volume, "price_to_book": pb, "earnings_date": date(2026, 1, 6)}


class FakeProvider:
    def __init__(self):
        self.profiles = {p["ticker"]: p for p in [
            # Technology: 5 names, bottom 20% -> CHEAP (also has improving fundamentals)
            profile("CHEAP", "Technology", 1.0),
            *[profile(f"TECH{i}", "Technology", 3.0 + i) for i in range(4)],
            # Industrials: bottom name has deteriorating fundamentals -> dropped at F-Score
            profile("TRAP", "Industrials", 0.8),
            *[profile(f"IND{i}", "Industrials", 2.0 + i) for i in range(4)],
            # Filtered out earlier in the funnel
            profile("OTC", "Technology", 0.5, exchange="PNK"),
            profile("BANK", "Financial Services", 0.5),
            profile("MEGA", "Technology", 0.5, cap=900e9),
            profile("THIN", "Technology", 0.5, volume=10_000),
            profile("PENNY", "Technology", 0.5, price=4.0),
        ]}

    def get_profile(self, ticker):
        return self.profiles.get(ticker)

    def get_financials(self, ticker):
        return statements(improving=ticker != "TRAP")

    def get_option_expirations(self, ticker):
        return ["2026-01-16", "2026-02-06"]

    def get_call_chain(self, ticker, expiration):
        return pd.DataFrame({"strike": [52.5, 55.0, 60.0], "bid": [1.2, 0.6, 0.1], "ask": [1.3, 0.7, 0.2],
                             "volume": [400, 300, 200], "openInterest": [1500, 1200, 900],
                             "impliedVolatility": [0.35, 0.35, 0.35]})


class FakeSec:
    def get_latest_filings(self, ticker):
        return {"cik": 1, "10-Q": {"form": "10-Q", "filing_date": "2025-11-05", "report_date": "2025-09-30",
                                   "url": "https://www.sec.gov/Archives/edgar/data/1/x/q.htm"}}


class TestEndToEnd(unittest.TestCase):
    def test_pipeline(self):
        tickers = list(FakeProvider().profiles) + ["DELISTED"]
        result = run_screen(tickers, FakeProvider(), FakeSec(), today=TODAY, max_workers=2)

        self.assertEqual(list(result.funnel), STAGES)
        self.assertEqual([result.funnel[s] for s in STAGES], [15, 14, 12, 10, 2, 1, 1])
        self.assertEqual([s.ticker for s in result.signals], ["CHEAP"])
        self.assertEqual(result.fscores["TRAP"].score < 8, True)

        sig = result.signals[0]
        self.assertEqual(sig.fscore.score, 9)
        self.assertEqual(sig.setup.expiration, "2026-02-06")
        self.assertGreater(sig.setup.strike, 50.0)

        table = signals_table(result)
        for col in DISPLAY_COLUMNS:
            self.assertIn(col, table.columns)
        self.assertEqual(table.loc[0, "Freshness (10-Q Date)"], "2025-11-05 (10-Q)")
        self.assertEqual(table.loc[0, "Earnings Risk"], "🚨 HIGH")

        self.assertEqual(len(result.earnings), result.funnel["Liquidity"])

        order = robinhood_order(sig.setup)
        self.assertAlmostEqual(order["Limit Price (net debit / share)"], round(50.0 - sig.setup.premium, 2))

        with tempfile.TemporaryDirectory() as tmp:
            book = PaperPortfolio(Path(tmp) / "paper.json")
            pos = book.log_position("CHEAP", sig.setup.spot, sig.setup.strike, sig.setup.premium,
                                    sig.setup.expiration, opened=TODAY)
            self.assertEqual(len(PaperPortfolio(Path(tmp) / "paper.json").positions()), 1)
            self.assertTrue(simulate_closeout(pos, 100.0)["assigned"])


if __name__ == "__main__":
    unittest.main()
