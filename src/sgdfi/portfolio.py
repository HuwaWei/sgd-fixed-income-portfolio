"""Benchmark and model portfolio construction, risk vs benchmark, ex-ante tracking error."""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np

from . import market as m
from .bond import Bond, par_bond
from .curve import Curve


@dataclass
class Portfolio:
    name: str
    holdings: list  # list[(Bond, weight)] — market-value weights summing to 1

    @property
    def weights(self) -> np.ndarray:
        return np.array([w for _, w in self.holdings])

    @property
    def bonds(self) -> list:
        return [b for b, _ in self.holdings]

    def _wavg(self, f) -> float:
        return float(sum(w * f(b) for b, w in self.holdings))

    def ytm(self, c: Curve) -> float:
        return self._wavg(lambda b: b.ytm(c))

    def duration(self, c: Curve) -> float:
        return self._wavg(lambda b: b.eff_duration(c))

    def convexity(self, c: Curve) -> float:
        return self._wavg(lambda b: b.eff_convexity(c))

    def spread_duration(self, c: Curve) -> float:
        return self._wavg(lambda b: b.spread_duration(c))

    def spread_duration_by_sector(self, c: Curve) -> dict:
        return {s: float(sum(w * b.spread_duration(c) for b, w in self.holdings if b.sector == s))
                for s in m.SECTORS}

    def krd(self, c: Curve) -> np.ndarray:
        return sum(w * b.key_rate_durations(c, m.KEY_RATE_TENORS) for b, w in self.holdings)

    def credit_weight(self) -> float:
        return float(sum(w for b, w in self.holdings if b.is_credit))

    def avg_spread_bp(self) -> float:
        return float(sum(w * b.spread for b, w in self.holdings) * 1e4)

    def dv01_sgd(self, c: Curve, aum: float = m.AUM_SGD) -> float:
        return self.duration(c) * aum * 1e-4


def build_curve() -> Curve:
    return Curve.from_par(m.SGS_TENORS, m.SGS_YIELDS)


def sgs(curve: Curve, tenor: int) -> Bond:
    return par_bond(f"SGS {tenor}Y benchmark", curve, tenor, note="par bond at MAS benchmark yield, 30 Oct 2026")


def build_benchmark(curve: Curve) -> Portfolio:
    """Duration-weighted SGS 2Y-30Y: each line contributes equal duration (w_i ∝ 1/D_i)."""
    bonds = [sgs(curve, t) for t in m.BENCHMARK_TENORS]
    inv = np.array([1.0 / b.eff_duration(curve) for b in bonds])
    w = inv / inv.sum()
    return Portfolio("Benchmark: SGS 2Y-30Y (duration-weighted)", list(zip(bonds, w)))


# Credit sleeve. There is no public SGD IG spread curve for 30 Oct 2026. Statutory-board spreads are calibrated to
# HDB's S$1.2bn 4Y green bond priced at 2.586% in Sep 2026 (OCBC Monthly Credit View, 6 Oct 2026), which implies
# 35-69bp over interpolated SGS 4Y across September (MAS); 40bp is used, +7bp for the 10Y line as before.
# Bank, GLC and REIT spreads are illustrative assumptions. Notes tagged ILLUSTRATIVE get a dagger in the holdings table.
CREDIT_UNIVERSE = [
    # name, coupon (None => priced near par), maturity, sector, rating, spread bp, note
    ("HDB 3.46% 21-May-2031", 0.0346, 4.56, "Statutory board", "AAA",
     40, "coupon/maturity per HDB pricing release (21 May 2024); spread ILLUSTRATIVE, calibrated to HDB 4Y Sep-2026 print"),
    ("Statutory board AAA 10Y", None, 10, "Statutory board", "AAA",
     47, "ILLUSTRATIVE instrument; spread calibrated to HDB 4Y Sep-2026 print +7bp"),
    ("SG bank senior AA- 5Y", None, 5, "Bank senior", "AA-",
     55, "ILLUSTRATIVE instrument and spread"),
    ("SG GLC corporate A 7Y", None, 7, "Corporate/REIT", "A",
     85, "ILLUSTRATIVE instrument and spread"),
    ("S-REIT BBB+ 5Y", None, 5, "Corporate/REIT", "BBB+",
     125, "ILLUSTRATIVE instrument and spread"),
]

