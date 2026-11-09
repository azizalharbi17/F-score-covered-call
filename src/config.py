"""Filtering parameters, SEC user-agent, and universe presets."""

import os
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a local .env file (if present) into os.environ."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT_DIR / ".env")

# --- SEC EDGAR -------------------------------------------------------------
# SEC fair-access guidelines require a declared User-Agent. The ticker->CIK
# lookup file on www.sec.gov also expects a contact email, which can be added
# via SEC_USER_AGENT in a local .env file.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "QuantResearchTool/1.0")
SEC_MIN_REQUEST_INTERVAL = 0.12  # seconds; stays under SEC's 10 requests/second

# --- Local storage ---------------------------------------------------------
CACHE_DIR = ROOT_DIR / ".cache"
PROFILE_TTL_HOURS = 24
FINANCIALS_TTL_HOURS = 24 * 7
OPTIONS_TTL_HOURS = 0.25
SEC_TTL_HOURS = 24
PRICE_HISTORY_TTL_HOURS = 1
PAPER_PORTFOLIO_PATH = ROOT_DIR / "data" / "paper_portfolio.json"

# --- Universe filters ------------------------------------------------------
# Yahoo Finance exchange codes for NYSE and NASDAQ listings.
ALLOWED_EXCHANGES = {"NYQ": "NYSE", "NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ"}
MID_CAP_MIN = 2e9
MID_CAP_MAX = 10e9
LARGE_CAP_MAX = 50e9
EXCLUDED_SECTORS = {"Financial Services", "Utilities"}
MIN_AVG_DAILY_VOLUME = 500_000
MIN_PRICE = 10.0
PB_BOTTOM_QUANTILE = 0.20
MIN_FSCORE = 8

# --- Covered call parameters -----------------------------------------------
DTE_MIN = 30
DTE_MAX = 45
TARGET_DELTA = 0.30
MIN_OPTION_VOLUME = 100       # strictly greater than
MIN_OPEN_INTEREST = 500       # strictly greater than
RISK_FREE_RATE = 0.04
CONTRACT_SIZE = 100

# --- Earnings risk windows (days) ------------------------------------------
EARNINGS_HIGH_DAYS = 7
EARNINGS_MEDIUM_DAYS = 14

# --- Universe presets ------------------------------------------------------
# Candidate US mid/large caps; the screen itself enforces exchange, cap,
# sector, and liquidity rules, so names drifting outside the range drop out.
UNIVERSE_PRESETS = {
    "US Mid & Large Cap (Non-Financial)": [
        # Technology
        "AKAM", "FFIV", "NTAP", "HPQ", "HPE", "WDC", "STX", "SWKS", "QRVO", "ON",
        "MCHP", "TER", "ENTG", "MKSI", "LSCC", "CRUS", "SMCI", "GDDY", "ZM", "DOCU",
        "DBX", "TWLO", "OKTA", "PSTG", "CIEN", "GEN", "EPAM", "CTSH", "IT", "JKHY",
        "TRMB", "ZBRA", "KEYS", "TDY", "CDW", "FLEX", "JBL", "ARW", "AVT", "SNX",
        "TTMI", "AMKR", "OLED", "COHR", "DXC", "KD",
        # Healthcare
        "BIIB", "VTRS", "HOLX", "DVA", "UHS", "THC", "CNC", "MOH", "HSIC", "LH",
        "DGX", "BAX", "ZBH", "TFX", "RVTY", "CRL", "INCY", "JAZZ", "EXEL", "UTHR",
        "HALO", "OGN", "PRGO", "ELAN", "BMRN", "NBIX", "ALKS", "ACHC", "ENSG",
        # Consumer discretionary
        "F", "GM", "BBY", "DG", "DLTR", "KSS", "M", "GAP", "AEO", "ANF",
        "URBN", "DKS", "ULTA", "BBWI", "WSM", "RH", "LKQ", "GPC", "AAP", "BWA",
        "LEA", "APTV", "GT", "ALV", "MHK", "WHR", "HAS", "MAT", "PII", "HOG",
        "LEN", "PHM", "TOL", "KBH", "DHI", "NVR", "MTH", "YETI", "CROX", "DECK",
        "PVH", "RL", "TPR", "VFC", "LVS", "WYNN", "MGM", "CZR", "H", "CCL",
        "NCLH", "EXPE", "DRI", "TXRH", "DPZ", "WEN", "EAT", "LAD", "AN", "KMX",
        "PAG", "ABG",
        # Consumer staples
        "KHC", "GIS", "CAG", "CPB", "SJM", "HRL", "MKC", "TSN", "ADM", "BG",
        "INGR", "POST", "TAP", "STZ", "KDP", "CLX", "CHD", "EL", "COTY", "ELF",
        "BJ", "CASY", "KR", "ACI", "SFM", "DAR", "LW", "USFD", "PFGC", "SYY",
        # Industrials
        "DAL", "UAL", "LUV", "AAL", "ALK", "CHRW", "EXPD", "JBHT", "KNX", "ODFL",
        "XPO", "SAIA", "R", "MAS", "OC", "LII", "AOS", "SNA", "SWK", "PNR",
        "DOV", "IEX", "XYL", "FTV", "AME", "ROK", "GNRC", "HII", "LDOS", "SAIC",
        "BAH", "CACI", "TXT", "ALLE", "ACM", "J", "EME", "FLR", "MTZ", "WAB",
        "OSK", "AGCO", "CNH", "TEX", "HUBB", "NDSN", "RRX", "MIDD", "CSL", "BLDR",
        # Materials
        "DOW", "LYB", "CE", "EMN", "HUN", "OLN", "CF", "MOS", "FMC", "ALB",
        "IFF", "PPG", "RPM", "AXTA", "AVY", "BALL", "PKG", "IP", "SW", "AMCR",
        "SEE", "CCK", "SON", "GPK", "NUE", "STLD", "CLF", "RS", "AA", "MLM",
        "VMC", "EXP", "ATI", "CMC",
        # Energy
        "DVN", "CTRA", "APA", "OVV", "HAL", "BKR", "NOV", "CHRD", "MTDR", "PR",
        "RRC", "AR", "EQT", "SM", "MUR", "VLO", "PSX", "DINO", "PBF", "DK",
        "TRGP", "CVI", "CNX", "FANG",
        # Communication services
        "FOXA", "NWSA", "WBD", "OMC", "LYV", "MTCH", "PINS", "SNAP", "NYT", "CHTR",
        "LBRDK", "SIRI", "TKO", "ROKU", "Z",
        # Real estate
        "VNO", "BXP", "SLG", "KIM", "REG", "FRT", "CPT", "MAA", "UDR", "ESS",
        "EQR", "ARE", "HST", "CUBE", "EXR", "INVH", "AMH", "ELS", "SUI", "DOC",
        "VTR", "OHI", "WPC", "NNN", "JLL", "CSGP",
    ],
}
DEFAULT_PRESET = "US Mid & Large Cap (Non-Financial)"
