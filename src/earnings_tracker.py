"""Upcoming earnings and binary event-risk calendar."""

from __future__ import annotations

from datetime import date

import pandas as pd

from src import config

HIGH, MEDIUM, CLEAR, UNKNOWN = "🚨 HIGH", "⚠️ MEDIUM", "✅ CLEAR", "❔ UNKNOWN"


def classify_earnings_risk(days_until: int | None) -> str:
    """HIGH within 7 days, MEDIUM within 8-14 days, CLEAR beyond 14 days."""
    if days_until is None or days_until < 0:
        return UNKNOWN
    if days_until <= config.EARNINGS_HIGH_DAYS:
        return HIGH
    if days_until <= config.EARNINGS_MEDIUM_DAYS:
        return MEDIUM
    return CLEAR


def days_until(earnings_date: date | None, today: date) -> int | None:
    if earnings_date is None:
        return None
    return (earnings_date - today).days


def build_earnings_calendar(profiles: list[dict], today: date | None = None) -> pd.DataFrame:
    """One row per screened equity, soonest earnings first."""
    today = today or date.today()
    rows = []
    for p in profiles:
        d = days_until(p.get("earnings_date"), today)
        if d is not None and d < 0:
            d = None  # stale date from the last report; next date not yet published
        risk = classify_earnings_risk(d)
        if risk in (HIGH, MEDIUM):
            note = "Options event risk pending; fresh 10-Q/10-K follows — recalculate F-Score."
        elif risk == CLEAR:
            note = "No earnings within 14 days."
        else:
            note = "Next earnings date not published."
        rows.append({
            "Ticker": p["ticker"],
            "Sector": p.get("sector"),
            "Next Earnings": p.get("earnings_date") if d is not None else None,
            "Days Until": d,
            "Event Risk": risk,
            "Note": note,
        })
    df = pd.DataFrame(rows, columns=["Ticker", "Sector", "Next Earnings", "Days Until", "Event Risk", "Note"])
    return df.sort_values("Days Until", na_position="last").reset_index(drop=True)
