"""Streamlit dashboard: Piotroski F-Score & P/B Covered Call Screener."""

from __future__ import annotations

from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import config
from src.data_provider import MarketDataProvider
from src.earnings_tracker import HIGH, MEDIUM
from src.options_engine import payoff_curve, robinhood_order
from src.paper_trader import PaperPortfolio, breakeven, capital_allocated, premium_collected, simulate_closeout
from src.sec_edgar import SecEdgarClient, filings_browse_url
from src.universe import DISPLAY_COLUMNS, STAGES, run_screen, signals_table

st.set_page_config(page_title="F-Score Covered Call Screener", page_icon="📈", layout="wide")


@st.cache_data(ttl=3600, show_spinner="Screening universe (first run downloads data; later runs use the disk cache)…")
def load_screen(preset: str, run_date: date):
    return run_screen(config.UNIVERSE_PRESETS[preset], MarketDataProvider(), SecEdgarClient(), today=run_date)


st.title("📈 Piotroski F-Score & P/B Covered Call Screener")

preset = st.sidebar.selectbox("Universe preset", list(config.UNIVERSE_PRESETS), index=0)
result = load_screen(preset, date.today())
table = signals_table(result)
portfolio = PaperPortfolio()

# ---------------------------------------------------------------------------
# Section A: Strategy parameters & universe metrics
# ---------------------------------------------------------------------------
st.header("A · Strategy Parameters & Universe Funnel")
st.markdown(
    f"**Active filters:** US exchanges only (NYSE, NASDAQ) · Mid-Cap (\\$2B–\\$10B) & Large-Cap (\\$10B–\\$50B) · "
    f"Excluding {', '.join(sorted(config.EXCLUDED_SECTORS))} · ADV ≥ {config.MIN_AVG_DAILY_VOLUME:,} · "
    f"Price ≥ \\${config.MIN_PRICE:.0f} · Bottom {config.PB_BOTTOM_QUANTILE:.0%} sector P/B · "
    f"F-Score ≥ {config.MIN_FSCORE} · Calls {config.DTE_MIN}–{config.DTE_MAX} DTE, ≈{config.TARGET_DELTA:.2f} Δ, "
    f"volume > {config.MIN_OPTION_VOLUME}, OI > {config.MIN_OPEN_INTEREST}"
)
funnel_labels = [
    ("Universe Ingested", STAGES[0]),
    ("Cap & Sector Screen", STAGES[2]),  # includes the US-exchange filter
    (f"Liquidity (ADV ≥ 500k, Price ≥ ${config.MIN_PRICE:.0f})", STAGES[3]),
    ("Bottom 20% P/B", STAGES[4]),
    ("F-Score ≥ 8", STAGES[5]),
    ("Actionable Covered Calls", STAGES[6]),
]
for col, (label, key) in zip(st.columns(len(funnel_labels)), funnel_labels):
    col.metric(label, result.funnel.get(key, 0))
st.caption(f"Run date {result.run_date} · US-exchange stage: {result.funnel.get(STAGES[1], 0)} of "
           f"{result.funnel.get(STAGES[0], 0)} ingested listed on NYSE/NASDAQ.")

# ---------------------------------------------------------------------------
# Section B: Actionable covered call signals
# ---------------------------------------------------------------------------
st.header("B · Actionable Covered Call Signals")
if table.empty:
    st.info("No equities currently pass every stage of the screen (F-Score ≥ 8 in the bottom 20% sector P/B "
            "with a liquid 30–45 DTE call). The funnel above shows where candidates dropped out.")
else:
    st.dataframe(table[DISPLAY_COLUMNS], hide_index=True, width="stretch")
st.download_button("⬇️ Download signals CSV", table.to_csv(index=False).encode(),
                   file_name=f"covered_call_signals_{result.run_date}.csv", mime="text/csv",
                   disabled=table.empty)

