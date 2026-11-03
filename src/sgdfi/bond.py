"""Fixed-coupon bond pricing and risk off the SGS curve plus a z-spread."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from .curve import Curve, key_rate_profile

BP = 1e-4


@dataclass(frozen=True)
class Bond:
    name: str
    coupon: float        # decimal, semi-annual
    maturity: float      # years from valuation date
    sector: str          # "SGS" or a credit sector
    rating: str
    spread: float = 0.0  # z-spread over SGS zero curve, decimal
    note: str = ""

    @property
    def is_credit(self) -> bool:
        return self.sector != "SGS"

    def cashflows(self, h: float = 0.0):
        """Cash-flow times (from horizon h) and amounts per 100 face for flows after h."""
        n = int(np.floor((self.maturity - h) / 0.5 + 1e-9)) + 1
        times = self.maturity - 0.5 * np.arange(n)[::-1]
        times = times[times > h + 1e-9] - h
        amts = np.full(times.shape, 100 * self.coupon / 2)
        amts[-1] += 100
        return times, amts

    def coupons_paid(self, h: float) -> float:
        n = int(np.floor((self.maturity) / 0.5 + 1e-9)) + 1
        times = self.maturity - 0.5 * np.arange(n)
        paid = times[(times > 1e-9) & (times <= h + 1e-9)]
        return 100 * self.coupon / 2 * len(paid)

    def dirty(self, curve: Curve, h: float = 0.0, dspread: float = 0.0) -> float:
        t, cf = self.cashflows(h)
        return float((cf * curve.df(t, self.spread + dspread)).sum())

    def accrued(self, h: float = 0.0) -> float:
        t, _ = self.cashflows(h)
        return 100 * self.coupon / 2 * (0.5 - t[0]) / 0.5

    def clean(self, curve: Curve, h: float = 0.0) -> float:
        return self.dirty(curve, h) - self.accrued(h)

    def ytm(self, curve: Curve, h: float = 0.0, dspread: float = 0.0) -> float:
        target = self.dirty(curve, h, dspread)
        t, cf = self.cashflows(h)
        lo, hi = -0.05, 0.50
        for _ in range(200):
            mid = 0.5 * (lo + hi)
            p = (cf * (1 + mid / 2) ** (-2 * t)).sum()
            lo, hi = (mid, hi) if p > target else (lo, mid)
        return 0.5 * (lo + hi)

    # --- risk (effective measures, central differences) ---
    def eff_duration(self, curve: Curve) -> float:
        p0 = self.dirty(curve)
        up, dn = self.dirty(curve.shifted(1)), self.dirty(curve.shifted(-1))
        return (dn - up) / (2 * p0 * BP)

    def eff_convexity(self, curve: Curve, bp: float = 10) -> float:
        p0 = self.dirty(curve)
        up, dn = self.dirty(curve.shifted(bp)), self.dirty(curve.shifted(-bp))
        return (up + dn - 2 * p0) / (p0 * (bp * BP) ** 2)

    def spread_duration(self, curve: Curve) -> float:
        if not self.is_credit:
            return 0.0
        p0 = self.dirty(curve)
        up, dn = self.dirty(curve, dspread=BP), self.dirty(curve, dspread=-BP)
        return (dn - up) / (2 * p0 * BP)

    def key_rate_durations(self, curve: Curve, keys) -> np.ndarray:
        p0 = self.dirty(curve)
        out = []
        for k in keys:
            prof = key_rate_profile(k, curve.tenors, keys)
            up, dn = self.dirty(curve.shifted(prof)), self.dirty(curve.shifted(-prof))
            out.append((dn - up) / (2 * p0 * BP))
        return np.array(out)


def par_bond(name: str, curve: Curve, tenor: float, sector="SGS", rating="AAA", spread=0.0, note="") -> Bond:
    """Bond whose coupon is set to price at par (SGS) or near par (credit) on the curve."""
    c = curve.par_yield(tenor)
    if spread:
        lo, hi = 0.0, 0.2
        for _ in range(100):
            mid = 0.5 * (lo + hi)
            p = Bond(name, mid, tenor, sector, rating, spread).dirty(curve)
            lo, hi = (mid, hi) if p < 100 else (lo, mid)
        c = round(0.5 * (lo + hi) * 8000) / 8000  # nearest 1/80 of 1% (1.25bp)
    return Bond(name, c, float(tenor), sector, rating, spread, note)
