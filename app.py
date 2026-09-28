"""Land Reserve Planner — Phase 5 (Streamlit).

Phase 1: top strip, Standard A
Phase 2: B/C, Monte Carlo, Year by year, Assumptions
Phase 3: Fortress assessments, Properties, Snowmass, MUEL, Exit terms
Phase 4: Present mode + printable meeting packet
Phase 5: inflation presets, CapEx in forecast, scenario save/compare

Run:  .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from endowment_engine import (
    NON_MUEL_TOTAL,
    Assumptions,
    Sleeve,
    full_book_sleeve,
    life_label,
    run_forecast,
    scaled_sleeve,
    years_of_support,
)
from monte_carlo import funding_odds, min_endowment_for_odds, min_endowment_real_preserving
from phase3_model import (
    PROPERTY_COSTS_Y1,
    UPCOMING_DEFAULTS,
    PropertyPlan,
    active_burn,
    exit_illustration,
    fortress_assessment_summary,
    muel_snapshot,
    upcoming_weighted_by_year,
)
from meeting_packet import build_packet_md

st.set_page_config(page_title="Land Reserve Planner", layout="wide")


def _expected_password() -> str:
    """Family shared password from Streamlit secrets or env (never hardcode)."""
    try:
        secrets = st.secrets
        if "LAND_PLANNER_PASSWORD" in secrets:
            return str(secrets["LAND_PLANNER_PASSWORD"])
        if "passwords" in secrets and "app" in secrets["passwords"]:
            return str(secrets["passwords"]["app"])
    except Exception:
        pass
    import os
    return os.environ.get("LAND_PLANNER_PASSWORD", "")


def require_family_password() -> None:
    """Block the app until the shared family password is entered."""
    expected = _expected_password()
    if not expected:
        st.error(
            "Password not configured. In Streamlit Cloud go to "
            "App settings → Secrets and add:\n\n"
            'LAND_PLANNER_PASSWORD = "your-family-password"'
        )
        st.stop()
    if st.session_state.get("family_authed"):
        return
    st.markdown("### Land reserve planner")
    st.caption("Family access — enter the shared password.")
    with st.form("family_gate"):
        entered = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Enter")
    if submitted:
        if entered == expected:
            st.session_state["family_authed"] = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


require_family_password()


def starting_sleeve(keep_brk_flag: bool, distribute_amt: float) -> Sleeve:
    s = full_book_sleeve()
    if not keep_brk_flag:
        s = Sleeve(0.0, 0.0, s.etf_mv, s.etf_basis, s.cash)
    book = s.total
    if distribute_amt <= 0:
        return s
    if distribute_amt >= book:
        return Sleeve(0.0, 0.0, 0.0, 0.0, 0.0)
    scale = (book - distribute_amt) / book
    return Sleeve(
        s.brk_mv * scale, s.brk_basis * scale,
        s.etf_mv * scale, s.etf_basis * scale, s.cash * scale,
    )


def make_assumptions(
    burn: float,
    ret: float,
    *,
    inflation: float,
    div_growth: float,
    income_tax: float,
    cg_tax: float,
    extra_costs_by_year: Optional[Dict[int, float]] = None,
    name: str = "ui",
    years: Optional[int] = None,
) -> Assumptions:
    kwargs: Dict[str, Any] = dict(
        market_return=ret,
        opex0=burn,
        name=name,
        inflation=inflation,
        div_growth=div_growth,
        income_tax=income_tax,
        cg_tax=cg_tax,
        extra_costs_by_year=extra_costs_by_year,
    )
    if years is not None:
        kwargs["years"] = years
    return Assumptions(**kwargs)


def forecast(sleeve: Sleeve, burn: float, ret: float, base: Assumptions) -> pd.DataFrame:
    return run_forecast(
        sleeve,
        make_assumptions(
            burn, ret,
            inflation=base.inflation,
            div_growth=base.div_growth,
            income_tax=base.income_tax,
            cg_tax=base.cg_tax,
            extra_costs_by_year=base.extra_costs_by_year,
            name="ui",
        ),
    )


def min_endowment_for_years_ui(
    burn: float,
    market_return: float,
    target_years: int,
    base: Assumptions,
    tol: float = 25_000.0,
) -> float:
    """Binary search that respects inflation + extra_costs_by_year from base."""
    lo, hi = 1_000_000.0, 120_000_000.0

    def lasts(amt: float) -> bool:
        a = make_assumptions(
            burn, market_return,
            inflation=base.inflation,
            div_growth=base.div_growth,
            income_tax=base.income_tax,
            cg_tax=base.cg_tax,
            extra_costs_by_year=base.extra_costs_by_year,
            name="minA",
            years=target_years + 5,
        )
        df = run_forecast(scaled_sleeve(amt), a)
        return years_of_support(df) >= target_years

    if not lasts(hi):
        return float("nan")
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if lasts(mid):
            hi = mid
        else:
            lo = mid
    return hi


def corpus_series(df: pd.DataFrame, name: str) -> pd.Series:
    s = df.loc[df["Net_Endowment"] > 1.0].set_index("Year")["Net_Endowment"]
    s.name = name
    return s


def burn_from_choice(cost_choice: str, plan: PropertyPlan, custom: float = 433_000.0) -> float:
    if cost_choice.startswith("By property"):
        return active_burn(plan, True, 397_000.0)
    if cost_choice.startswith("IC"):
        return active_burn(plan, False, 433_000.0)
    return active_burn(plan, False, custom)


def default_scenario_seeds() -> Dict[str, dict]:
    return {
        "IC $433k (default)": {
            "cost_choice": "IC / Christopher ($433k)",
            "return_pct": 5,
            "distribute": 0.0,
            "keep_brk": True,
            "infl": 0.025,
            "include_capex": False,
            "keep_farm": True,
            "keep_ep": True,
            "keep_sm": True,
            "snow_rent": 0.0,
            "target_years_a": 30,
            "custom_burn": 433_000.0,
        },
        "Cousin compare $397k @ 3%": {
            "cost_choice": "By property ($397k)",
            "return_pct": 5,
            "distribute": 0.0,
            "keep_brk": True,
            "infl": 0.030,
            "include_capex": False,
            "keep_farm": True,
            "keep_ep": True,
            "keep_sm": True,
            "snow_rent": 0.0,
            "target_years_a": 30,
            "custom_burn": 433_000.0,
        },
    }


def snapshot_current(
    cost_choice: str,
    return_pct: int,
    distribute: float,
    keep_brk: bool,
    infl: float,
    include_capex: bool,
    target_years_a: int,
    custom_burn: float,
) -> dict:
    return {
        "cost_choice": cost_choice,
        "return_pct": int(return_pct),
        "distribute": float(distribute),
        "keep_brk": bool(keep_brk),
        "infl": float(infl),
        "include_capex": bool(include_capex),
        "keep_farm": bool(st.session_state.get("keep_farm", True)),
        "keep_ep": bool(st.session_state.get("keep_ep", True)),
        "keep_sm": bool(st.session_state.get("keep_sm", True)),
        "snow_rent": float(st.session_state.get("snow_rent", 0.0)),
        "target_years_a": int(target_years_a),
        "custom_burn": float(custom_burn),
    }


def metrics_from_snapshot(snap: dict, tax_divg: dict) -> dict:
    plan = PropertyPlan(
        keep_farm=bool(snap.get("keep_farm", True)),
        keep_eastpointe=bool(snap.get("keep_ep", True)),
        keep_snowmass=bool(snap.get("keep_sm", True)),
        snowmass_rent=float(snap.get("snow_rent", 0.0)),
    )
    burn = burn_from_choice(
        snap["cost_choice"], plan, float(snap.get("custom_burn", 433_000.0))
    )
    ret = int(snap["return_pct"]) / 100.0
    extras = (
        upcoming_weighted_by_year(UPCOMING_DEFAULTS)
        if snap.get("include_capex", True)
        else None
    )
    base = make_assumptions(
        burn, ret,
        inflation=float(snap["infl"]),
        div_growth=float(tax_divg["divg"]),
        income_tax=float(tax_divg["inctax"]),
        cg_tax=float(tax_divg["cgtax"]),
        extra_costs_by_year=extras,
        name="scenario",
    )
    sleeve = starting_sleeve(bool(snap["keep_brk"]), float(snap["distribute"]) * 1_000_000.0)
    df = forecast(sleeve, burn, ret, base)
    yrs = years_of_support(df)
    ex = df.attrs.get("exhausted_year")
    ty = int(snap.get("target_years_a", 30))
    std_a = float(min_endowment_for_years_ui(burn, ret, ty, base))
    return {
        "burn": burn,
        "return_pct": int(snap["return_pct"]),
        "book": sleeve.total,
        "years": life_label(yrs, ex),
        "std_a": std_a,
        "infl": float(snap["infl"]),
        "include_capex": bool(snap.get("include_capex", True)),
        "cost_choice": snap["cost_choice"],
    }


# ----- Header -----
st.markdown("**FAMILY LAND TRUST · FARM · EASTPOINTE · SNOWMASS**")
st.title("Land Reserve Planner")
st.caption(
    "Phase 5: inflation presets, CapEx in forecast, scenario save/compare. "
    "Speculative planning estimates — not forecasts."
)
_h1, _h2, _h3 = st.columns([4, 1, 1])
with _h2:
    present_mode = st.toggle("Present", value=False, help="Cleaner screen-share view.")
with _h3:
    show_packet = st.toggle("Meeting packet", value=False, help="Printable multi-section memo.")

# ----- Session defaults -----
for k, v in {
    "infl": 0.025, "divg": 0.05, "inctax": 0.29, "cgtax": 0.238,
    "mc_mu": 0.08, "mc_sigma": 0.17, "mc_paths": 200, "mc_target": 0.90,
    "n_trusts": 9, "snow_rent_mode": "None on record ($0)",
    "keep_farm": True, "keep_ep": True, "keep_sm": True, "snow_rent": 0.0,
    "include_capex": False, "infl_preset": "IC default 2.5%",
    "custom_burn": 433_000.0,
}.items():
    st.session_state.setdefault(k, v)

if "scenarios" not in st.session_state or not st.session_state["scenarios"]:
    st.session_state["scenarios"] = default_scenario_seeds()

# ----- Top strip -----
st.subheader("Settings")
c1, c2, c3, c4 = st.columns(4)
with c1:
    cost_choice = st.radio(
        "Yearly property costs",
        ["IC / Christopher ($433k)", "By property ($397k)", "Custom"],
        index=0,
    )
with c2:
    return_pct = st.slider("Stock return, smooth years", 3, 8, 5, 1, format="%d%%")
    market_return = return_pct / 100.0
with c3:
    distribute = st.slider("Distribute non-MUEL now ($M)", 0.0, round(NON_MUEL_TOTAL / 1e6, 1), 0.0, 0.1)
    distribute_dollars = distribute * 1_000_000.0
with c4:
    keep_brk = st.checkbox("Keep the two BRK.A", True)
    target_years_a = st.number_input("Standard A / B horizon (years)", 10, 50, 30, 5)

c5, c6 = st.columns(2)
with c5:
    include_capex = st.checkbox(
        "Include upcoming expenses in forecast",
        value=bool(st.session_state.get("include_capex", False)),
        help="Adds likelihood-weighted CapEx from Properties → Upcoming into the engine.",
    )
    st.session_state["include_capex"] = include_capex
with c6:
    def _on_infl_preset() -> None:
        choice = st.session_state.get("infl_preset_radio", "IC default 2.5%")
        if str(choice).startswith("Cousin"):
            st.session_state["infl"] = 0.030
            st.session_state["infl_preset"] = "Cousin-align ~3.0%"
        else:
            st.session_state["infl"] = 0.025
            st.session_state["infl_preset"] = "IC default 2.5%"

    st.radio(
        "Cost-inflation preset",
        ["IC default 2.5%", "Cousin-align ~3.0%"],
        index=0 if st.session_state.get("infl_preset", "IC default 2.5%").startswith("IC") else 1,
        horizontal=True,
        key="infl_preset_radio",
        on_change=_on_infl_preset,
    )

# Property / snowmass controls live on Lands tabs but affect burn — pull from session
plan = PropertyPlan(
    keep_farm=st.session_state.get("keep_farm", True),
    keep_eastpointe=st.session_state.get("keep_ep", True),
    keep_snowmass=st.session_state.get("keep_sm", True),
    snowmass_rent=float(st.session_state.get("snow_rent", 0.0)),
)

custom_burn = float(st.session_state.get("custom_burn", 433_000.0))
if cost_choice.startswith("By property"):
    burn = active_burn(plan, True, 397_000.0)
elif cost_choice.startswith("IC"):
    burn = active_burn(plan, False, 433_000.0)
else:
    custom_burn = float(st.number_input(
        "Custom yearly costs ($)", 200_000, 600_000, int(custom_burn), 1_000
    ))
    st.session_state["custom_burn"] = custom_burn
    burn = active_burn(plan, False, custom_burn)

extra_costs = upcoming_weighted_by_year(UPCOMING_DEFAULTS) if include_capex else None

base = make_assumptions(
    burn, market_return,
    inflation=float(st.session_state["infl"]),
    div_growth=float(st.session_state["divg"]),
    income_tax=float(st.session_state["inctax"]),
    cg_tax=float(st.session_state["cgtax"]),
    extra_costs_by_year=extra_costs,
    name="base",
)

sleeve = starting_sleeve(keep_brk, distribute_dollars)
df_current = forecast(sleeve, burn, market_return, base)
yrs_cur = years_of_support(df_current)
ex_cur = df_current.attrs.get("exhausted_year")
df_full = forecast(full_book_sleeve(), burn, market_return, base)
yrs_full = years_of_support(df_full)
ex_full = df_full.attrs.get("exhausted_year")

mc_mu = float(st.session_state["mc_mu"])
mc_sigma = float(st.session_state["mc_sigma"])
mc_paths = int(st.session_state["mc_paths"])
mc_target = float(st.session_state["mc_target"])

# ----- Meeting packet (print view) -----
if show_packet:
    from phase3_model import fortress_assessment_summary as _fas, muel_snapshot as _ms
    _std_a_pkt = float(min_endowment_for_years_ui(burn, market_return, int(target_years_a), base))
    _ft = _fas(df_current, burn, base.inflation, n_trusts=int(st.session_state["n_trusts"]))
    _mu = _ms()
    packet_md = build_packet_md(
        burn=burn,
        return_pct=int(return_pct),
        sleeve_total=sleeve.total,
        non_muel_total=NON_MUEL_TOTAL,
        years_label=life_label(yrs_cur, ex_cur),
        exhaust=ex_cur,
        std_a=_std_a_pkt,
        target_years=int(target_years_a),
        keep_brk=keep_brk,
        distribute_m=distribute,
        fortress_pv_per_trust=_ft.get("pv_per_trust"),
        fortress_exhaust=_ft.get("exhaust_year"),
        muel_mv=_mu["mv"],
        muel_eai=_mu["eai"],
    )
    st.success("Meeting packet ready — use the browser Print dialog (Ctrl+P / Cmd+P).")
    st.download_button(
        "Download packet (.md)",
        packet_md,
        file_name="Family_Land_Trust_planning_packet.md",
        mime="text/markdown",
    )
    st.markdown(packet_md)
    st.caption("Turn off Meeting packet to return to the interactive planner.")
    st.stop()

if present_mode:
    st.info(
        f"Presenting · burn **${burn:,.0f}** · return **{return_pct}%** · "
        f"book **${sleeve.total/1e6:.2f}M** · years **{life_label(yrs_cur, ex_cur)}** · "
        f"BRK {'kept' if keep_brk else 'out'} · distribute **${distribute:.1f}M** · "
        f"infl **{base.inflation*100:.1f}%** · CapEx {'on' if include_capex else 'off'}"
    )

# ----- Scenarios save / compare -----
with st.expander("Scenarios — save & compare", expanded=False):
    sc1, sc2 = st.columns([2, 1])
    with sc1:
        scen_name = st.text_input("Scenario name", value="", placeholder="e.g. Cousin 3% + CapEx")
    with sc2:
        st.write("")
        st.write("")
        if st.button("Save scenario", type="primary"):
            name = (scen_name or "").strip()
            if name:
                st.session_state["scenarios"][name] = snapshot_current(
                    cost_choice, int(return_pct), float(distribute), keep_brk,
                    float(st.session_state["infl"]), include_capex,
                    int(target_years_a), float(st.session_state.get("custom_burn", 433_000.0)),
                )
                st.success(f'Saved "{name}".')
            else:
                st.warning("Enter a name first.")
    names = sorted(st.session_state["scenarios"].keys())
    cmp_a, cmp_b = st.columns(2)
    with cmp_a:
        name_a = st.selectbox("Compare A", names, index=0, key="cmp_a")
    with cmp_b:
        default_b = 1 if len(names) > 1 else 0
        name_b = st.selectbox("Compare B", names, index=default_b, key="cmp_b")
    if names:
        if st.button("Compare now"):
            tax_divg = {
                "divg": st.session_state["divg"],
                "inctax": st.session_state["inctax"],
                "cgtax": st.session_state["cgtax"],
            }
            with st.spinner("Comparing scenarios…"):
                st.session_state["cmp_result"] = (
                    name_a,
                    name_b,
                    metrics_from_snapshot(st.session_state["scenarios"][name_a], tax_divg),
                    metrics_from_snapshot(st.session_state["scenarios"][name_b], tax_divg),
                )
        if "cmp_result" in st.session_state:
            ca, cb, ma, mb = st.session_state["cmp_result"]
            cmp_df = pd.DataFrame(
                [
                    {"Metric": "Burn (Y1)", ca: f"${ma['burn']:,.0f}", cb: f"${mb['burn']:,.0f}"},
                    {"Metric": "Return", ca: f"{ma['return_pct']}%", cb: f"{mb['return_pct']}%"},
                    {
                        "Metric": "Starting book",
                        ca: f"${ma['book']/1e6:.2f}M",
                        cb: f"${mb['book']/1e6:.2f}M",
                    },
                    {"Metric": "Years (smooth)", ca: ma["years"], cb: mb["years"]},
                    {
                        "Metric": "Standard A",
                        ca: f"${ma['std_a']/1e6:.1f}M",
                        cb: f"${mb['std_a']/1e6:.1f}M",
                    },
                    {
                        "Metric": "Inflation",
                        ca: f"{ma['infl']*100:.1f}%",
                        cb: f"{mb['infl']*100:.1f}%",
                    },
                    {
                        "Metric": "CapEx in forecast",
                        ca: "Yes" if ma["include_capex"] else "No",
                        cb: "Yes" if mb["include_capex"] else "No",
                    },
                ]
            )
            st.dataframe(cmp_df, width="stretch", hide_index=True)

tabs = st.tabs([
    "Reserve size", "Projection", "Year by year", "Assumptions",
    "Fortress Trusts", "Properties", "Snowmass", "MUEL & dividends", "Exit terms",
])
tab_reserve, tab_proj, tab_yoy, tab_ass, tab_ft, tab_prop, tab_snow, tab_muel, tab_exit = tabs

# ===== Reserve size =====
with tab_reserve:
    st.header("How big should the Land Trust reserve be at decanting?")
    with st.spinner("Solving Standard A…"):
        std_a = float(min_endowment_for_years_ui(burn, market_return, int(target_years_a), base))
    run_bc = st.checkbox("Solve Standards B and C (slower)", False)
    std_b = std_c = float("nan")
    if run_bc:
        with st.spinner("Standard B…"):
            std_b = float(min_endowment_for_odds(
                burn, int(target_years_a), mc_target, mc_mu, mc_sigma, mc_paths, 42, 150_000.0, base))
        with st.spinner("Standard C…"):
            std_c = float(min_endowment_real_preserving(
                burn, market_return, 50, base.inflation, base=base))

    r1, r2, r3 = st.columns(3)
    r1.metric(f"A · {int(target_years_a)}y @ {return_pct}%", f"${std_a/1e6:.1f}M")
    r1.caption(f"Burn ${burn:,.0f} (after property/Snowmass settings)")
    if run_bc and std_b == std_b:
        r2.metric(f"B · {mc_target:.0%} odds", f"${std_b/1e6:.1f}M")
    else:
        r2.metric("B · Monte Carlo", "—")
    if run_bc and std_c == std_c:
        r3.metric("C · Real value 50y", f"${std_c/1e6:.1f}M")
    else:
        r3.metric("C · Real-preserving", "—")

    st.caption(
        f"Our Standard A at current settings: **${std_a/1e6:.2f}M** "
        f"(infl {base.inflation*100:.1f}%, CapEx {'on' if include_capex else 'off'}). "
        "Cousin site shows ~$11.1M at $397k / 5% / 30y — use the Cousin-align ~3.0% inflation "
        "preset to get closer to that band (~$11.1–11.2M)."
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Your settings · years", life_label(yrs_cur, ex_cur))
    m2.metric("Your starting book", f"${sleeve.total:,.0f}")
    m3.metric("Full book · years", life_label(yrs_full, ex_full))

    chart = pd.concat([
        corpus_series(df_current, "Your settings"),
        corpus_series(forecast(scaled_sleeve(std_a), burn, market_return, base), f"A (${std_a/1e6:.1f}M)"),
        corpus_series(df_full, "Full book"),
    ], axis=1)
    st.line_chart(chart)

# ===== Projection =====
with tab_proj:
    st.header("Projection")
    p1, p2, p3, p4 = st.columns(4)
    p1.metric("Years (smooth)", life_label(yrs_cur, ex_cur))
    p2.metric("Exhaust year", "—" if ex_cur is None else str(ex_cur))
    p3.metric("Starting book", f"${sleeve.total/1e6:.2f}M")
    p4.metric("Year-1 burn", f"${burn:,.0f}")
    st.line_chart(corpus_series(df_current, "Net endowment"))
    horizon_mc = st.selectbox("Odds horizon (years)", [30, 50], index=0)
    if st.button("Run Monte Carlo odds", type="primary"):
        with st.spinner(f"{mc_paths} paths…"):
            st.session_state["odds_you"] = funding_odds(
                sleeve.total, burn, int(horizon_mc), mc_mu, mc_sigma, mc_paths, 42, base)
            st.session_state["odds_full"] = funding_odds(
                NON_MUEL_TOTAL, burn, int(horizon_mc), mc_mu, mc_sigma, mc_paths, 42, base)
    if "odds_you" in st.session_state:
        o1, o2 = st.columns(2)
        o1.metric("Your settings odds", f"{st.session_state['odds_you']['odds']*100:.0f}%")
        o2.metric("Full book odds", f"{st.session_state['odds_full']['odds']*100:.0f}%")

# ===== Year by year =====
with tab_yoy:
    st.header("Year by year")
    show = df_current[["Year", "Begin_Corpus", "AfterTax_Div", "OpEx", "PAL_Draw",
                       "BRK_Sold", "ETF_Sold", "CG_Tax", "Net_Endowment"]].copy()
    for col in show.columns:
        if col != "Year":
            show[col] = show[col].map(lambda x: round(float(x), 0))
    st.dataframe(show, width="stretch", hide_index=True, height=420)
    st.download_button("Download CSV", show.to_csv(index=False), "year_by_year.csv", "text/csv")
    if include_capex and extra_costs:
        st.caption(
            "Upcoming CapEx is included in the engine need (likelihood-weighted). "
            f"Years with extras: {', '.join(str(y) for y in sorted(extra_costs))}."
        )

# ===== Assumptions =====
with tab_ass:
    st.header("Assumptions")
    st.write(
        f"Active inflation preset: **{st.session_state.get('infl_preset', 'IC default 2.5%')}** "
        f"→ {float(st.session_state['infl'])*100:.1f}% "
        "(change via the Cost-inflation preset on the Settings strip)."
    )
    a1, a2 = st.columns(2)
    with a1:
        st.session_state["infl"] = st.number_input(
            "Cost inflation", 0.0, 0.08, float(st.session_state["infl"]), 0.005, format="%.3f",
            help="Presets on the Settings strip set 2.5% (IC) or 3.0% (cousin-align).",
        )
        st.session_state["divg"] = st.number_input(
            "Dividend growth", 0.0, 0.10, float(st.session_state["divg"]), 0.005, format="%.3f"
        )
        st.session_state["inctax"] = st.number_input(
            "Dividend tax", 0.0, 0.50, float(st.session_state["inctax"]), 0.01, format="%.2f"
        )
        st.session_state["cgtax"] = st.number_input(
            "CG tax", 0.0, 0.40, float(st.session_state["cgtax"]), 0.001, format="%.3f"
        )
    with a2:
        st.session_state["mc_mu"] = st.number_input(
            "MC average return", 0.03, 0.12, float(st.session_state["mc_mu"]), 0.005, format="%.3f"
        )
        st.session_state["mc_sigma"] = st.number_input(
            "MC volatility", 0.05, 0.30, float(st.session_state["mc_sigma"]), 0.01, format="%.2f"
        )
        st.session_state["mc_paths"] = st.number_input(
            "MC paths", 50, 500, int(st.session_state["mc_paths"]), 50
        )
        st.session_state["mc_target"] = st.number_input(
            "Standard B target odds", 0.5, 0.99, float(st.session_state["mc_target"]), 0.01, format="%.2f"
        )
        st.session_state["n_trusts"] = st.number_input(
            "Fortress trusts (count)", 1, 21, int(st.session_state["n_trusts"]), 1
        )
    st.info("Change a top-strip control after edits so the app reruns.")

# ===== Fortress Trusts =====
with tab_ft:
    st.header("Fortress Trusts — if the reserve runs out")
    st.write(
        "On the **smooth** path: after the reserve is exhausted, land costs are treated as "
        "branch assessments split evenly across the Fortress trusts (default 9 ⇒ each pays 1/21 of costs). "
        "Monte Carlo “chance bills start” is on Projection; here is the cash consequence on this path."
    )
    n_trusts = int(st.session_state["n_trusts"])
    cases = {
        "Your settings": df_current,
        "Full book kept": df_full,
        "Standard A sleeve": forecast(scaled_sleeve(std_a), burn, market_return, base),
        "Keep none": forecast(Sleeve(0, 0, 0, 0, 0), burn, market_return, base),
    }
    rows = []
    for name, df in cases.items():
        summ = fortress_assessment_summary(
            df, burn, base.inflation, n_trusts=n_trusts, discount=0.05, through_year=2100
        )
        kept = {
            "Your settings": sleeve.total,
            "Full book kept": NON_MUEL_TOTAL,
            "Standard A sleeve": std_a,
            "Keep none": 0.0,
        }[name]
        rows.append({
            "Case": name,
            "Kept": f"${kept/1e6:.1f}M",
            "Exhaust year": summ["exhaust_year"] or "—",
            "Each trust gets now (1/n)": f"${summ['securities_if_distributed_now']:,.0f}",
            "PV all bills / trust": f"${summ['pv_per_trust']:,.0f}",
            "Typical 2060s bill / trust (real)": f"${summ['per_trust_typical_2060s']:,.0f}",
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    summ_you = fortress_assessment_summary(df_current, burn, base.inflation, n_trusts)
    if summ_you["bills"]:
        bill_df = pd.DataFrame(summ_you["bills"], columns=["Year", "Total real", "Per trust real"])
        bill_df = bill_df.set_index("Year")[["Per trust real"]]
        st.subheader("Expected yearly bill per Fortress trust (smooth path, today's $)")
        st.line_chart(bill_df)
    else:
        st.success("On this smooth path the reserve never runs out — no assessment bills in the model.")

    st.caption(
        "Draft v1B Art. II ¶G leaves assessment mechanics to the administrative trustee. "
        "This tab is an illustration only."
    )

# ===== Properties =====
with tab_prop:
    st.header("Properties — keep or sell")
    st.write(
        "With **By property** costs, unchecking a property removes its Year-1 cost from the burn. "
        "Sale proceeds / tax are not yet wired. The IC **$433k** single figure (and Custom) does not "
        "cut costs when you uncheck a property — CapEx is already embedded in that burn."
    )
    p1, p2, p3 = st.columns(3)
    with p1:
        st.checkbox("Keep Farm", key="keep_farm")
        st.caption(f"Illustrative Y1 cost ${PROPERTY_COSTS_Y1['Farm']:,.0f}")
    with p2:
        st.checkbox("Keep Eastpointe", key="keep_ep")
        st.caption(f"Illustrative Y1 cost ${PROPERTY_COSTS_Y1['Eastpointe']:,.0f}")
    with p3:
        st.checkbox("Keep Snowmass", key="keep_sm")
        st.caption(f"Illustrative Y1 cost ${PROPERTY_COSTS_Y1['Snowmass']:,.0f}")

    st.metric("Effective Year-1 burn now", f"${burn:,.0f}")
    st.subheader("Upcoming expenses (placeholders)")
    up = pd.DataFrame(UPCOMING_DEFAULTS)
    st.dataframe(up, width="stretch", hide_index=True)
    weighted = upcoming_weighted_by_year(UPCOMING_DEFAULTS)
    if weighted:
        st.bar_chart(pd.Series(weighted, name="Weighted reserve hit"))
    if include_capex:
        st.caption(
            "These likelihood-weighted amounts **ARE included** in the forecast engine "
            "(and Standard A / B / C searches) while “Include upcoming expenses in forecast” is checked."
        )
    else:
        st.caption(
            "Upcoming expenses are listed here but **not** added into the forecast — "
            "check “Include upcoming expenses in forecast” on the Settings strip to wire them in."
        )

# ===== Snowmass =====
with tab_snow:
    st.header("Snowmass rental income")
    mode = st.radio(
        "Rental income in the plan",
        ["None on record ($0)", "Estimate ($30,600)"],
        index=0 if st.session_state.get("snow_rent", 0) == 0 else 1,
    )
    st.session_state["snow_rent"] = 0.0 if mode.startswith("None") else 30_600.0
    st.session_state["snow_rent_mode"] = mode
    st.write(
        f"Current setting: **${st.session_state['snow_rent']:,.0f}** net per year, treated as a "
        "simple reduction to Year-1 burn (Phase 3 simplification)."
    )
    st.caption("Cousin site default is $0 (none on record since 2019). Flip to estimate to see longer funding life.")

# ===== MUEL =====
with tab_muel:
    st.header("MUEL & dividends")
    snap = muel_snapshot()
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("MUEL shares", f"{snap['shares']:,}")
    m2.metric("MUEL market value", f"${snap['mv']/1e6:.1f}M")
    m3.metric("MUEL EAI (div)", f"${snap['eai']:,.0f}")
    m4.metric("Implied yield", f"{snap['yield']*100:.2f}%")
    st.write(snap["note"])
    st.write(
        f"Non-MUEL book in the reserve model: **${NON_MUEL_TOTAL/1e6:.1f}M**. "
        "Decanting that keeps MUEL in the Land Trust is not yet simulated as part of the reserve sleeve."
    )

# ===== Exit terms =====
with tab_exit:
    st.header("Exit terms (illustrative)")
    st.write("One-branch exit sketch — not legal advice; mirrors the cousin site’s optional levers.")
    e1, e2 = st.columns(2)
    with e1:
        land_val = st.number_input("Assumed land value (all three)", 1_000_000, 50_000_000, 12_000_000, 100_000)
        land_share = st.slider("Land share paid to exiting branch", 0.0, 1.0, 1.0 / 7.0, 0.01)
    with e2:
        res_share = st.slider("Reserve share paid to exiting branch", 0.0, 1.0, 1.0 / 7.0, 0.01)
        use_reserve = sleeve.total
    ill = exit_illustration(land_share, res_share, land_val, use_reserve)
    x1, x2, x3, x4 = st.columns(4)
    x1.metric("Land check", f"${ill['land_check']:,.0f}")
    x2.metric("Reserve check", f"${ill['reserve_check']:,.0f}")
    x3.metric("Total to exiting", f"${ill['total_to_exiting']:,.0f}")
    x4.metric("Reserve after", f"${ill['reserve_after']:,.0f}")

st.caption(
    f"Non-MUEL ${NON_MUEL_TOTAL:,.0f} · Phase 5 · CapEx + scenarios + inflation presets"
)
