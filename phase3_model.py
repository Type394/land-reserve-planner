"""Phase 3 planning helpers: Fortress assessments, properties, MUEL, exits."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from endowment_engine import (
    MUEL_EAI,
    MUEL_MV,
    MUEL_SHARES,
    NON_MUEL_TOTAL,
    Assumptions,
    Sleeve,
    run_forecast,
    scaled_sleeve,
    years_of_support,
)


# Illustrative property cost split when "By property" total is used (sums ~397k)
PROPERTY_COSTS_Y1 = {
    "Farm": 180_000.0,
    "Eastpointe": 140_000.0,
    "Snowmass": 77_000.0,
}

UPCOMING_DEFAULTS = [
    {"name": "Farm tractor", "year": 2028, "amount": 75_000, "likelihood": 0.7, "payer": "reserve"},
    {"name": "Farm mower", "year": 2029, "amount": 18_000, "likelihood": 0.8, "payer": "reserve"},
    {"name": "Eastpointe pool replacement", "year": 2032, "amount": 120_000, "likelihood": 0.4, "payer": "reserve"},
    {"name": "Eastpointe golf cart", "year": 2027, "amount": 12_000, "likelihood": 0.6, "payer": "branch"},
    {"name": "Snowmass utility vehicle", "year": 2030, "amount": 25_000, "likelihood": 0.5, "payer": "reserve"},
]


@dataclass
class PropertyPlan:
    keep_farm: bool = True
    keep_eastpointe: bool = True
    keep_snowmass: bool = True
    sell_year_farm: int = 2030
    sell_year_eastpointe: int = 2030
    sell_year_snowmass: int = 2030
    snowmass_rent: float = 0.0  # annual net, after tax simplification = pretax reduction to burn


def active_burn(plan: PropertyPlan, base_by_property: bool, christopher_burn: float) -> float:
    """Year-1 burn after keep/sell (sells assumed at start for Phase 3 simplicity)."""
    if not base_by_property:
        # Christopher / custom single figure — sales not modeled as cost cuts
        return christopher_burn - plan.snowmass_rent
    total = 0.0
    if plan.keep_farm:
        total += PROPERTY_COSTS_Y1["Farm"]
    if plan.keep_eastpointe:
        total += PROPERTY_COSTS_Y1["Eastpointe"]
    if plan.keep_snowmass:
        total += PROPERTY_COSTS_Y1["Snowmass"]
    total = max(0.0, total - plan.snowmass_rent)
    return total


def upcoming_weighted_by_year(items: List[dict]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for it in items:
        if it.get("payer") != "reserve":
            continue
        y = int(it["year"])
        out[y] = out.get(y, 0.0) + float(it["amount"]) * float(it["likelihood"])
    return out


def fortress_assessment_summary(
    df: pd.DataFrame,
    burn0: float,
    inflation: float,
    n_trusts: int = 9,
    discount: float = 0.05,
    through_year: int = 2100,
) -> Dict:
    """After reserve exhausts, branches pay the inflated burn; split across n_trusts (1/21 if 9)."""
    exhaust = df.attrs.get("exhausted_year")
    start = int(df["Year"].iloc[0])
    share = 1.0 / n_trusts
    bills = []  # (year, total_real, per_trust_real)
    if exhaust is None:
        return {
            "exhaust_year": None,
            "chance_proxy": 0.0,  # smooth path never fails
            "bills": [],
            "pv_total": 0.0,
            "pv_per_trust": 0.0,
            "per_trust_typical_2060s": 0.0,
            "securities_if_distributed_now": NON_MUEL_TOTAL * share,
        }

    for year in range(int(exhaust), through_year + 1):
        i = year - start
        opex = burn0 * ((1.0 + inflation) ** i)
        # real (today's $)
        real = opex / ((1.0 + inflation) ** i)
        bills.append((year, real, real * share))

    # PV of nominal bills at discount rate (use nominal opex)
    pv = 0.0
    for year in range(int(exhaust), through_year + 1):
        i = year - start
        opex = burn0 * ((1.0 + inflation) ** i)
        t = year - start
        pv += opex / ((1.0 + discount) ** t)

    sixties = [b for b in bills if 2060 <= b[0] <= 2069]
    typical_60 = float(np.mean([b[2] for b in sixties])) if sixties else 0.0

    return {
        "exhaust_year": int(exhaust),
        "chance_proxy": 1.0,  # on this smooth path, bills do start
        "bills": bills,
        "pv_total": pv,
        "pv_per_trust": pv * share,
        "per_trust_typical_2060s": typical_60,
        "securities_if_distributed_now": NON_MUEL_TOTAL * share,
        "n_trusts": n_trusts,
    }


def muel_snapshot() -> Dict:
    return {
        "shares": MUEL_SHARES,
        "mv": MUEL_MV,
        "eai": MUEL_EAI,
        "yield": MUEL_EAI / MUEL_MV if MUEL_MV else 0.0,
        "note": "MUEL stays outside the non-MUEL reserve model in Phase 3 (same as IC engine).",
    }


def exit_illustration(
    land_share_paid: float,
    reserve_share: float,
    land_value: float,
    reserve_mv: float,
) -> Dict:
    """Illustrative one-branch exit math (not legal advice)."""
    land_check = land_value * land_share_paid
    reserve_check = reserve_mv * reserve_share
    return {
        "land_check": land_check,
        "reserve_check": reserve_check,
        "total_to_exiting": land_check + reserve_check,
        "reserve_after": reserve_mv - reserve_check,
    }
