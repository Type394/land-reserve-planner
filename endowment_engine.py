#!/usr/bin/env python3
"""Family Land Trust — Minimum Land Endowment engine (2026-09-25).

Non-MUEL income endowment life model with Blue Sheet Sep 23, 2026 holdings,
SCHB TTM dividend corrected 2026-09-25 (Yahoo trailing-4-quarter $0.307/sh;
Blue Sheet Div tab had Q1/Q2 ~10× too high). Planning burn embeds historical
CapEx average; separate triennial CapEx is off by default (capex_y1=0).
PAL paydown remains every 3 years.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ---------------------------------------------------------------------------
# Blue Sheet holdings (embedded.xlsx, as of 2026-09-23); income corrected 2026-09-25
# ---------------------------------------------------------------------------
BLUE_DATE = "2026-09-25"
TOTAL_WITH_MUEL = 60_952_789.040888995
NON_MUEL_TOTAL = 15_477_580.470888998
MUEL_MV = 45_475_208.57
MUEL_SHARES = 98_857  # 91,557 certificates + 7,300 at Schwab
MUEL_EAI = 138_399.80
STOCK_SUBTOTAL_MV = 15_301_674.470888998
STOCK_SUBTOTAL_COST = 5_878_150.83
STOCK_SUBTOTAL_GAIN = 9_423_523.640889
STOCK_WA_GAIN = 1.6031442393064623  # ~160%
# SCHB correction 2026-09-25: 13,189.8507 sh × $0.307 TTM = ~$4,049 (was ~$21,209 / ~5.43%).
# Corrected sleeve pretax non-MUEL EAI locked at $263,937; model rounds to PRETAX_Y1.
NON_MUEL_EAI_EXACT = 263_937.0
COMBINED_EAI = NON_MUEL_EAI_EXACT + MUEL_EAI  # ~402,337
CASH_MV = 175_906.0  # SNVXX + SNSXX + Cash
BRK_MV = 1_524_472.0
BRK_BASIS = 445_767.0
ETF_MV = STOCK_SUBTOTAL_MV - BRK_MV  # 13,777,202.47
ETF_BASIS = STOCK_SUBTOTAL_COST - BRK_BASIS  # 5,432,383.83
PAL_START = 0.0

# Planning knobs
PRETAX_Y1 = 264_000.0  # rounded corrected non-MUEL EAI ($263,937); SCHB fix 2026-09-25
# Trust Notes tax stack (2026-09-25): ~96% qualified dividends @ 28.5% all-in
# (federal QD + Missouri + NIIT); ~4% ordinary trust income @ 40.8% illustrative
# (37% federal trust top + 3.8% NIIT). Effective = 0.96*0.285 + 0.04*0.408 = 0.28992 → 0.290.
INCOME_TAX = 0.290
AFTERTAX_Y1 = PRETAX_Y1 * (1.0 - INCOME_TAX)  # 187,440 — reported as ~$187k
DIV_GROWTH = 0.05
OPEX_INFLATION = 0.025
# CapEx: planning burn ($433k) already embeds historical CapEx average.
# Set CAPEX_Y1_DOLLARS = 0 to disable a separate triennial CapEx layer (Option 1).
# Parameterize >0 to re-enable lumpy CapEx in Year-1 dollars, inflated to outlay year.
CAPEX_Y1_DOLLARS = 0.0
CAPEX_EVERY = 3  # used only when CAPEX_Y1_DOLLARS > 0
CAPEX_INFLATION = 0.025
PAL_PAYDOWN_EVERY = 3  # independent of CapEx layer
PAL_RATE = 0.07
PAL_CAP_FRAC = 0.30
CG_TAX = 0.238  # LTCG + NIIT (same as prior model)
START_YEAR = 2026
ENGINE_YEARS = 60  # enough to see exhaust / 50+
PLANNING_HORIZON = 50

BURNS = [433_000, 400_000, 375_000, 350_000, 325_000]
RETURNS = [0.05, 0.07, 0.09]


@dataclass
class Sleeve:
    brk_mv: float
    brk_basis: float
    etf_mv: float
    etf_basis: float
    cash: float

    @property
    def total(self) -> float:
        return self.brk_mv + self.etf_mv + self.cash


def full_book_sleeve() -> Sleeve:
    return Sleeve(BRK_MV, BRK_BASIS, ETF_MV, ETF_BASIS, CASH_MV)


def scaled_sleeve(target: float) -> Sleeve:
    """Scale full non-MUEL book to a target starting MV (for min-endowment search).
    Pro-rata across BRK / ETF / cash (works above or below today's full book).
    """
    if target <= 0:
        return Sleeve(0.0, 0.0, 0.0, 0.0, 0.0)
    if abs(target - NON_MUEL_TOTAL) < 1.0:
        return full_book_sleeve()
    scale = target / NON_MUEL_TOTAL
    return Sleeve(
        BRK_MV * scale,
        BRK_BASIS * scale,
        ETF_MV * scale,
        ETF_BASIS * scale,
        CASH_MV * scale,
    )


def gross_up_sale(net_needed: float, gain_frac: float, cg_tax: float) -> float:
    if net_needed <= 0:
        return 0.0
    drag = min(0.99, gain_frac * cg_tax)
    return net_needed / (1.0 - drag)


def sell_to_raise_net(
    s: Sleeve, div_state: float, net_needed: float, cg_tax: float
) -> Tuple[float, float, float, float, float]:
    """Sell BRK first, then ETF, with CG gross-up. Returns brk_sold, etf_sold, cg_tax, net_raised, div_state."""
    brk_sold = etf_sold = cg_tax_paid = net_raised = 0.0
    remaining = net_needed
    if remaining > 0 and s.brk_mv > 0:
        gfrac = max(0.0, 1.0 - s.brk_basis / s.brk_mv) if s.brk_mv > 0 else 0.0
        gross = gross_up_sale(remaining, gfrac, cg_tax)
        sell = min(gross, s.brk_mv)
        actual_tax = sell * gfrac * cg_tax
        net = sell - actual_tax
        if s.brk_mv > 0:
            s.brk_basis *= 1.0 - sell / s.brk_mv
        s.brk_mv -= sell
        brk_sold = sell
        cg_tax_paid += actual_tax
        net_raised += net
        remaining = max(0.0, remaining - net)
    if remaining > 0 and s.etf_mv > 0:
        gfrac = max(0.0, 1.0 - s.etf_basis / s.etf_mv) if s.etf_mv > 0 else 0.0
        gross = gross_up_sale(remaining, gfrac, cg_tax)
        sell = min(gross, s.etf_mv)
        actual_tax = sell * gfrac * cg_tax
        net = sell - actual_tax
        pre = s.etf_mv
        if pre > 0:
            frac = sell / pre
            s.etf_basis *= 1.0 - frac
            div_state *= 1.0 - frac
        s.etf_mv = pre - sell
        etf_sold = sell
        cg_tax_paid += actual_tax
        net_raised += net
        remaining = max(0.0, remaining - net)
    return brk_sold, etf_sold, cg_tax_paid, net_raised, div_state


@dataclass
class Assumptions:
    market_return: float
    opex0: float  # annual burn (OpEx + assessments + routine CapEx)
    name: str = ""
    inflation: float = OPEX_INFLATION
    div_growth: float = DIV_GROWTH
    income_tax: float = INCOME_TAX
    pretax_y1: float = PRETAX_Y1
    cg_tax: float = CG_TAX
    capex_y1: float = CAPEX_Y1_DOLLARS
    capex_every: int = CAPEX_EVERY
    capex_inflation: float = CAPEX_INFLATION
    years: int = ENGINE_YEARS
    start_year: int = START_YEAR
    pal_balance: float = PAL_START
    pal_rate: float = PAL_RATE
    pal_paydown_every: int = PAL_PAYDOWN_EVERY
    pal_cap_frac: float = PAL_CAP_FRAC
    # If True, scale Year-1 pretax dividends with sleeve ETF share of full book
    scale_div_with_sleeve: bool = True
    # Optional path of all-in total returns (length >= years). When set, overrides market_return each year.
    annual_returns: Optional[List[float]] = None
    # Optional calendar-year -> extra spend (CapEx / specials), added to that year's need
    extra_costs_by_year: Optional[Dict[int, float]] = None


def run_forecast(sleeve0: Sleeve, a: Assumptions) -> pd.DataFrame:
    """PAL-bridge model with optional CapEx + PAL paydown (CG gross-up on sales).

    Total return `r` is ALL-IN (price + dividends). Dividends are the income component
    of that return, not an extra layer on top of full `r` applied to MV.

    CapEx: when capex_y1 <= 0, no separate lumpy CapEx is added (planning burn may
    already embed historical CapEx). When capex_y1 > 0, CapEx fires every capex_every
    years inflated from Year-1 dollars.

    Mechanics each year i (0-indexed, Year = start_year + i):
      1. pretax_div = div_state; aftertax = pretax * (1 - tax) — spendable income.
         BRK (non-dividend) grows at full r.
         ETF price return = r - (div_state / begin_etf) so ETF total = price + yield = r.
         (If yield > r, price return can be negative.)
      2. opex = opex0 * (1+infl)^i
      3. capex = 0 if capex_y1 <= 0; else capex_y1 * (1+capex_infl)^(i+1) on cadence
      4. pal_int = begin_pal * pal_rate
      5. total_need = opex + capex + pal_int; shortfall after after-tax div + cash → PAL draw
      6. Every pal_paydown_every years (or if PAL > cap*gross): sell BRK then ETF to pay PAL to $0
      7. Grow div_state by div_growth for next year (after any sale scaling)
    """
    s = Sleeve(sleeve0.brk_mv, sleeve0.brk_basis, sleeve0.etf_mv, sleeve0.etf_basis, sleeve0.cash)
    # Scale pretax dividends if starting sleeve is a fraction of full book
    if a.scale_div_with_sleeve and ETF_MV > 0:
        div_scale = s.etf_mv / ETF_MV
    else:
        div_scale = 1.0
    div_state = a.pretax_y1 * div_scale
    pal = float(a.pal_balance)
    out: List[dict] = []
    exhausted_year = None

    for i in range(a.years):
        year = a.start_year + i
        if a.annual_returns is not None and i < len(a.annual_returns):
            r = float(a.annual_returns[i])
        else:
            r = a.market_return
        begin_total = s.total
        begin_brk, begin_etf, begin_cash = s.brk_mv, s.etf_mv, s.cash
        begin_pal = pal

        if begin_total - begin_pal <= 1.0 or begin_total <= 1.0:
            if exhausted_year is None:
                exhausted_year = year
            opex = a.opex0 * ((1.0 + a.inflation) ** i)
            if a.capex_y1 > 0 and a.capex_every > 0 and (i + 1) % a.capex_every == 0:
                capex = a.capex_y1 * ((1.0 + a.capex_inflation) ** (i + 1))
            else:
                capex = 0.0
            out.append(
                {
                    "Year": year,
                    "Return": r,
                    "Begin_Corpus": 0.0,
                    "Begin_PAL": begin_pal,
                    "Pretax_Div": 0.0,
                    "AfterTax_Div": 0.0,
                    "OpEx": opex,
                    "CapEx": capex,
                    "PAL_Interest": begin_pal * a.pal_rate,
                    "Total_Need": 0.0,
                    "Shortfall": 0.0,
                    "PAL_Draw": 0.0,
                    "PAL_Paydown": 0.0,
                    "Forced_Paydown": False,
                    "BRK_Sold": 0.0,
                    "ETF_Sold": 0.0,
                    "CG_Tax": 0.0,
                    "End_Corpus": 0.0,
                    "End_PAL": pal,
                    "Net_Endowment": 0.0,
                    "Exhausted": True,
                }
            )
            continue

        # All-in total return r: BRK (no dividend) at full r; ETF price = r - current yield.
        # Dividends are the income slice of r — spendable after tax — not layered on top of full r.
        pretax_div = div_state if begin_etf > 1 else 0.0
        aftertax_div = pretax_div * (1.0 - a.income_tax)
        etf_yield = (pretax_div / begin_etf) if begin_etf > 1 else 0.0
        etf_price_return = r - etf_yield

        s.brk_mv = begin_brk * (1.0 + r)
        s.etf_mv = begin_etf * (1.0 + etf_price_return)

        opex = a.opex0 * ((1.0 + a.inflation) ** i)
        is_paydown_year = (i + 1) % a.pal_paydown_every == 0
        # Separate CapEx layer only when capex_y1 > 0 (else burn embeds CapEx)
        if a.capex_y1 > 0 and a.capex_every > 0 and (i + 1) % a.capex_every == 0:
            # Year-n CapEx = Y1_dollars * (1+infl)^n  (n = i+1)
            capex = a.capex_y1 * ((1.0 + a.capex_inflation) ** (i + 1))
        else:
            capex = 0.0
        extra = 0.0
        if a.extra_costs_by_year:
            extra = float(a.extra_costs_by_year.get(year, 0.0) or 0.0)
        capex = capex + extra

        pal_int = begin_pal * a.pal_rate
        total_need = opex + capex + pal_int

        if total_need <= aftertax_div:
            s.cash = begin_cash + (aftertax_div - total_need)
            shortfall = 0.0
        else:
            need_from_cash = total_need - aftertax_div
            if begin_cash >= need_from_cash:
                s.cash = begin_cash - need_from_cash
                shortfall = 0.0
            else:
                shortfall = need_from_cash - begin_cash
                s.cash = 0.0

        brk_sold = etf_sold = cg_tax_paid = 0.0
        pal_draw = 0.0
        pal_paydown = 0.0
        forced_paydown = False

        if shortfall > 0:
            pal += shortfall
            pal_draw = shortfall

        gross_now = s.total
        if pal > 0 and gross_now > 0 and (pal / gross_now) > a.pal_cap_frac and not is_paydown_year:
            forced_paydown = True

        if pal > 1.0 and (is_paydown_year or forced_paydown):
            payoff = pal
            bs, es, tax, raised, div_state = sell_to_raise_net(s, div_state, payoff, a.cg_tax)
            brk_sold += bs
            etf_sold += es
            cg_tax_paid += tax
            applied = min(raised, pal)
            pal -= applied
            pal_paydown = applied
            if pal < 1.0:
                pal = 0.0

        end_corpus = s.total
        end_pal = pal
        net_endowment = end_corpus - end_pal

        if net_endowment <= 1.0 and exhausted_year is None:
            exhausted_year = year
            if end_corpus <= 1.0:
                s.brk_mv = s.etf_mv = s.cash = 0.0
                end_corpus = 0.0
                net_endowment = 0.0

        if i < a.years - 1 and div_state > 0 and s.etf_mv > 0:
            div_state *= 1.0 + a.div_growth

        out.append(
            {
                "Year": year,
                "Return": r,
                "Begin_Corpus": begin_total,
                "Begin_PAL": begin_pal,
                "Pretax_Div": pretax_div,
                "AfterTax_Div": aftertax_div,
                "OpEx": opex,
                "CapEx": capex,
                "PAL_Interest": pal_int,
                "Total_Need": total_need,
                "Shortfall": shortfall,
                "PAL_Draw": pal_draw,
                "PAL_Paydown": pal_paydown,
                "Forced_Paydown": forced_paydown,
                "BRK_Sold": brk_sold,
                "ETF_Sold": etf_sold,
                "CG_Tax": cg_tax_paid,
                "End_Corpus": end_corpus,
                "End_PAL": end_pal,
                "Net_Endowment": net_endowment,
                "Exhausted": net_endowment <= 1.0,
            }
        )

    df = pd.DataFrame(out)
    df.attrs["exhausted_year"] = exhausted_year
    return df


def years_of_support(df: pd.DataFrame) -> int:
    alive = df.loc[df["Net_Endowment"] > 1.0]
    return int(len(alive))


def life_label(years: int, exhaust_year, horizon: int = PLANNING_HORIZON) -> str:
    if years >= horizon and (exhaust_year is None or years >= ENGINE_YEARS):
        return "50+"
    if years > horizon and exhaust_year is not None:
        # lasted past 50 but exhausted later within engine
        if years >= horizon:
            return f"{years}" if years < ENGINE_YEARS else "50+"
    if years >= ENGINE_YEARS:
        return "50+"
    return str(years)


def net_at_year(df: pd.DataFrame, calendar_year: int) -> Optional[float]:
    row = df.loc[df["Year"] == calendar_year]
    if row.empty:
        return None
    net = float(row["Net_Endowment"].iloc[0])
    if net <= 1.0:
        return None
    return net


def surplus_at_horizon(df: pd.DataFrame, horizon_years: int) -> Tuple[str, Optional[float]]:
    """Net MV at end of horizon_years of funding; n/a if depleted earlier."""
    target_year = START_YEAR + horizon_years - 1
    exhaust = df.attrs.get("exhausted_year")
    yrs = years_of_support(df)
    if yrs < horizon_years:
        return ("n/a", None) if exhaust else ("n/a", None)
    net = net_at_year(df, target_year)
    if net is None:
        return ("n/a", None)
    return (f"{net:,.0f}", net)


def min_endowment_for_years(
    burn: float, market_return: float, target_years: int = 50, tol: float = 25_000.0
) -> float:
    """Binary search starting non-MUEL MV needed to last target_years."""
    lo, hi = 1_000_000.0, 120_000_000.0

    def lasts(amt: float) -> bool:
        sleeve = scaled_sleeve(amt)
        a = Assumptions(market_return=market_return, opex0=burn, years=target_years + 5)
        df = run_forecast(sleeve, a)
        return years_of_support(df) >= target_years

    if not lasts(hi):
        return float("nan")  # even huge book fails (shouldn't)
    # If full book already lasts, still find minimum
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if lasts(mid):
            hi = mid
        else:
            lo = mid
    return hi


def run_all_scenarios() -> Dict:
    """Run (return × burn) grid; return structured results + forecast frames."""
    results = []
    forecasts = {}
    for ret in RETURNS:
        for burn in BURNS:
            key = (ret, burn)
            a = Assumptions(
                market_return=ret,
                opex0=burn,
                name=f"r{ret*100:.0f}_burn{burn}",
                years=ENGINE_YEARS,
            )
            sleeve = full_book_sleeve()
            df = run_forecast(sleeve, a)
            yrs = years_of_support(df)
            exh = df.attrs.get("exhausted_year")
            y50_net = net_at_year(df, START_YEAR + PLANNING_HORIZON - 1)
            s10 = surplus_at_horizon(df, 10)
            s20 = surplus_at_horizon(df, 20)
            s30 = surplus_at_horizon(df, 30)
            # life display
            if yrs >= PLANNING_HORIZON and (exh is None or yrs >= ENGINE_YEARS):
                life_disp = "50+"
            elif yrs >= PLANNING_HORIZON and exh is not None:
                life_disp = str(yrs)  # e.g. 54
            else:
                life_disp = str(yrs)

            # terminal at y50 if still solvent
            if y50_net is not None and yrs >= PLANNING_HORIZON:
                term50 = y50_net
            else:
                term50 = None

            # min endowment for 50y (optional)
            try:
                min50 = min_endowment_for_years(burn, ret, 50)
            except Exception:
                min50 = float("nan")

            row = {
                "return": ret,
                "burn": burn,
                "years": yrs,
                "life_disp": life_disp,
                "exhaust_year": exh,
                "y50_net": term50,
                "surplus_10": s10[1],
                "surplus_20": s20[1],
                "surplus_30": s30[1],
                "min_endowment_50": min50,
            }
            results.append(row)
            forecasts[key] = df
    return {"rows": results, "forecasts": forecasts}


if __name__ == "__main__":
    out = run_all_scenarios()
    for r in out["rows"]:
        print(
            f"ret={r['return']*100:.0f}% burn=${r['burn']:,.0f} life={r['life_disp']} "
            f"exh={r['exhaust_year']} y10={r['surplus_10']} y20={r['surplus_20']} y30={r['surplus_30']} "
            f"min50={r['min_endowment_50']}"
        )
    print(f"AFTERTAX_Y1={AFTERTAX_Y1:,.2f} NON_MUEL={NON_MUEL_TOTAL:,.2f}")
