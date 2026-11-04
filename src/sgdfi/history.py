"""Test the active positions against three years of daily MAS SGS benchmark yields.

Data: data/sgs_benchmark_yields_history.csv (MAS "SGS Prices and Yields - Benchmark Issues", daily, refreshed by
scripts/fetch_mas_history.py). Days with any missing tenor (10 cells around benchmark rollovers) are dropped.

Each position is rebuilt on every start date from that day's curve, with today's sizing rule, held for 6M and
revalued in full on the curve observed at the end date (bond aged by the elapsed ACT/365 time, coupons received,
not reinvested). Active returns are in bp of fund value:

* Duration underweight: duration-weighted benchmark vs the same benchmark with enough moved into 6M bills to
  give today's active duration. Pure parallel/level exposure, carry included.
* 5s30s steepener: long 5Y, short 20Y and 30Y par SGS at today's active DV01 per S$ of fund (DV01-matched), with
  net cash at the 6M bill yield. Return = sum_i n_i (R_i - R_cash).
* Rates book: the model portfolio with each credit line replaced by an SGS par bond of the same maturity, vs the
  benchmark. This is every rates exposure in the portfolio (2Y/15Y underweights included), with no spread.

Credit is not testable: MAS publishes no SGD credit spreads, and there is no free daily SGD IG spread history.
Windows overlap (daily starts, 6M holding), so hit rates are not independent observations.
"""
from __future__ import annotations

import csv
import datetime as dt

import numpy as np

from . import market as m
from .bond import Bond
from .curve import Curve
from .portfolio import MODEL_WEIGHTS, CREDIT_UNIVERSE, build_benchmark, sgs

HOLD_DAYS = 182          # 6M holding period, calendar days (end = first trading day on/after start + 182d)
MARK_EVERY = 5           # mark-to-market every 5th trading day (and on the end date) for drawdowns
SOURCE = ("MAS, SGS Prices and Yields - Benchmark Issues (daily, average buying rates of govt securities "
          "dealers), https://eservices.mas.gov.sg/statistics/fdanet/BenchmarkPricesAndYields.aspx")


def load(path=m.DATA / "sgs_benchmark_yields_history.csv"):
    """Dates and an (n_days, n_tenors) array of yields in %, dropping days with a missing tenor."""
    with open(path, newline="") as f:
        rd = csv.reader(f)
        head = next(rd)
        tenors = [float(t) for t in head[1:]]
        dates, ys = [], []
        for row in rd:
            if any(not x.strip() for x in row[1:]):
                continue
            dates.append(dt.date.fromisoformat(row[0]))
            ys.append([float(x) for x in row[1:]])
    tenors = [int(t) if t.is_integer() else t for t in tenors]
    assert tenors == m.SGS_TENORS, "history tenors must match the snapshot curve"
    return np.array(dates), np.array(ys)


def level_stats(series: np.ndarray, dates: np.ndarray) -> dict:
    """Mean, sd and z-score of the last observation (today) against the full window; last-12M mean for context."""
    mu, sd = float(series.mean()), float(series.std(ddof=1))
    now = float(series[-1])
    last_year = dates > dates[-1] - dt.timedelta(days=365)
    return {"now": now, "mean": mu, "sd": sd, "z": (now - mu) / sd, "min": float(series.min()),
            "max": float(series.max()), "pct_rank": float((series < now).mean() * 100),
            "mean_12m": float(series[last_year].mean())}


def verdict(t: dict) -> str:
    """Plain-language read of a backtest: needs a positive average and a hit rate above 60% to 'support'."""
    if t["mean_bp"] > 0 and t["hit_rate"] >= 0.6:
        return "Supports"
    if t["mean_bp"] > 0:
        return "Mild support"
    return "Weakens"


