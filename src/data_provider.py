"""Market data provider with a local disk cache.

Market data comes from Yahoo Finance through the `yfinance` library, which
manages Yahoo's cookie + crumb authenticated session internally. Every
response is cached on disk so repeated runs do not re-download data.
"""

from __future__ import annotations

import hashlib
import pickle
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from src import config


class DiskCache:
    """Pickle-based key/value cache with per-entry time-to-live."""

    def __init__(self, directory: Path = config.CACHE_DIR):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.directory / (hashlib.sha1(key.encode()).hexdigest() + ".pkl")

    def get(self, key: str, ttl_hours: float) -> Any | None:
        path = self._path(key)
        if not path.exists():
            return None
        if time.time() - path.stat().st_mtime > ttl_hours * 3600:
            return None
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception:
            return None

    def set(self, key: str, value: Any) -> None:
        tmp = self._path(key).with_suffix(".tmp")
        with tmp.open("wb") as fh:
            pickle.dump(value, fh)
        tmp.replace(self._path(key))

    def get_or_fetch(self, key: str, ttl_hours: float, fetch: Callable[[], Any]) -> Any:
        cached = self.get(key, ttl_hours)
        if cached is not None:
            return cached
        value = fetch()
        if value is not None:
            self.set(key, value)
        return value


def _first(info: dict, *keys: str) -> Any:
    for key in keys:
        value = info.get(key)
        if value not in (None, "", "Infinity"):
            return value
    return None


class MarketDataProvider:
    """Yahoo Finance data access: profiles, annual statements, option chains."""

    def __init__(self, cache: DiskCache | None = None):
        self.cache = cache or DiskCache()

    @staticmethod
    def _ticker(symbol: str):
        import yfinance as yf

        return yf.Ticker(symbol)

    def get_profile(self, symbol: str) -> dict | None:
        """Price, market cap, sector, exchange, ADV, P/B and next earnings date."""

        def fetch():
            try:
                info = self._ticker(symbol).info or {}
            except Exception:
                return None
            price = _first(info, "currentPrice", "regularMarketPrice")
            if price is None:
                return None
            earnings_ts = _first(info, "earningsTimestampStart", "earningsTimestamp")
            earnings_date = (
                datetime.fromtimestamp(earnings_ts, tz=timezone.utc).date() if earnings_ts else None
            )
            return {
                "ticker": symbol,
                "name": _first(info, "shortName", "longName") or symbol,
                "exchange": info.get("exchange"),
                "sector": info.get("sector"),
                "market_cap": info.get("marketCap"),
                "price": float(price),
                "avg_volume": _first(info, "averageVolume", "averageDailyVolume3Month"),
                "price_to_book": info.get("priceToBook"),
                "earnings_date": earnings_date,
            }

        return self.cache.get_or_fetch(f"profile:{symbol}", config.PROFILE_TTL_HOURS, fetch)

    def get_financials(self, symbol: str) -> dict[str, pd.DataFrame] | None:
        """Annual (10-K) income statement, balance sheet and cash flow statement."""

        def fetch():
            try:
                t = self._ticker(symbol)
                statements = {
                    "income": t.income_stmt,
                    "balance": t.balance_sheet,
                    "cashflow": t.cashflow,
                }
            except Exception:
                return None
            if any(df is None or df.empty for df in statements.values()):
                return None
            return statements

        return self.cache.get_or_fetch(f"financials:{symbol}", config.FINANCIALS_TTL_HOURS, fetch)

    def get_quarterly_financials(self, symbol: str) -> dict[str, pd.DataFrame] | None:
        """Quarterly (10-Q) income statement, balance sheet and cash flow statement."""

        def fetch():
            try:
                t = self._ticker(symbol)
                statements = {
                    "income": t.quarterly_income_stmt,
                    "balance": t.quarterly_balance_sheet,
                    "cashflow": t.quarterly_cashflow,
                }
            except Exception:
                return None
            if any(df is None or df.empty for df in statements.values()):
                return None
            return statements

        return self.cache.get_or_fetch(f"quarterly:{symbol}", config.PROFILE_TTL_HOURS, fetch)

    def get_earnings_dates(self, symbol: str) -> list:
        """Past and scheduled earnings announcement dates, oldest first."""

        def fetch():
            try:
                df = self._ticker(symbol).get_earnings_dates(limit=12)
            except Exception:
                return None
            if df is None or df.empty:
                return None
            return sorted({pd.Timestamp(ts).date() for ts in df.index})

        return self.cache.get_or_fetch(f"earnings:{symbol}", config.PROFILE_TTL_HOURS, fetch) or []

    def get_price_history(self, symbol: str, years: int = 3) -> pd.Series | None:
        """Daily closing prices (split-adjusted), indexed by date."""

        def fetch():
            try:
                df = self._ticker(symbol).history(period=f"{years}y", interval="1d", auto_adjust=False)
            except Exception:
                return None
            if df is None or df.empty or "Close" not in df:
                return None
            closes = df["Close"].dropna()
            closes.index = pd.to_datetime([ts.date() for ts in closes.index])
            return closes

        return self.cache.get_or_fetch(f"prices:{symbol}:{years}y", config.PRICE_HISTORY_TTL_HOURS, fetch)

    def get_option_expirations(self, symbol: str) -> list[str]:
        def fetch():
            try:
                return list(self._ticker(symbol).options)
            except Exception:
                return None

        return self.cache.get_or_fetch(f"expirations:{symbol}", config.OPTIONS_TTL_HOURS, fetch) or []

    def get_call_chain(self, symbol: str, expiration: str) -> pd.DataFrame | None:
        def fetch():
            try:
                return self._ticker(symbol).option_chain(expiration).calls
            except Exception:
                return None

        return self.cache.get_or_fetch(
            f"calls:{symbol}:{expiration}", config.OPTIONS_TTL_HOURS, fetch
        )
