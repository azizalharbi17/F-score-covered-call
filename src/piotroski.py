"""9-point Piotroski F-Score engine with an itemized audit trail.

Compares the current fiscal year (t) against the prior fiscal year (t-1)
using annual (10-K) statements.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

# Candidate row labels in yfinance annual statements, in priority order.
LINE_ITEMS = {
    "net_income": ("income", ["Net Income", "Net Income Common Stockholders"]),
    "revenue": ("income", ["Total Revenue", "Operating Revenue"]),
    "gross_profit": ("income", ["Gross Profit"]),
    "cost_of_revenue": ("income", ["Cost Of Revenue", "Reconciled Cost Of Revenue"]),
    "total_assets": ("balance", ["Total Assets"]),
    "current_assets": ("balance", ["Current Assets"]),
    "current_liabilities": ("balance", ["Current Liabilities"]),
    "long_term_debt": ("balance", ["Long Term Debt", "Long Term Debt And Capital Lease Obligation"]),
    "shares_outstanding": ("balance", ["Ordinary Shares Number", "Share Issued"]),
    "cfo": ("cashflow", ["Operating Cash Flow", "Cash Flow From Continuing Operating Activities"]),
}


@dataclass
class Check:
    number: int
    category: str
    name: str
    passed: bool
    current: float | None
    prior: float | None
    justification: str


@dataclass
class FScoreResult:
    score: int
    checks: list[Check]
    period_current: str | None = None
    period_prior: str | None = None
    missing: list[str] = field(default_factory=list)
    current_items: dict = field(default_factory=dict)
    prior_items: dict = field(default_factory=dict)


def _value(df: pd.DataFrame, labels: list[str], col) -> float | None:
    """Value for the first matching row label. `col` is a column position (int) or a period date."""
    if df is None or df.empty:
        return None
    if isinstance(col, int):
        if col >= len(df.columns):
            return None
        pos = col
    else:
        matches = [i for i, c in enumerate(df.columns) if pd.Timestamp(c) == pd.Timestamp(col)]
        if not matches:
            return None
        pos = matches[0]
    for label in labels:
        if label in df.index:
            value = df.loc[label].iloc[pos]
            if pd.notna(value):
                return float(value)
    return None


def extract_line_items(statements: dict[str, pd.DataFrame], cols=(0, 1)) -> tuple[dict, dict, str | None, str | None]:
    """Return (current, prior, current_period, prior_period) line-item dicts.

    `cols` are column positions (annual: latest and prior year) or period dates.
    """
    periods = []
    for col in cols:
        items = {}
        for key, (stmt, labels) in LINE_ITEMS.items():
            items[key] = _value(statements.get(stmt), labels, col)
        # Derive gross profit when only cost of revenue is reported.
        if items["gross_profit"] is None and items["revenue"] is not None and items["cost_of_revenue"] is not None:
            items["gross_profit"] = items["revenue"] - items["cost_of_revenue"]
        # A company with no long-term debt line simply carries none.
        if items["long_term_debt"] is None and items["total_assets"] is not None:
            items["long_term_debt"] = 0.0
        periods.append(items)

    def _period(col):
        if not isinstance(col, int):
            return pd.Timestamp(col).strftime("%Y-%m-%d")
        df = statements.get("balance")
        if df is None or col >= len(df.columns):
            return None
        return pd.Timestamp(df.columns[col]).strftime("%Y-%m-%d")

    return periods[0], periods[1], _period(cols[0]), _period(cols[1])


def _ratio(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def _fmt(value: float | None, pct: bool = False) -> str:
    if value is None:
        return "n/a"
    if pct:
        return f"{value:.2%}"
    if abs(value) >= 1e6:
        return f"{value / 1e6:,.1f}M"
    return f"{value:,.3f}"


def compute_fscore(current: dict, prior: dict, period_current: str | None = None,
                   period_prior: str | None = None) -> FScoreResult:
    """Score the 9 binary tests. Missing data counts as a failed test."""
    c, p = current, prior
    checks: list[Check] = []
    missing: list[str] = []

    def add(number, category, name, cur, pri, test, describe, needs_prior=True):
        if cur is None or (needs_prior and pri is None):
            missing.append(name)
            checks.append(Check(number, category, name, False, cur, pri, "Data unavailable — scored 0."))
            return
        checks.append(Check(number, category, name, bool(test(cur, pri)), cur, pri, describe(cur, pri)))

    prof, lev, eff = "Profitability", "Leverage, Liquidity & Capital Structure", "Operating Efficiency"

    # 1. Net income > 0
    add(1, prof, "Net Income > 0", c["net_income"], p["net_income"],
        lambda cur, pri: cur > 0,
        lambda cur, pri: f"Net income = {_fmt(cur)} ({'> 0' if cur > 0 else '≤ 0'}).", needs_prior=False)

    # 2. ΔROA > 0  (ROA = Net Income / Total Assets)
    roa_c = _ratio(c["net_income"], c["total_assets"])
    roa_p = _ratio(p["net_income"], p["total_assets"])
    add(2, prof, "ΔROA > 0", roa_c, roa_p,
        lambda cur, pri: cur > pri,
        lambda cur, pri: f"ROA (NI / Total Assets) {_fmt(cur, True)} vs prior {_fmt(pri, True)}: "
                         f"Δ = {cur - pri:+.2%}.")

    # 3. CFO > 0
    add(3, prof, "Operating Cash Flow > 0", c["cfo"], p["cfo"],
        lambda cur, pri: cur > 0,
        lambda cur, pri: f"CFO = {_fmt(cur)} ({'> 0' if cur > 0 else '≤ 0'}).", needs_prior=False)

    # 4. Accrual check: CFO > Net income (both current period)
    add(4, prof, "Accrual Check: CFO > Net Income", c["cfo"], c["net_income"],
        lambda cfo, ni: cfo > ni,
        lambda cfo, ni: f"CFO {_fmt(cfo)} {'>' if cfo > ni else '≤'} Net income {_fmt(ni)}.")

    # 5. Δ Long-term debt / assets ≤ 0
    lev_c = _ratio(c["long_term_debt"], c["total_assets"])
    lev_p = _ratio(p["long_term_debt"], p["total_assets"])
    add(5, lev, "Δ Long-Term Debt / Assets ≤ 0", lev_c, lev_p,
        lambda cur, pri: cur <= pri,
        lambda cur, pri: f"LTD / Assets {_fmt(cur, True)} vs prior {_fmt(pri, True)}: Δ = {cur - pri:+.2%}.")

    # 6. Δ Current ratio > 0
    cr_c = _ratio(c["current_assets"], c["current_liabilities"])
    cr_p = _ratio(p["current_assets"], p["current_liabilities"])
    add(6, lev, "Δ Current Ratio > 0", cr_c, cr_p,
        lambda cur, pri: cur > pri,
        lambda cur, pri: f"Current ratio {cur:.3f} vs prior {pri:.3f}: Δ = {cur - pri:+.3f}.")

    # 7. Shares outstanding ≤ prior year
    add(7, lev, "Shares Outstanding ≤ Prior Year", c["shares_outstanding"], p["shares_outstanding"],
        lambda cur, pri: cur <= pri,
        lambda cur, pri: f"Shares {_fmt(cur)} vs prior {_fmt(pri)}: Δ = {(cur - pri) / pri:+.2%}.")

    # 8. Δ Gross margin > 0
    gm_c = _ratio(c["gross_profit"], c["revenue"])
    gm_p = _ratio(p["gross_profit"], p["revenue"])
    add(8, eff, "Δ Gross Margin > 0", gm_c, gm_p,
        lambda cur, pri: cur > pri,
        lambda cur, pri: f"Gross margin {_fmt(cur, True)} vs prior {_fmt(pri, True)}: Δ = {cur - pri:+.2%}.")

    # 9. Δ Asset turnover > 0
    at_c = _ratio(c["revenue"], c["total_assets"])
    at_p = _ratio(p["revenue"], p["total_assets"])
    add(9, eff, "Δ Asset Turnover > 0", at_c, at_p,
        lambda cur, pri: cur > pri,
        lambda cur, pri: f"Asset turnover {cur:.3f} vs prior {pri:.3f}: Δ = {cur - pri:+.3f}.")

    return FScoreResult(sum(ch.passed for ch in checks), checks, period_current, period_prior, missing,
                        dict(current), dict(prior))


def score_statements(statements: dict[str, pd.DataFrame]) -> FScoreResult:
    current, prior, pc, pp = extract_line_items(statements)
    return compute_fscore(current, prior, pc, pp)


def year_ago_quarters(statements: dict[str, pd.DataFrame]) -> tuple | None:
    """Latest reported quarter end and the quarter end about one year before it."""
    balance = statements.get("balance")
    if balance is None or balance.empty:
        return None
    reported = [pd.Timestamp(c) for c in balance.columns
                if _value(balance, LINE_ITEMS["total_assets"][1], c) is not None]
    if not reported:
        return None
    latest = max(reported)
    year_ago = [c for c in reported if 330 <= (latest - c).days <= 400]
    return (latest, max(year_ago)) if year_ago else None


def score_quarterly_statements(statements: dict[str, pd.DataFrame]) -> FScoreResult | None:
    """Same 9 tests on the latest quarter (10-Q) vs the same quarter a year earlier."""
    quarters = year_ago_quarters(statements)
    if quarters is None:
        return None
    current, prior, pc, pp = extract_line_items(statements, cols=quarters)
    return compute_fscore(current, prior, pc, pp)
