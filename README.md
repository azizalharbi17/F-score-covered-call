# Piotroski F-Score & P/B Covered Call Screener

A quantitative stock screener, fundamental turnaround analyzer, and monitoring dashboard designed for retail execution and paper-trading **Covered Call** strategies on platforms like **Robinhood**.

The system identifies deeply mispriced US equities by pairing Stanford Professor Joseph Piotroski's 9-point fundamental accounting score with sector-relative Price-to-Book (P/B) ratios, and systematically maps eligible turnaround stocks to high-probability income-generating Covered Call recipes.

---

## 🎯 Executive Strategy & Quantitative Thesis

### 1. Eliminating the "Value Trap"
Buying low Price-to-Book (P/B) equities without fundamental safeguards often traps investors in deteriorating businesses heading toward restructuring or insolvency. Joseph Piotroski's seminal research (*"Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers"*, 2000) demonstrated that applying a strict 9-point accounting health screen to low-valuation stocks:
- Eliminates financially decaying value traps.
- Enhances annual excess returns by an average of **+7.5% over the market**.
- Identifies fundamentally sound companies experiencing temporary sentiment discounts.

### 2. The 9-Point Piotroski Scoring Engine
The engine evaluates current period ($t$) vs. prior period ($t-1$) financial statements across three core categories (yielding binary 1/0 points):

#### Category 1: Profitability (4 Points)
1. **Net Income > 0 (+1):** Positive bottom-line earnings.
2. **Change in Return on Assets ($\Delta$ ROA) > 0 (+1):** ROA this period is higher than ROA in the prior period, where $ROA = Net\\;Income / Total\\;Assets$.
3. **Operating Cash Flow (CFO) > 0 (+1):** Positive operating cash flow.
4. **Accrual Anomaly Check: CFO > Net Income (+1):** Confirms earnings are cash-backed rather than inflated by non-cash receivables.

#### Category 2: Leverage, Liquidity & Capital Structure (3 Points)
5. **$\Delta$ Long-Term Debt / Assets $\le$ 0 (+1):** Decreasing or stable debt leverage.
6. **$\Delta$ Current Ratio > 0 (+1):** Improving short-term working capital liquidity ($Current\\;Assets / Current\\;Liabilities$).
7. **Common Shares Outstanding $\le$ Prior Year (+1):** Zero dilution; no emergency share issuance.

#### Category 3: Operating Efficiency (2 Points)
8. **$\Delta$ Gross Margin > 0 (+1):** Expanding gross margin ($Gross\\;Profit / Revenue$).
9. **$\Delta$ Asset Turnover > 0 (+1):** Increasing operational efficiency ($Revenue / Total\\;Assets$).

> **Turnaround Buy Signal:** Equities achieving an **F-Score of 8 or 9** within the **bottom 20% (quintile) P/B ratio** of their respective sector trigger the high-conviction buy signal.

### 3. Options Income Mapping (Covered Calls)
For every equity passing the Buy Signal, the application queries live options chains to construct an actionable retail covered call trade:
- **Underlying Leg:** Buy 100 shares of the stock.
- **Short Option Leg:** Sell 1 Out-of-the-Money (OTM) Call contract.
- **Target Expiration:** 30 to 45 Days to Expiration (DTE) to capture maximum time decay (Theta).
- **Target Delta:** Approx. **0.30 Delta** (~70% probability of expiring OTM), leaving 5%–10% room for capital appreciation.
- **Liquidity Filters:** Option Daily Volume $> 100$ and Open Interest $> 500$ to ensure tight retail bid-ask spreads.
- **Return Expectations:** Yields **1.5% to 4.0% monthly cashflow**, while providing downside cushion and defined maximum return if assigned.

---

## 🏛️ Architecture & Project Structure

