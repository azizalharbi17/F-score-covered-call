"""Streamlit dashboard: Piotroski F-Score & P/B Covered Call Screener."""

from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src import config
from src.data_provider import MarketDataProvider
from src.options_engine import payoff_curve, robinhood_order
from src.paper_trader import PaperPortfolio, mark_to_market, simulate_closeout
from src.sec_edgar import SecEdgarClient, filings_browse_url
from src.universe import STAGES, run_screen, signals_table

st.set_page_config(page_title="F-Score Covered Call Screener", layout="wide")

# Plain-language versions of the 9 F-Score points: (short name, if passed, if failed)
PLAIN_POINTS = {
    1: ("Made a profit", "Made a profit last year", "Lost money last year"),
    2: ("Profit up vs last year", "Profit went up vs last year, relative to company size",
        "Profit went down vs last year, relative to company size"),
    3: ("Business brings in cash", "Its business brought in cash", "Its business used more cash than it brought in"),
    4: ("Profit backed by cash", "Cash coming in was higher than reported profit",
        "Reported profit was higher than the cash coming in"),
    5: ("Debt not rising", "Debt did not rise compared to company size", "Debt rose compared to company size"),
    6: ("Bills easier to pay", "Better able to pay short-term bills than last year",
        "Less able to pay short-term bills than last year"),
    7: ("No new shares", "Did not issue new shares", "Issued new shares, shrinking each owner's slice"),
    8: ("Keeps more per sale", "Kept more of each sale as profit than last year",
        "Kept less of each sale as profit than last year"),
    9: ("More sales for its size", "Made more sales for its size than last year",
        "Made fewer sales for its size than last year"),
}
TIER_COLORS = {  # background, text
    "High": ("#dcfce7", "#166534"),
    "Medium": ("#fef9c3", "#854d0e"),
    "Low": ("#fee2e2", "#991b1b"),
}
GREEN, RED = "#16a34a", "#dc2626"


@st.cache_data(ttl=3600, show_spinner="Checking stocks (the first run downloads data and can take a minute)…")
def load_screen(preset: str, run_date: date):
    result = run_screen(config.UNIVERSE_PRESETS[preset], MarketDataProvider(), SecEdgarClient(), today=run_date)
    return result, datetime.now().astimezone()


def tier(score: int) -> str:
    return "High" if score >= 8 else "Medium" if score >= 6 else "Low"


def market_status() -> str:
    """Regular US session, Mon–Fri 9:30–16:00 New York time (exchange holidays not checked)."""
    now = datetime.now(ZoneInfo("America/New_York"))
    return "open" if now.weekday() < 5 and time(9, 30) <= now.time() < time(16, 0) else "closed"


def plain_point(check, missing: list[str]) -> tuple[bool | None, str]:
    if check.name in missing:
        return None, "Not enough data to check this"
    _, yes, no = PLAIN_POINTS[check.number]
    return check.passed, (yes if check.passed else no)


def why_no_picks(result) -> str:
    strong = result.funnel.get(STAGES[5], 0)
    if strong:
        only_volume = set(result.option_notes.values()) == {"options not traded enough"}
        if strong == 1:
            return ("1 stock passed the score, but its options were not traded enough." if only_volume
                    else "1 stock passed the score, but it had no suitable option to sell.")
        tail = "none had enough option trading" if only_volume else "none had a suitable option to sell"
        return f"{strong} stocks passed the score, but {tail}."
    if result.candidates:
        return f"None of the {len(result.candidates)} cheapest stocks scored 8 or higher."
    return "No stocks made it past the price and size filters."


def next_earnings(profile: dict, today: date) -> str:
    d = profile.get("earnings_date")
    return f"{d:%b %-d, %Y}" if d and d >= today else "Not announced"


def dots_html(fs) -> str:
    dots = []
    for ch in fs.checks:
        color = GREEN if ch.passed else RED
        dots.append(f'<span title="{ch.number}. {PLAIN_POINTS[ch.number][0]}" style="display:inline-block;'
                    f'width:14px;height:14px;border-radius:50%;background:{color};margin-right:5px;"></span>')
    return "".join(dots)


def score_html(score: int, size: str = "2.2rem") -> str:
    _, fg = TIER_COLORS[tier(score)]
    return f'<span style="font-size:{size};font-weight:700;color:{fg};">{score} / 9</span>'


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
preset = st.session_state.get("preset", config.DEFAULT_PRESET)
result, updated_at = load_screen(preset, date.today())
today = result.run_date
provider = MarketDataProvider()
portfolio = PaperPortfolio()
picks = result.signals
pick_tickers = {s.ticker for s in picks}

