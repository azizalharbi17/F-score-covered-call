"""Local simulated paper-trading portfolio for covered calls (JSON file)."""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

from src import config

N = config.CONTRACT_SIZE


class PaperPortfolio:
    def __init__(self, path: Path = config.PAPER_PORTFOLIO_PATH):
        self.path = Path(path)

    def positions(self) -> list[dict]:
        if not self.path.exists():
            return []
        return json.loads(self.path.read_text())

    def _save(self, positions: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(positions, indent=2))

    def log_position(self, ticker: str, entry_price: float, strike: float, premium: float,
                     expiration: str, opened: date | None = None) -> dict:
        position = {
            "id": uuid.uuid4().hex[:8],
            "ticker": ticker,
            "opened": (opened or date.today()).isoformat(),
            "shares": N,
            "entry_price": round(entry_price, 2),
            "strike": strike,
            "premium": round(premium, 2),
            "expiration": expiration,
        }
        positions = self.positions()
        positions.append(position)
        self._save(positions)
        return position

    def summary(self) -> dict:
        positions = self.positions()
        return {
            "positions": len(positions),
            "capital_allocated": sum(capital_allocated(p) for p in positions),
            "premium_collected": sum(premium_collected(p) for p in positions),
        }


def capital_allocated(p: dict) -> float:
    return p["entry_price"] * p["shares"]


def premium_collected(p: dict) -> float:
    return p["premium"] * p["shares"]


def breakeven(p: dict) -> float:
    return p["entry_price"] - p["premium"]


def simulate_closeout(p: dict, price_at_expiration: float) -> dict:
    """Outcome at expiration: called away above the strike, else call expires worthless."""
    assigned = price_at_expiration >= p["strike"]
    exit_price = p["strike"] if assigned else price_at_expiration
    pnl = (exit_price - p["entry_price"] + p["premium"]) * p["shares"]
    net_debit = (p["entry_price"] - p["premium"]) * p["shares"]
    return {
        "outcome": "Assigned — shares called away at strike" if assigned
                   else "Call expired worthless — shares retained",
        "assigned": assigned,
        "exit_value": exit_price * p["shares"],
        "pnl": pnl,
        "return_on_capital": pnl / capital_allocated(p),
        "return_on_net_debit": pnl / net_debit if net_debit else 0.0,
    }
