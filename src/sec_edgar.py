"""Official SEC EDGAR REST API client for 10-Q / 10-K filing metadata."""

from __future__ import annotations

import threading
import time

import requests

from src import config
from src.data_provider import DiskCache

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{document}"
BROWSE_URL = (
    "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
    "&type={form}&dateb=&owner=include&count=40"
)
PERIODIC_FORMS = ("10-Q", "10-K")


class SecEdgarClient:
    def __init__(self, user_agent: str = config.SEC_USER_AGENT, cache: DiskCache | None = None):
        self.cache = cache or DiskCache()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._lock = threading.Lock()
        self._last_request = 0.0

    def _get_json(self, url: str) -> dict | None:
        # Serialize requests and space them out to respect SEC rate limits.
        with self._lock:
            wait = config.SEC_MIN_REQUEST_INTERVAL - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()
            try:
                resp = self.session.get(url, timeout=15)
            except requests.RequestException:
                return None
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def get_cik_map(self) -> dict[str, int]:
        def fetch():
            data = self._get_json(TICKER_MAP_URL)
            if not data:
                return None
            return {row["ticker"].upper(): int(row["cik_str"]) for row in data.values()}

        return self.cache.get_or_fetch("sec:cik_map", config.SEC_TTL_HOURS * 7, fetch) or {}

    def get_cik(self, ticker: str) -> int | None:
        cik_map = self.get_cik_map()
        return cik_map.get(ticker.upper()) or cik_map.get(ticker.upper().replace(".", "-"))

    def get_latest_filings(self, ticker: str) -> dict | None:
        """Latest 10-Q and 10-K: filing date, period (report) date and document URL."""
        cik = self.get_cik(ticker)
        if cik is None:
            return None

        def fetch():
            data = self._get_json(SUBMISSIONS_URL.format(cik=cik))
            if not data:
                return None
            recent = data.get("filings", {}).get("recent", {})
            filings: dict = {"cik": cik}
            for i, form in enumerate(recent.get("form", [])):
                if form in PERIODIC_FORMS and form not in filings:
                    accession = recent["accessionNumber"][i]
                    filings[form] = {
                        "form": form,
                        "filing_date": recent["filingDate"][i],
                        "report_date": recent["reportDate"][i],
                        "url": ARCHIVE_URL.format(
                            cik=cik,
                            accession=accession.replace("-", ""),
                            document=recent["primaryDocument"][i],
                        ),
                    }
                if all(f in filings for f in PERIODIC_FORMS):
                    break
            return filings

        return self.cache.get_or_fetch(f"sec:filings:{cik}", config.SEC_TTL_HOURS, fetch)


def latest_periodic_filing(filings: dict | None) -> dict | None:
    """The most recent of the latest 10-Q and 10-K."""
    if not filings:
        return None
    found = [filings[f] for f in PERIODIC_FORMS if f in filings]
    return max(found, key=lambda f: f["filing_date"]) if found else None


def filings_browse_url(ticker: str, cik: int | None = None, form: str = "10-Q") -> str:
    """EDGAR company filing index (accepts a CIK or, as fallback, the ticker)."""
    return BROWSE_URL.format(cik=cik if cik is not None else ticker, form=form)