# Model portfolio weights (market value). Rationale in README / commentary.
MODEL_WEIGHTS = {
    "SGS 2Y benchmark": 0.360,
    "SGS 5Y benchmark": 0.130,
    "SGS 10Y benchmark": 0.060,
    "SGS 15Y benchmark": 0.060,
    "SGS 20Y benchmark": 0.035,
    "SGS 30Y benchmark": 0.025,
    "HDB 3.46% 21-May-2031": 0.060,
    "Statutory board AAA 10Y": 0.040,
    "SG bank senior AA- 5Y": 0.100,
    "SG GLC corporate A 7Y": 0.080,
    "S-REIT BBB+ 5Y": 0.050,
}


def credit_bonds(curve: Curve, spread_bp: dict | None = None) -> list:
    """Credit universe; `spread_bp` optionally overrides spreads by name (used for sensitivity only)."""
    out = []
    for name, cpn, mat, sec, rtg, s_bp, note in CREDIT_UNIVERSE:
        s_bp = (spread_bp or {}).get(name, s_bp)
        if cpn is None:
            out.append(par_bond(name, curve, mat, sec, rtg, s_bp * 1e-4, note))
        else:
            out.append(Bond(name, cpn, mat, sec, rtg, s_bp * 1e-4, note))
    return out


def build_model(curve: Curve, weights: dict = MODEL_WEIGHTS, spread_bp: dict | None = None) -> Portfolio:
    universe = {b.name: b for b in [sgs(curve, t) for t in m.BENCHMARK_TENORS] + credit_bonds(curve, spread_bp)}
    assert abs(sum(weights.values()) - 1) < 1e-9, "model weights must sum to 1"
    return Portfolio("Model portfolio", [(universe[n], w) for n, w in weights.items()])


# ---------------- ex-ante tracking error ----------------
def factor_covariance() -> tuple[list, np.ndarray]:
    """Annualised covariance of factor moves (decimal yield/spread units). ASSUMPTION-driven."""
    keys = m.KEY_RATE_TENORS
    sectors = list(m.SECTORS)
    names = [f"KR{k}Y" for k in keys] + [f"SPR:{s}" for s in sectors]
    vol = np.array([m.RATE_VOL_BP[k] for k in keys] + [m.SECTORS[s]["spread_vol_bp"] for s in sectors]) * 1e-4
    n, nk = len(names), len(keys)
    corr = np.eye(n)
    for i in range(nk):
        for j in range(nk):
            corr[i, j] = np.exp(-m.RATE_CORR_DECAY * abs(keys[i] - keys[j]))
    for i in range(nk, n):
        for j in range(nk, n):
            if i != j:
                corr[i, j] = m.SPREAD_SPREAD_CORR
        for j in range(nk):
            corr[i, j] = corr[j, i] = m.SPREAD_RATE_CORR
    return names, np.outer(vol, vol) * corr


def tracking_error(port: Portfolio, bench: Portfolio, curve: Curve) -> dict:
    """TE = sqrt(x' Σ x + Σ_i (w_i SD_i σ_idio,i)^2), x = active factor durations."""
    names, cov = factor_covariance()
    sd_p, sd_b = port.spread_duration_by_sector(curve), bench.spread_duration_by_sector(curve)
    x = np.concatenate([port.krd(curve) - bench.krd(curve),
                        [sd_p[s] - sd_b[s] for s in m.SECTORS]])
    systematic = float(x @ cov @ x)
    idio = sum((w * b.spread_duration(curve) * m.SECTORS[b.sector]["idio_vol_bp"] * 1e-4) ** 2
               for b, w in port.holdings if b.is_credit)
    total = np.sqrt(systematic + idio)
    nk = len(m.KEY_RATE_TENORS)
    xr = x.copy(); xr[nk:] = 0
    xs = x.copy(); xs[:nk] = 0
    rates_var, spread_var = float(xr @ cov @ xr), float(xs @ cov @ xs) + idio
    return {
        "te_bp": total * 1e4,
        "te_6m_bp": total * np.sqrt(0.5) * 1e4,  # square-root-of-time scaling to the 6M scenario horizon
        "rates_only_bp": np.sqrt(rates_var) * 1e4,
        "spread_only_bp": np.sqrt(spread_var) * 1e4,
        "idio_bp": np.sqrt(idio) * 1e4,
        # TE if rates and spread factors were uncorrelated; > te_bp means the cross-term diversifies, < means it adds risk
        "uncorrelated_bp": np.sqrt(rates_var + spread_var) * 1e4,
        "cross_term_bp2": 2 * float(xr @ cov @ xs) * 1e8,
        "exposures": dict(zip(names, x)),
    }