```
F-score-strategy/
├── app.py                     # Streamlit quantitative dashboard application
├── requirements.txt           # Python dependencies
├── README.md                  # Comprehensive documentation and strategy blueprint
├── src/
│   ├── __init__.py
│   ├── config.py              # Filtering parameters, SEC user-agent, presets
│   ├── data_provider.py       # Market data, crumb-authenticated session & disk cache
│   ├── sec_edgar.py           # Official SEC EDGAR REST API client for 10-Q/10-K metadata
│   ├── piotroski.py           # 9-point fundamental calculation engine & audit details
│   ├── options_engine.py      # Black-Scholes Delta, payoff curves & Robinhood order generator
│   ├── earnings_tracker.py    # Upcoming earnings & binary risk calendar
│   ├── universe.py            # 7-stage universe filtering & screening funnel
│   └── paper_trader.py        # Local simulated paper-trading portfolio manager
└── tests/
    ├── __init__.py
    ├── test_screener.py       # Unit tests for scoring, options delta, and earnings
    └── test_run_e2e.py        # Integration test for end-to-end pipeline
```

---

## 📊 Dashboard Modules & UI Features

### Section A: Strategy Parameters & Universe Metrics Header
- Displays active filters: US Exchanges only (NYSE, NASDAQ), Mid-Cap ($2B-$10B) & Large-Cap ($10B-$50B), non-financial and non-utility sectors.
- Real-time quantitative funnel counters:
  `Universe Ingested` $\rightarrow$ `Cap & Sector Screen` $\rightarrow$ `Liquidity (ADV ≥ 500k, Price ≥ $10)` $\rightarrow$ `Bottom 20% P/B` $\rightarrow$ `F-Score ≥ 8` $\rightarrow$ `Actionable Covered Calls`.

### Section B: Actionable Covered Call Signals Table
- Master screener table with columns:
  `Ticker` | `Sector` | `Share Price` | `P/B Ratio` | `F-Score` | `Freshness (10-Q Date)` | `Stock Action` | `Target Call Setup (Strike / DTE)` | `Est. Monthly Yield` | `Options Liquidity`
- **CSV Export:** Download full filtered signals and option parameters with one click.
- **Inspection Studio Drawer:**
  - **9-Point Piotroski Scorecard:** Visual pass/fail badges, itemized line-item values, and mathematical justifications across Profitability, Leverage, and Efficiency.
  - **Interactive Plotly Payoff Diagram:** Dynamic profit/loss curve at expiration comparing the hedged Covered Call vs unhedged stock across strike, spot, and breakeven.
  - **Robinhood Order Specification Card:** Clear Buy-Write limit order details, net debit per share, collateral required, and 1-click **Log to Paper Portfolio** button.
  - **SEC EDGAR Filing Viewer:** Direct link to official SEC filings and disclosure report dates.

### Section C: Upcoming Disclosure & Earnings Tracker
- Scans screened mid/large-cap equities for upcoming earnings announcements within the next 7 to 14 days.
- Flags binary options event risk (`🚨 HIGH` within 7 days, `⚠️ MEDIUM` within 8-14 days, `✅ CLEAR` > 14 days).
- Informs options traders when high-volatility events are pending and alerts when fresh 10-Q/10-K filings will drop for model recalculation.

### Retail Paper-Trading Portfolio Tracker
- Local simulation tracker allowing retail traders to paper-trade generated covered calls.
- Tracks capital allocated, premium collected, breakeven, and includes a **Position Closeout Simulator** to test return upon expiration or assignment.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.10+ (Python 3.11 recommended)
- Windows / macOS / Linux

### 2. Environment Setup
```bash
# Clone or navigate to the repository
cd F-score-strategy

# Activate virtual environment
# Windows:
.\.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Run Unit Tests
```bash
python -m unittest discover tests
```

### 4. Launch the Dashboard
```bash
streamlit run app.py
```
Open your browser to [http://localhost:8501](http://localhost:8501) to interact with the screener.

---

## 🔒 SEC Compliance & Data Integrity
- Requests to the official SEC EDGAR REST API adhere strictly to SEC fair access guidelines with an explicit `User-Agent` header (`QuantResearchTool/1.0`).
- Local disk caching stores company fundamentals to prevent redundant API calls and respect rate limits.

[README]
[strategy]
[instructions]