# ---------------------------------------------------------------------------
# Top of page
# ---------------------------------------------------------------------------
st.caption(f"Last updated: {updated_at:%b %-d, %Y at %-I:%M %p %Z}. Market: {market_status()}.")
st.markdown(f'<div style="font-size:2.8rem;font-weight:700;line-height:1.2;">Today\'s picks: {len(picks)}</div>',
            unsafe_allow_html=True)
if not picks:
    st.markdown(f'<div style="font-size:1.15rem;color:#475569;margin-bottom:1rem;">{why_no_picks(result)}</div>',
                unsafe_allow_html=True)

st.subheader("Every stock that reached the score step")
ranked = sorted(result.candidates, key=lambda p: (-result.fscores[p["ticker"]].score, p["ticker"]))
rows, scores = [], []
for p in ranked:
    score = result.fscores[p["ticker"]].score
    if p["ticker"] in pick_tickers:
        ready = "Yes"
    elif p["ticker"] in result.option_notes:
        ready = f"No: {result.option_notes[p['ticker']]}"
    else:
        ready = "No: score too low to check"
    rows.append({
        "Stock": f"{p['ticker']}  ·  {p['name']}",
        "F-Score": f"{score} / 9",
        "Potential": tier(score),
        "Price-to-book": f"{p['price_to_book']:.2f}  ·  Cheap",  # every name here is in its sector's bottom 20%
        "Option ready": ready,
    })
    scores.append(score)
main = pd.DataFrame(rows, columns=["Stock", "F-Score", "Potential", "Price-to-book", "Option ready"])


def _color_scores(df: pd.DataFrame) -> pd.DataFrame:
    styles = pd.DataFrame("", index=df.index, columns=df.columns)
    for i, score in enumerate(scores):
        bg, fg = TIER_COLORS[tier(score)]
        css = f"background-color:{bg};color:{fg};font-weight:700;"
        styles.iloc[i, df.columns.get_loc("F-Score")] = css
        styles.iloc[i, df.columns.get_loc("Potential")] = css
    return styles


if main.empty:
    st.info("No stocks reached the score step today.")
else:
    event = st.dataframe(
        main.style.apply(_color_scores, axis=None),
        hide_index=True, width="stretch", height=min(38 * len(main) + 40, 460),
        on_select="rerun", selection_mode="single-row", key="score_table",
        column_config={"F-Score": st.column_config.TextColumn(width="small"),
                       "Potential": st.column_config.TextColumn(width="small")},
    )
    selected = event.selection.rows
    if selected:
        p = ranked[selected[0]]
        fs = result.fscores[p["ticker"]]
        with st.container(border=True):
            st.markdown(f"**{p['ticker']}  ·  {p['name']}** &nbsp; {score_html(fs.score, '1.6rem')}",
                        unsafe_allow_html=True)
            for ch in fs.checks:
                ok, sentence = plain_point(ch, fs.missing)
                mark = ('<span style="color:#16a34a;font-weight:700;">&#10003;</span>' if ok
                        else '<span style="color:#94a3b8;font-weight:700;">?</span>' if ok is None
                        else '<span style="color:#dc2626;font-weight:700;">&#10007;</span>')
                st.markdown(f"{mark} &nbsp; {sentence}", unsafe_allow_html=True)
    else:
        st.caption("Click a row to see that stock's 9 points.")

# ---------------------------------------------------------------------------
# Section 1: Today's picks
# ---------------------------------------------------------------------------
st.header("Today's picks")
if not picks:
    st.caption("No picks today.")
for sig in picks:
    p, c, fs = sig.profile, sig.setup, sig.fscore
    n = config.CONTRACT_SIZE
    exp = datetime.strptime(c.expiration, "%Y-%m-%d").date()
    with st.container(border=True):
        left, right = st.columns([3, 2])
        left.markdown(f"### {sig.ticker}  ·  {p['name']}")
        left.markdown(f"Price: **${p['price']:,.2f}**")
        right.markdown(f"{score_html(fs.score)}<br>{dots_html(fs)}", unsafe_allow_html=True)
        st.markdown(
            f"**What to do:** Buy {n} shares at ${p['price']:,.2f} (${p['price'] * n:,.2f}). "
            f"Sell 1 call at ${c.strike:g} expiring {exp:%b %-d, %Y}. "
            f"You collect ${c.premium * n:,.2f} now."
        )
        m1, m2, m3 = st.columns(3)
        m1.metric("Most you can make", f"${(c.strike - c.spot + c.premium) * n:,.2f}")
        m2.metric("Break-even price", f"${c.breakeven:,.2f}")
        m3.metric("Next earnings", next_earnings(p, today))
        if st.button("Log to paper portfolio", key=f"log_{sig.ticker}"):
            portfolio.log_position(sig.ticker, c.spot, c.strike, c.premium, c.expiration)
            st.success(f"Added {sig.ticker} to your paper portfolio.")

