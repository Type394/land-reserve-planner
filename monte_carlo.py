"""Monte Carlo helpers for Land Reserve Planner Phase 2."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np

from endowment_engine import (
    Assumptions,
    Sleeve,
    run_forecast,
    scaled_sleeve,
    years_of_support,
)


def draw_returns(
    n_years: int,
    mu: float,
    sigma: float,
    rng: np.random.Generator,
) -> List[float]:
    """Independent annual total returns ~ Normal(mu, sigma), clipped to [-0.5, 0.8]."""
    raw = rng.normal(loc=mu, scale=sigma, size=n_years)
    return [float(np.clip(x, -0.50, 0.80)) for x in raw]


def path_survives(
    sleeve: Sleeve,
    burn: float,
    returns: List[float],
    horizon_years: int,
    base: Optional[Assumptions] = None,
) -> bool:
    kwargs = dict(
        market_return=0.08,
        opex0=burn,
        years=horizon_years,
        annual_returns=returns[:horizon_years],
        name="mc",
    )
    if base is not None:
        kwargs.update(
            inflation=base.inflation,
            div_growth=base.div_growth,
            income_tax=base.income_tax,
            pretax_y1=base.pretax_y1,
            cg_tax=base.cg_tax,
            extra_costs_by_year=base.extra_costs_by_year,
        )
    df = run_forecast(sleeve, Assumptions(**kwargs))
    return years_of_support(df) >= horizon_years


def funding_odds(
    starting_mv: float,
    burn: float,
    horizon_years: int,
    mu: float = 0.08,
    sigma: float = 0.17,
    n_paths: int = 500,
    seed: int = 42,
    base: Optional[Assumptions] = None,
) -> Dict:
    rng = np.random.default_rng(seed)
    sleeve0 = scaled_sleeve(starting_mv)
    years_needed = horizon_years
    alive = 0
    exhaust_years: List[Optional[int]] = []
    for _ in range(n_paths):
        rets = draw_returns(years_needed + 5, mu, sigma, rng)
        kwargs = dict(
            market_return=mu,
            opex0=burn,
            years=years_needed + 5,
            annual_returns=rets,
            name="mc",
        )
        if base is not None:
            kwargs.update(
                inflation=base.inflation,
                div_growth=base.div_growth,
                income_tax=base.income_tax,
                pretax_y1=base.pretax_y1,
                cg_tax=base.cg_tax,
                extra_costs_by_year=base.extra_costs_by_year,
            )
        df = run_forecast(sleeve0, Assumptions(**kwargs))
        yrs = years_of_support(df)
        if yrs >= horizon_years:
            alive += 1
            exhaust_years.append(None)
        else:
            exhaust_years.append(df.attrs.get("exhausted_year"))
    return {
        "odds": alive / n_paths,
        "alive": alive,
        "n_paths": n_paths,
        "horizon_years": horizon_years,
    }


def min_endowment_for_odds(
    burn: float,
    horizon_years: int,
    target_odds: float = 0.90,
    mu: float = 0.08,
    sigma: float = 0.17,
    n_paths: int = 200,
    seed: int = 42,
    tol: float = 100_000.0,
    base: Optional[Assumptions] = None,
) -> float:
    """Binary search starting MV so Monte Carlo survival odds >= target_odds."""
    lo, hi = 1_000_000.0, 80_000_000.0

    def ok(amt: float) -> bool:
        return (
            funding_odds(
                amt, burn, horizon_years, mu, sigma, n_paths, seed, base
            )["odds"]
            >= target_odds
        )

    if not ok(hi):
        return float("nan")
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


def min_endowment_real_preserving(
    burn: float,
    market_return: float,
    target_years: int = 50,
    inflation: float = 0.025,
    tol: float = 25_000.0,
    base: Optional[Assumptions] = None,
) -> float:
    """Smallest start MV whose real net endowment at target_years >= start MV."""
    lo, hi = 1_000_000.0, 120_000_000.0

    def ok(amt: float) -> bool:
        kwargs = dict(
            market_return=market_return,
            opex0=burn,
            years=target_years + 2,
            inflation=inflation,
            name="C",
        )
        if base is not None:
            kwargs.update(
                div_growth=base.div_growth,
                income_tax=base.income_tax,
                pretax_y1=base.pretax_y1,
                cg_tax=base.cg_tax,
                extra_costs_by_year=base.extra_costs_by_year,
            )
        df = run_forecast(scaled_sleeve(amt), Assumptions(**kwargs))
        if years_of_support(df) < target_years:
            return False
        end_year = df["Year"].iloc[0] + target_years - 1
        row = df.loc[df["Year"] == end_year]
        if row.empty:
            return False
        net = float(row["Net_Endowment"].iloc[0])
        real = net / ((1.0 + inflation) ** target_years)
        return real + 1.0 >= amt

    if not ok(hi):
        return float("nan")
    while hi - lo > tol:
        mid = (lo + hi) / 2.0
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi
