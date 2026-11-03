"""SGS zero curve bootstrapped from benchmark par yields (semi-annual coupons)."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

GRID_STEP = 0.5


@dataclass(frozen=True)
class Curve:
    tenors: tuple   # input par tenors (years)
    par: tuple      # input par yields (decimal)
    grid: np.ndarray
    zero: np.ndarray  # semi-annual compounded zero rates on grid

    @classmethod
    def from_par(cls, tenors, par_yields_pct) -> "Curve":
        tenors = np.asarray(tenors, float)
        par = np.asarray(par_yields_pct, float) / 100.0
        grid = np.arange(GRID_STEP, tenors[-1] + 1e-9, GRID_STEP)
        par_grid = np.interp(grid, tenors, par)  # linear in tenor, flat outside
        dfs = np.empty_like(grid)
        for i, c in enumerate(par_grid):
            annuity = dfs[:i].sum()
            dfs[i] = (1.0 - c / 2.0 * annuity) / (1.0 + c / 2.0)
        zero = 2.0 * (dfs ** (-1.0 / (2.0 * grid)) - 1.0)
        return cls(tuple(tenors), tuple(par), grid, zero)

    def shifted(self, shocks_bp) -> "Curve":
        """New curve with par yields shocked by `shocks_bp` (scalar or one per input tenor)."""
        shocks = np.broadcast_to(np.asarray(shocks_bp, float), (len(self.tenors),))
        new = (np.asarray(self.par) * 1e4 + shocks) / 100.0
        return Curve.from_par(self.tenors, new)

    def zero_rate(self, t):
        return np.interp(np.asarray(t, float), self.grid, self.zero)

    def df(self, t, spread: float = 0.0):
        t = np.asarray(t, float)
        return (1.0 + (self.zero_rate(t) + spread) / 2.0) ** (-2.0 * t)

    def par_yield(self, t: float) -> float:
        return float(np.interp(t, self.tenors, self.par))


def key_rate_profile(key: float, tenors, keys) -> np.ndarray:
    """1bp triangular key-rate bump on input tenors; ends are flat so bumps sum to a parallel shift."""
    keys = list(keys)
    i = keys.index(key)
    out = np.zeros(len(tenors))
    for j, t in enumerate(tenors):
        if t <= keys[0]:
            out[j] = 1.0 if i == 0 else 0.0
        elif t >= keys[-1]:
            out[j] = 1.0 if i == len(keys) - 1 else 0.0
        else:
            unit = np.zeros(len(keys)); unit[i] = 1.0
            out[j] = np.interp(t, keys, unit)
    return out
