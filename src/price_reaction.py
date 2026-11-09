"""How a stock's price moved after its 10-Q / 10-K filings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

FLAT_MOVE = 0.02  # moves smaller than 2% count as flat when naming a pattern


@dataclass
class Move:
    """Price path from the filing day's close to the end of a window."""

    start_date: date        # first trading day on or after the filing
    start_price: float
    end_date: date
    end_price: float
    low_date: date
    low_price: float
    high_date: date
    high_price: float
    rebound_price: float    # highest close on or after the low

    @property
    def change(self) -> float:
        return self.end_price / self.start_price - 1

    @property
    def dip(self) -> float:
        """Biggest drop below the filing-day price (0 if it never closed lower)."""
        return min(0.0, self.low_price / self.start_price - 1)

    @property
    def rise(self) -> float:
        """Highest rise above the filing-day price (0 if it never closed higher)."""
        return max(0.0, self.high_price / self.start_price - 1)

    @property
    def low_vs_start(self) -> float:
        return self.low_price / self.start_price - 1

    @property
    def high_vs_start(self) -> float:
        return self.high_price / self.start_price - 1

    @property
    def from_low_to_end(self) -> float:
        return self.end_price / self.low_price - 1

    @property
    def rebound(self) -> float:
        """Biggest rise from the low to any later close in the window."""
        return self.rebound_price / self.low_price - 1

    def days_to_dip(self, filing_date: date) -> int:
        return (self.low_date - filing_date).days


def _to_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def measure(prices: pd.Series | None, start, end_before=None) -> Move | None:
    """Measure closes from the first trading day on/after `start` up to (not including) `end_before`."""
    start = _to_date(start)
    if prices is None or prices.empty or start is None:
        return None
    start_ts = pd.Timestamp(start)
    if prices.index[0] > start_ts + pd.Timedelta(days=4):
        return None  # price history does not reach back to the filing (a few days allowed for weekends/holidays)
    seg = prices[prices.index >= start_ts]
    end_before = _to_date(end_before)
    if end_before is not None:
        seg = seg[seg.index < pd.Timestamp(end_before)]
    if seg.empty:
        return None
    low_ts, high_ts = seg.idxmin(), seg.idxmax()
    return Move(
        start_date=seg.index[0].date(), start_price=float(seg.iloc[0]),
        end_date=seg.index[-1].date(), end_price=float(seg.iloc[-1]),
        low_date=low_ts.date(), low_price=float(seg[low_ts]),
        high_date=high_ts.date(), high_price=float(seg[high_ts]),
        rebound_price=float(seg[seg.index >= low_ts].max()),
    )


def classify_pattern(move: Move) -> str:
    dipped, rose = move.dip <= -FLAT_MOVE, move.rise >= FLAT_MOVE
    if dipped and rose:
        return "Dip then up" if move.low_date < move.high_date else "Up then down"
    if dipped:
        return "Straight down" if move.change < 0 else "Dip then up"
    if rose:
        return "Straight up" if move.change >= 0 else "Up then down"
    return "Straight up" if move.change >= 0 else "Straight down"


def past_quarters(prices: pd.Series | None, filings: list[dict], count: int = 8) -> list[dict]:
    """One row per completed filing window (filing day to the day before the next filing), newest first."""
    dated = sorted(
        ({"form": f.get("form", "n/a"), "filing_date": _to_date(f.get("filing_date"))} for f in filings),
        key=lambda f: f["filing_date"] or date.min,
    )
    rows = []
    for current, nxt in zip(dated, dated[1:]):
        move = None
        if current["filing_date"] and nxt["filing_date"]:
            move = measure(prices, current["filing_date"], nxt["filing_date"])
        rows.append({
            "form": current["form"],
            "filing_date": current["filing_date"],
            "next_filing": nxt["filing_date"],
            "move": move,
            "pattern": classify_pattern(move) if move else None,
        })
    return rows[-count:][::-1]


def _pct(x: float) -> str:
    return f"{abs(x) * 100:.1f}%"


def _fmt(d: date) -> str:
    return f"{d:%b %-d, %Y}"


def since_sentence(label: str, filing_date, move: Move | None) -> str:
    filing_date = _to_date(filing_date)
    if filing_date is None:
        return f"Since the {label}: n/a (filing date not available)."
    head = f"Since the {label} ({_fmt(filing_date)}): "
    if move is None:
        return head + "n/a (no price data)."
    text = head + (f"up {_pct(move.change)}." if move.change >= 0 else f"down {_pct(move.change)}.")
    if move.dip < 0:
        text += f" It first dropped {_pct(move.dip)} (lowest on {_fmt(move.low_date)})"
        if move.low_date == move.end_date:
            text += ", and the latest close is that low."
        else:
            text += f", then rose {_pct(move.from_low_to_end)} from that low."
    else:
        text += " It never closed below the filing-day price."
    return text


def better_since(change_10k: float | None, change_10q: float | None) -> str:
    known = {k: v for k, v in (("10-K", change_10k), ("10-Q", change_10q)) if v is not None}
    if not known:
        return "n/a"
    if all(v < 0 for v in known.values()):
        return "Neither (down since both)" if len(known) == 2 else "Neither (down since the " + next(iter(known)) + ")"
    return max(known, key=known.get)


def summarize(rows: list[dict]) -> str:
    valid = [r for r in rows if r["move"]]
    if not valid:
        return "Not enough price data to summarize past quarters."
    n = len(valid)
    dip_first = [r["move"] for r in valid if r["pattern"] == "Dip then up"]
    higher = sum(1 for r in valid if r["move"].change > 0)
    if dip_first:
        avg_dip = sum(-m.dip for m in dip_first) / len(dip_first)
        avg_rebound = sum(m.rebound for m in dip_first) / len(dip_first)
        text = (f"In {len(dip_first)} of the last {n} quarters it dipped first, by {avg_dip * 100:.0f}% on average, "
                f"then rose {avg_rebound * 100:.0f}% on average from that low before the next report.")
    else:
        text = f"In none of the last {n} quarters did it dip first and then recover."
    return text + f" It ended higher by the next report in {higher} of {n}."