if result.signals:
    st.subheader("🔍 Inspection Studio")
    by_ticker = {s.ticker: s for s in result.signals}
    choice = st.selectbox("Inspect signal", list(by_ticker))
    sig = by_ticker[choice]
    setup, fs = sig.setup, sig.fscore

    with st.expander(f"{choice} — {sig.profile['name']}", expanded=True):
        tab_score, tab_payoff, tab_order, tab_sec = st.tabs(
            ["9-Point Scorecard", "Payoff Diagram", "Robinhood Order", "SEC EDGAR Filings"])

        with tab_score:
            st.markdown(f"**F-Score: {fs.score} / 9** · Fiscal year {fs.period_current} vs {fs.period_prior}")
            for category in dict.fromkeys(ch.category for ch in fs.checks):
                st.markdown(f"**{category}**")
                for ch in (c for c in fs.checks if c.category == category):
                    badge = "🟢 PASS" if ch.passed else "🔴 FAIL"
                    st.markdown(f"{badge} &nbsp; **{ch.number}. {ch.name}** — {ch.justification}")
            items = pd.DataFrame({"Current (t)": fs.current_items, "Prior (t-1)": fs.prior_items})
            items.index = [i.replace("_", " ").title() for i in items.index]
            st.markdown("**Line items (annual statements)**")
            st.dataframe(items.map(lambda v: f"{v:,.0f}" if pd.notna(v) else "n/a"), width="stretch")

        with tab_payoff:
            curve = payoff_curve(setup.spot, setup.strike, setup.premium)
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=curve["price"], y=curve["covered_call"], name="Covered Call"))
            fig.add_trace(go.Scatter(x=curve["price"], y=curve["stock_only"], name="Stock Only",
                                     line=dict(dash="dash")))
            fig.add_hline(y=0, line_color="gray", line_width=1)
            for x, label in [(setup.spot, "Spot"), (setup.strike, "Strike"), (setup.breakeven, "Breakeven")]:
                fig.add_vline(x=x, line_dash="dot", annotation_text=f"{label} ${x:.2f}")
            fig.update_layout(xaxis_title="Price at expiration ($)", yaxis_title="Profit / loss per 100 shares ($)",
                              height=420, margin=dict(t=30))
            st.plotly_chart(fig, width="stretch")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Premium / share", f"${setup.premium:.2f}")
            c2.metric("Est. monthly yield", f"{setup.monthly_yield:.2%}")
            c3.metric("Upside to strike", f"{setup.upside_room:.2%}")
            c4.metric("Δ / Prob. OTM", f"{setup.delta:.2f} / {setup.prob_otm:.0%}")

        with tab_order:
            order = robinhood_order(setup)
            with st.container(border=True):
                st.markdown("**Robinhood Order Specification — Buy-Write**")
                st.table(pd.DataFrame(order.items(), columns=["Field", "Value"]).astype(str).set_index("Field"))
            if st.button("📝 Log to Paper Portfolio", key=f"log_{choice}"):
                portfolio.log_position(choice, setup.spot, setup.strike, setup.premium, setup.expiration)
                st.success(f"Logged {choice} covered call to the paper portfolio.")

        with tab_sec:
            filings = sig.filings or {}
            cik = filings.get("cik")
            for form in ("10-Q", "10-K"):
                f = filings.get(form)
                if f:
                    st.markdown(f"**Latest {form}:** filed {f['filing_date']} · period {f['report_date']} · "
                                f"[Open filing]({f['url']})")
                else:
                    st.markdown(f"**Latest {form}:** not available")
            st.markdown(f"[EDGAR filing index for {choice}]({filings_browse_url(choice, cik)})")

# ---------------------------------------------------------------------------
# Section C: Upcoming disclosure & earnings tracker
# ---------------------------------------------------------------------------
st.header("C · Upcoming Disclosure & Earnings Tracker")
earn = result.earnings
c1, c2, c3 = st.columns(3)
c1.metric("🚨 HIGH (≤ 7 days)", int((earn["Event Risk"] == HIGH).sum()) if not earn.empty else 0)
c2.metric("⚠️ MEDIUM (8–14 days)", int((earn["Event Risk"] == MEDIUM).sum()) if not earn.empty else 0)
c3.metric("Screened equities", len(earn))
st.dataframe(earn, hide_index=True, width="stretch")

# ---------------------------------------------------------------------------
# Retail paper-trading portfolio tracker
# ---------------------------------------------------------------------------
st.header("📒 Paper-Trading Portfolio")
positions = portfolio.positions()
if not positions:
    st.info("No paper positions yet. Use **Log to Paper Portfolio** in the Inspection Studio.")
else:
    summary = portfolio.summary()
    c1, c2, c3 = st.columns(3)
    c1.metric("Open positions", summary["positions"])
    c2.metric("Capital allocated", f"${summary['capital_allocated']:,.2f}")
    c3.metric("Premium collected", f"${summary['premium_collected']:,.2f}")
    st.dataframe(pd.DataFrame([{
        "ID": p["id"], "Ticker": p["ticker"], "Opened": p["opened"], "Shares": p["shares"],
        "Entry": p["entry_price"], "Strike": p["strike"], "Premium": p["premium"],
        "Expiration": p["expiration"], "Capital": capital_allocated(p),
        "Premium Collected": premium_collected(p), "Breakeven": round(breakeven(p), 2),
    } for p in positions]), hide_index=True, width="stretch")

    st.subheader("Position Closeout Simulator")
    labels = {f"{p['ticker']} ${p['strike']:g}C {p['expiration']} ({p['id']})": p for p in positions}
    pos = labels[st.selectbox("Position", list(labels))]
    price = st.number_input("Stock price at expiration ($)", min_value=0.0, value=float(pos["strike"]), step=0.5)
    outcome = simulate_closeout(pos, price)
    c1, c2, c3 = st.columns(3)
    c1.metric("Outcome", "Assigned" if outcome["assigned"] else "Expired OTM")
    c2.metric("P/L", f"${outcome['pnl']:,.2f}")
    c3.metric("Return on capital", f"{outcome['return_on_capital']:.2%}")
    st.caption(outcome["outcome"])