# ---------------------------------------------------------------------------
# Section 2: Almost picks
# ---------------------------------------------------------------------------
st.header("Almost picks")
if result.option_notes:
    almost = pd.DataFrame([
        {"Ticker": t, "Score": f"{result.fscores[t].score} / 9", "Why it was left out": reason.capitalize()}
        for t, reason in sorted(result.option_notes.items(), key=lambda kv: -result.fscores[kv[0]].score)
    ])
    st.dataframe(almost, hide_index=True, width="stretch")
else:
    st.caption("None today.")

# ---------------------------------------------------------------------------
# Section 3: My paper portfolio
# ---------------------------------------------------------------------------
st.header("My paper portfolio")
positions = portfolio.positions()


def call_quote(pos: dict) -> float | None:
    chain = provider.get_call_chain(pos["ticker"], pos["expiration"])
    if chain is None or chain.empty:
        return None
    row = chain[chain["strike"] == pos["strike"]]
    if row.empty:
        return None
    bid, ask = row["bid"].iloc[0], row["ask"].iloc[0]
    bid = 0.0 if pd.isna(bid) else float(bid)
    ask = 0.0 if pd.isna(ask) else float(ask)
    if ask > 0:
        return (bid + ask) / 2
    return bid if bid > 0 else None


if not positions:
    st.caption("No positions yet. Use \"Log to paper portfolio\" on a pick.")
else:
    held = []
    for pos in positions:
        prof = provider.get_profile(pos["ticker"])
        price = prof["price"] if prof else pos["entry_price"]
        mtm = mark_to_market(pos, price, call_quote(pos))
        held.append({"Ticker": pos["ticker"], "Entry date": pos["opened"], "Cost": mtm["cost"],
                     "Current value": mtm["value"], "Profit/loss ($)": mtm["pnl"],
                     "Profit/loss (%)": mtm["pnl_pct"] * 100})
    st.dataframe(pd.DataFrame(held), hide_index=True, width="stretch", column_config={
        "Cost": st.column_config.NumberColumn(format="$%.2f"),
        "Current value": st.column_config.NumberColumn(format="$%.2f"),
        "Profit/loss ($)": st.column_config.NumberColumn(format="$%.2f"),
        "Profit/loss (%)": st.column_config.NumberColumn(format="%.2f%%"),
    })
    st.caption("Cost is what you paid for the shares minus the option money collected. "
               "Current value is the shares minus what it would cost to buy the option back.")

# ---------------------------------------------------------------------------
# Details
# ---------------------------------------------------------------------------
st.divider()
st.header("Details")

with st.expander("How many stocks passed each step"):
    plain_stages = [
        ("Stocks checked", STAGES[0]),
        ("Listed on NYSE or NASDAQ", STAGES[1]),
        ("Right size, not banks or utilities", STAGES[2]),
        (f"Trade often, price at least ${config.MIN_PRICE:.0f}", STAGES[3]),
        ("Cheapest 20% in their sector", STAGES[4]),
        ("F-Score 8 or higher", STAGES[5]),
        ("Picks with a ready option trade", STAGES[6]),
    ]
    st.dataframe(pd.DataFrame([{"Step": label, "Stocks left": result.funnel.get(key, 0)}
                               for label, key in plain_stages]), hide_index=True)
    st.caption(
        f"Filters: NYSE/NASDAQ only · market value \\$2B–\\$50B · excluding {', '.join(sorted(config.EXCLUDED_SECTORS))} · "
        f"average daily volume ≥ {config.MIN_AVG_DAILY_VOLUME:,} shares · price ≥ \\${config.MIN_PRICE:.0f} · "
        f"bottom {config.PB_BOTTOM_QUANTILE:.0%} price-to-book in sector · F-Score ≥ {config.MIN_FSCORE} · "
        f"call {config.DTE_MIN}–{config.DTE_MAX} days out, delta ≈ {config.TARGET_DELTA:.2f}, "
        f"volume > {config.MIN_OPTION_VOLUME}, open interest > {config.MIN_OPEN_INTEREST}."
    )