class History:
    def __init__(self, dates=None, yields=None):
        if dates is None:
            dates, yields = load()
        self.dates, self.y = dates, yields
        self.idx = {t: i for i, t in enumerate(m.SGS_TENORS)}
        self._curves: dict[int, Curve] = {}

    def col(self, t) -> np.ndarray:
        return self.y[:, self.idx[t]]

    def curve(self, i: int) -> Curve:
        if i not in self._curves:
            self._curves[i] = Curve.from_par(m.SGS_TENORS, self.y[i])
        return self._curves[i]

    def windows(self) -> list[tuple[int, int]]:
        """(start, end) index pairs: end is the first day on/after start + HOLD_DAYS."""
        out, j = [], 0
        for i, d in enumerate(self.dates):
            target = d + dt.timedelta(days=HOLD_DAYS)
            while j < len(self.dates) and self.dates[j] < target:
                j += 1
            if j == len(self.dates):
                break
            out.append((i, j))
        return out

    def yf(self, i: int, k: int) -> float:
        return (self.dates[k] - self.dates[i]).days / 365.0


# ---------------- positions ----------------
def _ret(b: Bond, p0: float, c1: Curve, h: float) -> float:
    """Holding-period return of bond b bought at dirty price p0 and revalued on c1 after h years."""
    return (b.dirty(c1, h) + b.coupons_paid(h)) / p0 - 1.0


class Position:
    """A set of (bond, MV weight) longs/shorts per S$1 of fund, rebuilt on each start date."""
    name: str

    def legs(self, c0: Curve) -> list[tuple[Bond, float]]:
        raise NotImplementedError

    def path(self, h: History, i: int, k: int) -> np.ndarray:
        """Active return (decimal) marked every MARK_EVERY trading days from start i, and on end date k."""
        c0 = h.curve(i)
        legs = [(b, w, b.dirty(c0)) for b, w in self.legs(c0)]
        cash_y = c0.par_yield(0.5)
        net = sum(w for _, w, _ in legs)
        out = []
        for j in sorted(set(range(i, k + 1, MARK_EVERY)) | {k}):
            t = h.yf(i, j)
            r = sum(w * _ret(b, p0, h.curve(j), t) for b, w, p0 in legs)
            out.append(r - net * cash_y * t)  # net long MV financed at (net short MV invested in) 6M bills
        return np.array(out)


class DurationUnderweight(Position):
    """Benchmark less alpha of it switched into 6M bills: active duration = target (pure level trade)."""
    name = "Duration underweight"

    def __init__(self, active_dur: float):
        self.active_dur = active_dur

    def legs(self, c0):
        b = build_benchmark(c0)
        d = b.duration(c0)
        alpha = -self.active_dur / d   # fraction of the benchmark sold into bills
        return [(bo, -alpha * w) for bo, w in b.holdings]


class Steepener(Position):
    """DV01-matched: long 5Y, short 20Y and 30Y at today's active key-rate DV01 per S$ of fund."""
    name = "5s30s steepener"

    def __init__(self, dv01_frac: dict):
        self.dv01 = dv01_frac   # tenor -> DV01 per S$ of fund per bp (sign = long/short)

    def legs(self, c0):
        out = []
        for t, d in self.dv01.items():
            bo = sgs(c0, t)
            out.append((bo, d / (bo.eff_duration(c0) * 1e-4)))
        return out


class RatesBook(Position):
    """Model weights with credit lines swapped for same-maturity SGS par bonds, minus the benchmark."""
    name = "Rates book (credit as SGS)"

    def legs(self, c0):
        mats = {n: mat for n, _, mat, *_ in CREDIT_UNIVERSE}
        port = {}
        for n, w in MODEL_WEIGHTS.items():
            if n in mats:
                bo = Bond(n, c0.par_yield(mats[n]), float(mats[n]), "SGS", "AAA")
            else:
                bo = sgs(c0, int(n.split()[1].rstrip("Y")))
            port[bo.name] = (bo, w)
        legs = list(port.values())
        legs += [(bo, -w) for bo, w in build_benchmark(c0).holdings]
        return legs


