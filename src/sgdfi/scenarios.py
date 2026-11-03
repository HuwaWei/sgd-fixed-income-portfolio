"""6M scenario analysis: full revaluation plus carry / roll / duration / curve / spread attribution."""
from __future__ import annotations

from dataclasses import dataclass, replace
import numpy as np

from . import market as m
from .bond import Bond, BP
from .curve import Curve
from .portfolio import Portfolio


@dataclass(frozen=True)
class Scenario:
    name: str
    narrative: str
    curve_bp: dict      # par-yield shock per input tenor (bp)
    spread_bp: dict     # spread shock per credit sector (bp)

    def shocks(self) -> np.ndarray:
        return np.array([self.curve_bp[t] for t in m.SGS_TENORS], float)

    def key_shocks(self) -> np.ndarray:
        return np.array([self.curve_bp[k] for k in m.KEY_RATE_TENORS], float)


def _c(*v):
    return dict(zip(m.SGS_TENORS, v))


# Tenors:          6M   1Y   2Y   5Y  10Y  15Y  20Y  30Y  50Y
SCENARIOS = [
    Scenario("Bull", "Fed pauses after one hike; UST 10Y back to ~4.4%; SGS bull-flattens",
             _c(-15, -20, -30, -40, -45, -45, -45, -45, -45),
             {"Statutory board": -3, "Bank senior": -10, "Corporate/REIT": -15}),
    Scenario("Base", "Central view: MAS anchors front end, global term premium lifts SGS long end",
             _c(0, 0, -5, 0, 10, 15, 18, 20, 20),
             {"Statutory board": 0, "Bank senior": 0, "Corporate/REIT": 0}),
    Scenario("Bear", "Two more Fed hikes; UST 10Y ~5.5%; SGS bear-steepens",
             _c(15, 20, 25, 40, 55, 60, 65, 70, 70),
             {"Statutory board": 5, "Bank senior": 15, "Corporate/REIT": 25}),
    Scenario("Credit stress", "Risk-off: flight to SGS quality, Asian credit reprices",
             _c(-10, -15, -25, -30, -30, -25, -25, -25, -25),
             {"Statutory board": 15, "Bank senior": 60, "Corporate/REIT": 120}),
]


def bond_return(b: Bond, curve: Curve, sc: Scenario | None, h: float = m.HORIZON_YEARS) -> float:
    """Total return over h by full revaluation (coupons received, not reinvested)."""
    p0 = b.dirty(curve)
    c1 = curve.shifted(sc.shocks()) if sc else curve
    ds = sc.spread_bp.get(b.sector, 0.0) * BP if (sc and b.is_credit) else 0.0
    return (b.dirty(c1, h, ds) + b.coupons_paid(h)) / p0 - 1.0


def bond_attribution(b: Bond, curve: Curve, sc: Scenario, h: float = m.HORIZON_YEARS) -> dict:
    """Decompose the scenario return; residual = full revaluation minus the linear terms."""
    y = b.ytm(curve)
    static = bond_return(b, curve, None, h)
    # Shocks hit the aged bond at the horizon: use horizon risk, scaled by P_h / P_0.
    aged = replace(b, maturity=b.maturity - h)
    scale = aged.dirty(curve) / b.dirty(curve)
    krd = aged.key_rate_durations(curve, m.KEY_RATE_TENORS) * scale
    dy = sc.key_shocks() * BP
    dy_par = dy.mean()
    ds = sc.spread_bp.get(b.sector, 0.0) * BP if b.is_credit else 0.0
    out = {
        "carry": y * h,
        "roll": static - y * h,
        "duration": -krd.sum() * dy_par,
        "curve": -float(krd @ (dy - dy_par)),
        "spread": -aged.spread_duration(curve) * scale * ds,
    }
    full = bond_return(b, curve, sc, h)
    out["residual"] = full - sum(out.values())
    out["total"] = full
    return out


def portfolio_attribution(p: Portfolio, curve: Curve, sc: Scenario) -> dict:
    keys = ["carry", "roll", "duration", "curve", "spread", "residual", "total"]
    acc = dict.fromkeys(keys, 0.0)
    for b, w in p.holdings:
        a = bond_attribution(b, curve, sc)
        for k in keys:
            acc[k] += w * a[k]
    return acc


def scenario_table(port: Portfolio, bench: Portfolio, curve: Curve) -> list:
    rows = []
    for sc in SCENARIOS:
        ap, ab = portfolio_attribution(port, curve, sc), portfolio_attribution(bench, curve, sc)
        rows.append({"scenario": sc, "port": ap, "bench": ab,
                     "active": {k: ap[k] - ab[k] for k in ap}})
    return rows