with st.expander("Full scorecards"):
    if ranked:
        labels = {f"{p['ticker']}  ·  {p['name']}": p for p in ranked}
        p = labels[st.selectbox("Stock", list(labels), key="scorecard_stock")]
        fs = result.fscores[p["ticker"]]
        st.markdown(f"**F-Score: {fs.score} / 9** · Fiscal year {fs.period_current} vs {fs.period_prior}")
        for category in dict.fromkeys(ch.category for ch in fs.checks):
            st.markdown(f"**{category}**")
            for ch in (c for c in fs.checks if c.category == category):
                st.markdown(f"{'PASS' if ch.passed else 'FAIL'} · **{ch.number}. {ch.name}** · {ch.justification}")
        items = pd.DataFrame({"Current (t)": fs.current_items, "Prior (t-1)": fs.prior_items})
        items.index = [i.replace("_", " ").title() for i in items.index]
        st.markdown("**Line items (annual statements)**")
        st.dataframe(items.map(lambda v: f"{v:,.0f}" if pd.notna(v) else "n/a"), width="stretch")
    else:
        st.caption("No stocks reached the score step.")

with st.expander("Payoff chart and order details"):
    if picks:
        by_ticker = {s.ticker: s for s in picks}
        sig = by_ticker[st.selectbox("Pick", list(by_ticker), key="payoff_pick")]
        setup = sig.setup
        curve = payoff_curve(setup.spot, setup.strike, setup.premium)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=curve["price"], y=curve["covered_call"], name="Covered call"))
        fig.add_trace(go.Scatter(x=curve["price"], y=curve["stock_only"], name="Stock only", line=dict(dash="dash")))
        fig.add_hline(y=0, line_color="gray", line_width=1)
        for x, label in [(setup.spot, "Spot"), (setup.strike, "Strike"), (setup.breakeven, "Breakeven")]:
            fig.add_vline(x=x, line_dash="dot", annotation_text=f"{label} ${x:.2f}")
        fig.update_layout(template="plotly_white", height=420, margin=dict(t=30),
                          xaxis_title="Price at expiration ($)", yaxis_title="Profit / loss per 100 shares ($)")
        st.plotly_chart(fig, width="stretch")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Premium / share", f"${setup.premium:.2f}")
        c2.metric("Est. monthly yield", f"{setup.monthly_yield:.2%}")
        c3.metric("Upside to strike", f"{setup.upside_room:.2%}")
        c4.metric("Delta / prob. OTM", f"{setup.delta:.2f} / {setup.prob_otm:.0%}")
        st.markdown("**Robinhood order specification (buy-write)**")
        st.table(pd.DataFrame(robinhood_order(setup).items(), columns=["Field", "Value"]).astype(str).set_index("Field"))
    else:
        st.caption("No picks today.")

with st.expander("SEC filings"):
    if picks:
        for sig in picks:
            filings = sig.filings or {}
            parts = []
            for form in ("10-Q", "10-K"):
                f = filings.get(form)
                parts.append(f"latest {form} filed {f['filing_date']} (period {f['report_date']}), [open]({f['url']})"
                             if f else f"latest {form} not available")
            st.markdown(f"**{sig.ticker}**: {'; '.join(parts)}; "
                        f"[all filings]({filings_browse_url(sig.ticker, filings.get('cik'))})")
    else:
        st.caption("No picks today.")

with st.expander("Earnings calendar"):
    st.dataframe(result.earnings, hide_index=True, width="stretch")

with st.expander("Paper trade closeout simulator"):
    if positions:
        labels = {f"{p['ticker']} ${p['strike']:g} call {p['expiration']} ({p['id']})": p for p in positions}
        pos = labels[st.selectbox("Position", list(labels), key="closeout_pos")]
        price = st.number_input("Stock price at expiration ($)", min_value=0.0, value=float(pos["strike"]), step=0.5)
        outcome = simulate_closeout(pos, price)
        c1, c2, c3 = st.columns(3)
        c1.metric("Outcome", "Assigned" if outcome["assigned"] else "Expired OTM")
        c2.metric("P/L", f"${outcome['pnl']:,.2f}")
        c3.metric("Return on capital", f"{outcome['return_on_capital']:.2%}")
        st.caption(outcome["outcome"])
    else:
        st.caption("No positions yet.")

with st.expander("Download CSV"):
    table = signals_table(result)
    st.download_button("Download picks CSV", table.to_csv(index=False).encode(),
                       file_name=f"covered_call_signals_{today}.csv", mime="text/csv", disabled=table.empty)

with st.expander("Settings"):
    st.selectbox("Stock list", list(config.UNIVERSE_PRESETS), key="preset")