def backtest(pos: Position, h: History, cond: np.ndarray | None = None) -> dict:
    """6M outcomes for every daily start. `cond` (one value per day): also report starts with cond <= today."""
    tot, dd, starts, sel = [], [], [], []
    for i, k in h.windows():
        if cond is not None:
            sel.append(cond[i] <= cond[-1] + 1e-9)
        p = pos.path(h, i, k) * 1e4
        tot.append(p[-1])
        dd.append(float((p - np.maximum.accumulate(np.maximum(p, 0.0))).min()))  # peak-to-trough, from start
        starts.append(h.dates[i])
    tot = np.array(tot)
    worst = int(tot.argmin())
    mask = np.array(sel, bool) if cond is not None else np.zeros(len(tot), bool)
    sub, sub_starts = tot[mask], np.array(starts)[mask]
    like_today = {"n": int(len(sub)), "hit_rate": float((sub > 0).mean()) if len(sub) else None,
                  "mean_bp": float(sub.mean()) if len(sub) else None,
                  "first": sub_starts[0].isoformat() if len(sub) else None,
                  "last": sub_starts[-1].isoformat() if len(sub) else None}
    return {"like_today": like_today,"name": pos.name, "n": len(tot), "hit_rate": float((tot > 0).mean()), "mean_bp": float(tot.mean()),
            "median_bp": float(np.median(tot)), "sd_bp": float(tot.std(ddof=1)),
            "worst_bp": float(tot.min()), "worst_start": starts[worst].isoformat(),
            "best_bp": float(tot.max()), "max_dd_bp": float(min(dd)),
            "starts": [d.isoformat() for d in starts], "returns_bp": tot.tolist()}


def realised_vol_bp(h: History) -> dict:
    """Annualised sd of daily par-yield changes, bp (sqrt(252) scaling), to compare with the TE assumptions."""
    return {t: float(np.diff(h.col(t)).std(ddof=1) * 100 * np.sqrt(252)) for t in m.KEY_RATE_TENORS}


def compute(r: dict, h: History | None = None) -> dict:
    """History tests for the positions in results dict `r` (from report.compute)."""
    h = h or History()
    # Today's history row must be the snapshot the portfolio is built on.
    assert h.dates[-1] == dt.date(2026, 10, 30) and np.allclose(h.y[-1], m.SGS_YIELDS), "history/snapshot mismatch"
    s5s30 = (h.col(30) - h.col(5)) * 100
    s10s30 = (h.col(30) - h.col(10)) * 100
    s2s5 = (h.col(5) - h.col(2)) * 100
    adv = np.array(r["krd"]["active_dv01_sgd"]) / m.AUM_SGD  # per S$ of fund
    keys = dict(zip(m.KEY_RATE_TENORS, adv))
    # "Like today": starts where the signal was at or below today's level (10Y yield; 5s30s slope).
    positions = [(DurationUnderweight(r["active"]["duration"]), h.col(10)),
                 (Steepener({5: keys[5], 20: keys[20], 30: keys[30]}), s5s30),
                 (RatesBook(), h.col(10))]
    tests = {p.name: backtest(p, h, cond) for p, cond in positions}
    for t in tests.values():
        t["verdict"] = verdict(t)
    # How often did 5s30s actually steepen by the base-case target move over 6M?
    move = np.array([s5s30[k] - s5s30[i] for i, k in h.windows()])
    tgt = r["levels"]["curve"]["target_bp"] - r["levels"]["curve"]["entry_bp"]
    stop = r["levels"]["curve"]["stop_bp"] - r["levels"]["curve"]["entry_bp"]
    s5s30_6m = {"mean_bp": float(move.mean()), "sd_bp": float(move.std(ddof=1)), "target_move_bp": float(tgt),
                "p_target": float((move >= tgt - 1e-9).mean()), "stop_move_bp": float(stop),
                "p_stop": float((move <= stop + 1e-9).mean())}
    vol = realised_vol_bp(h)
    return {
        "source": SOURCE, "start": h.dates[0].isoformat(), "end": h.dates[-1].isoformat(), "days": len(h.dates),
        "hold_days": HOLD_DAYS,
        "levels": {"sgs10y_pct": level_stats(h.col(10), h.dates), "sgs2y_pct": level_stats(h.col(2), h.dates),
                   "s5s30_bp": level_stats(s5s30, h.dates), "s10s30_bp": level_stats(s10s30, h.dates),
                   "s2s5_bp": level_stats(s2s5, h.dates)},
        "tests": tests, "s5s30_6m_change": s5s30_6m,
        "realised_vol_bp": vol, "assumed_vol_bp": dict(m.RATE_VOL_BP),
        "series": {"dates": [d.isoformat() for d in h.dates], "y2": h.col(2).tolist(), "y10": h.col(10).tolist(),
                   "y30": h.col(30).tolist(), "s5s30": s5s30.tolist()},
    }
