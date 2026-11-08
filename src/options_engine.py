"""Black-Scholes delta, covered call selection, payoff curves and Robinhood order spec."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd

from src import config


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call_delta(spot: float, strike: float, years: float, rate: float, sigma: float) -> float:
    """Black-Scholes delta of a European call (no dividends)."""
    if years <= 0 or sigma <= 0:
        return 1.0 if spot > strike else 0.0
    d1 = (math.log(spot / strike) + (rate + 0.5 * sigma**2) * years) / (sigma * math.sqrt(years))
    return norm_cdf(d1)


@dataclass
class CoveredCallSetup:
    ticker: str
    spot: float
    strike: float
    expiration: str
    dte: int
    delta: float
    implied_vol: float
    bid: float
    ask: float
    premium: float          # per share, limit price at mid
    volume: int
    open_interest: int

    @property
    def monthly_yield(self) -> float:
        """Premium / spot, normalized to a 30-day month."""
        return self.premium / self.spot * (30.0 / self.dte)

    @property
    def breakeven(self) -> float:
        return self.spot - self.premium

    @property
    def upside_room(self) -> float:
        return (self.strike - self.spot) / self.spot

    @property
    def max_return_if_assigned(self) -> float:
        return (self.strike - self.spot + self.premium) / self.spot

    @property
    def prob_otm(self) -> float:
        return 1.0 - self.delta


def _to_date(value) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def select_covered_call(ticker: str, spot: float, expirations: list[str], get_chain,
                        today: date | None = None) -> CoveredCallSetup | None:
    """Pick the liquid OTM call (30-45 DTE) whose delta is closest to the target.

    `get_chain(expiration)` must return a calls DataFrame with yfinance columns:
    strike, bid, ask, lastPrice, volume, openInterest, impliedVolatility.
    """
    today = today or date.today()
    best: CoveredCallSetup | None = None
    for exp in expirations:
        dte = (_to_date(exp) - today).days
        if not config.DTE_MIN <= dte <= config.DTE_MAX:
            continue
        chain = get_chain(exp)
        if chain is None or chain.empty:
            continue
        for row in chain.itertuples(index=False):
            strike = float(row.strike)
            volume = 0 if pd.isna(row.volume) else int(row.volume)
            oi = 0 if pd.isna(row.openInterest) else int(row.openInterest)
            iv = 0.0 if pd.isna(row.impliedVolatility) else float(row.impliedVolatility)
            bid = 0.0 if pd.isna(row.bid) else float(row.bid)
            ask = 0.0 if pd.isna(row.ask) else float(row.ask)
            if strike <= spot or volume <= config.MIN_OPTION_VOLUME or oi <= config.MIN_OPEN_INTEREST:
                continue
            if iv <= 0 or bid <= 0:
                continue
            delta = bs_call_delta(spot, strike, dte / 365.0, config.RISK_FREE_RATE, iv)
            premium = (bid + ask) / 2 if ask > 0 else bid
            setup = CoveredCallSetup(ticker, spot, strike, exp, dte, delta, iv, bid, ask,
                                     round(premium, 2), volume, oi)
            if best is None or abs(delta - config.TARGET_DELTA) < abs(best.delta - config.TARGET_DELTA):
                best = setup
    return best


def payoff_curve(spot: float, strike: float, premium: float, points: int = 121) -> pd.DataFrame:
    """Profit/loss at expiration for 100 shares, with and without the short call."""
    prices = np.linspace(spot * 0.6, spot * 1.4, points)
    n = config.CONTRACT_SIZE
    stock_pnl = (prices - spot) * n
    covered_pnl = (np.minimum(prices, strike) - spot + premium) * n
    return pd.DataFrame({"price": prices, "stock_only": stock_pnl, "covered_call": covered_pnl})


def robinhood_order(setup: CoveredCallSetup) -> dict:
    """Buy-write limit order specification for Robinhood."""
    n = config.CONTRACT_SIZE
    net_debit = setup.spot - setup.premium
    exp = _to_date(setup.expiration)
    return {
        "Strategy": "Covered Call (Buy-Write)",
        "Leg 1": f"BUY {n} shares {setup.ticker}",
        "Leg 2": f"SELL TO OPEN 1 {setup.ticker} {exp:%m/%d/%Y} ${setup.strike:g} Call",
        "Order Type": "Limit (net debit)",
        "Limit Price (net debit / share)": round(net_debit, 2),
        "Call Premium (mid / share)": setup.premium,
        "Total Net Debit": round(net_debit * n, 2),
        "Collateral Required": f"{n} shares of {setup.ticker} (≈ ${setup.spot * n:,.2f})",
        "Breakeven": round(setup.breakeven, 2),
        "Max Profit if Assigned": round((setup.strike - setup.spot + setup.premium) * n, 2),
        "Time in Force": "Good for Day",
    }
