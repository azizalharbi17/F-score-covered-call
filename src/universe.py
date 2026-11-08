"""7-stage universe filtering and screening funnel.

1. Universe ingested        5. Bottom 20% P/B within sector
2. US exchange              6. F-Score ≥ 8
3. Cap & sector screen      7. Actionable covered call
4. Liquidity
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from src import config
from src.earnings_tracker import build_earnings_calendar, classify_earnings_risk, days_until
from src.options_engine import CoveredCallSetup, no_setup_reason, select_covered_call
from src.piotroski import FScoreResult, score_statements
from src.sec_edgar import latest_periodic_filing

STAGES = [
    "Universe Ingested",
    "US Exchange",
    "Cap & Sector Screen",
    "Liquidity",
    "Bottom 20% P/B",
    "F-Score ≥ 8",
    "Actionable Covered Calls",
]


@dataclass
class Signal:
    profile: dict
    fscore: FScoreResult
    setup: CoveredCallSetup
    filings: dict | None = None

    @property
    def ticker(self) -> str:
        return self.profile["ticker"]


@dataclass
class ScreenResult:
    funnel: dict[str, int]
    signals: list[Signal]
    fscores: dict[str, FScoreResult] = field(default_factory=dict)
    earnings: pd.DataFrame = field(default_factory=pd.DataFrame)
    run_date: date = field(default_factory=date.today)
    candidates: list[dict] = field(default_factory=list)      # profiles that reached the F-Score step
    option_notes: dict[str, str] = field(default_factory=dict)  # F-Score ≥ 8 names with no call: why


def passes_cap_and_sector(p: dict) -> bool:
    cap = p.get("market_cap")
    return (cap is not None and config.MID_CAP_MIN <= cap <= config.LARGE_CAP_MAX
            and p.get("sector") not in (None, *config.EXCLUDED_SECTORS))


def passes_liquidity(p: dict) -> bool:
    return (p.get("avg_volume") or 0) >= config.MIN_AVG_DAILY_VOLUME and p.get("price", 0) >= config.MIN_PRICE


def bottom_pb_by_sector(profiles: list[dict], quantile: float = config.PB_BOTTOM_QUANTILE) -> list[dict]:
    """Keep stocks whose P/B is at or below their sector's bottom-quantile cutoff.

    Negative or missing P/B (negative book value) is excluded.
    """
    df = pd.DataFrame(profiles)
    if df.empty:
        return []
    df = df[pd.to_numeric(df["price_to_book"], errors="coerce") > 0].copy()
    df["price_to_book"] = df["price_to_book"].astype(float)
    cutoff = df.groupby("sector")["price_to_book"].transform(lambda s: s.quantile(quantile))
    keep = set(df.loc[df["price_to_book"] <= cutoff, "ticker"])
    return [p for p in profiles if p["ticker"] in keep]


def cap_bucket(market_cap: float) -> str:
    return "Mid-Cap" if market_cap < config.MID_CAP_MAX else "Large-Cap"


def run_screen(tickers: list[str], provider, sec_client=None, today: date | None = None,
               max_workers: int = 8) -> ScreenResult:
    today = today or date.today()
    funnel: dict[str, int] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        profiles = [p for p in pool.map(provider.get_profile, tickers) if p]
    funnel[STAGES[0]] = len(profiles)

    stage = [p for p in profiles if p.get("exchange") in config.ALLOWED_EXCHANGES]
    funnel[STAGES[1]] = len(stage)

    stage = [p for p in stage if passes_cap_and_sector(p)]
    funnel[STAGES[2]] = len(stage)

    liquid = [p for p in stage if passes_liquidity(p)]
    funnel[STAGES[3]] = len(liquid)

    cheap = bottom_pb_by_sector(liquid)
    funnel[STAGES[4]] = len(cheap)

    def score(p):
        statements = provider.get_financials(p["ticker"])
        return p["ticker"], (score_statements(statements) if statements else None)

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        fscores = {t: r for t, r in pool.map(score, cheap) if r is not None}
    strong = [p for p in cheap if p["ticker"] in fscores and fscores[p["ticker"]].score >= config.MIN_FSCORE]
    funnel[STAGES[5]] = len(strong)

    def option_setup(p):
        exps = provider.get_option_expirations(p["ticker"])
        setup = select_covered_call(p["ticker"], p["price"], exps,
                                    lambda e: provider.get_call_chain(p["ticker"], e), today)
        return setup, (None if setup else no_setup_reason(exps, today))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        outcomes = list(pool.map(option_setup, strong))
    signals = []
    option_notes = {}
    for p, (setup, reason) in zip(strong, outcomes):
        if setup is None:
            option_notes[p["ticker"]] = reason
            continue
        filings = sec_client.get_latest_filings(p["ticker"]) if sec_client else None
        signals.append(Signal(p, fscores[p["ticker"]], setup, filings))
    funnel[STAGES[6]] = len(signals)

    earnings = build_earnings_calendar(liquid, today)
    scored = [p for p in cheap if p["ticker"] in fscores]
    return ScreenResult(funnel, signals, fscores, earnings, today, scored, option_notes)


def signals_table(result: ScreenResult) -> pd.DataFrame:
    """Master screener table (Section B columns) plus export-only option details."""
    rows = []
    for s in result.signals:
        p, c = s.profile, s.setup
        filing = latest_periodic_filing(s.filings)
        rows.append({
            "Ticker": s.ticker,
            "Sector": p["sector"],
            "Share Price": round(p["price"], 2),
            "P/B Ratio": round(p["price_to_book"], 2),
            "F-Score": s.fscore.score,
            "Freshness (10-Q Date)": f"{filing['filing_date']} ({filing['form']})" if filing else "n/a",
            "Stock Action": f"BUY 100 @ ${p['price']:.2f}",
            "Target Call Setup (Strike / DTE)": f"SELL ${c.strike:g} Call / {c.dte} DTE ({c.expiration})",
            "Est. Monthly Yield": f"{c.monthly_yield:.2%}",
            "Options Liquidity": f"Vol {c.volume:,} / OI {c.open_interest:,}",
            # Additional option parameters (included in CSV export)
            "Market Cap Bucket": cap_bucket(p["market_cap"]),
            "Expiration": c.expiration,
            "Strike": c.strike,
            "DTE": c.dte,
            "Delta": round(c.delta, 3),
            "Implied Vol": round(c.implied_vol, 4),
            "Bid": c.bid,
            "Ask": c.ask,
            "Premium (Mid)": c.premium,
            "Breakeven": round(c.breakeven, 2),
            "Upside to Strike": f"{c.upside_room:.2%}",
            "Max Return if Assigned": f"{c.max_return_if_assigned:.2%}",
            "Earnings Risk": classify_earnings_risk(days_until(p.get("earnings_date"), result.run_date)),
        })
    return pd.DataFrame(rows)


DISPLAY_COLUMNS = [
    "Ticker", "Sector", "Share Price", "P/B Ratio", "F-Score", "Freshness (10-Q Date)",
    "Stock Action", "Target Call Setup (Strike / DTE)", "Est. Monthly Yield", "Options Liquidity",
]
