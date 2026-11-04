import datetime as dt

import numpy as np
import pytest

from sgdfi import history as hist
from sgdfi import market as m
from sgdfi.portfolio import build_benchmark, build_curve, build_model


@pytest.fixture(scope="module")
def real():
    return hist.History()


def flat_history(level=2.0, days=(0, 91, 182)):
    d0 = dt.date(2025, 1, 2)
    dates = np.array([d0 + dt.timedelta(days=x) for x in days])
    return hist.History(dates, np.full((len(dates), len(m.SGS_TENORS)), level))


# ---- data ----
def test_history_loads_three_years_and_ends_on_snapshot(real):
    assert real.dates[0] <= dt.date(2023, 10, 31) and real.dates[-1] == dt.date(2026, 10, 30)
    assert len(real.dates) > 700 and np.isfinite(real.y).all()
    assert np.allclose(real.y[-1], m.SGS_YIELDS)  # history and snapshot are the same MAS series


def test_history_matches_prior_month_end_input(real):
    i = list(real.dates).index(dt.date(2026, 9, 30))
    assert real.col(10)[i] == pytest.approx(m.SGS10Y_PREV)


def test_level_stats_zscore():
    x = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    dates = np.array([dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(5)])
    s = hist.level_stats(x, dates)
    assert s["mean"] == pytest.approx(3.0)
    assert s["z"] == pytest.approx(2.0 / np.std(x, ddof=1))
    assert s["pct_rank"] == pytest.approx(80.0)


def test_windows_hold_at_least_six_months(real):
    w = real.windows()
    assert all((real.dates[k] - real.dates[i]).days >= hist.HOLD_DAYS for i, k in w)
    assert all((real.dates[k] - real.dates[i]).days <= hist.HOLD_DAYS + 10 for i, k in w)  # dropped rollover days


# ---- positions ----
def test_duration_underweight_has_target_active_duration():
    c = build_curve()
    legs = hist.DurationUnderweight(-0.35).legs(c)
    assert sum(w * b.eff_duration(c) for b, w in legs) == pytest.approx(-0.35, abs=1e-9)


def test_steepener_is_dv01_matched_at_target_sizes():
    c = build_curve()
    sizes = {5: 8e-5, 20: -4e-5, 30: -4e-5}
    legs = hist.Steepener(sizes).legs(c)
    dv01 = [w * b.eff_duration(c) * 1e-4 for b, w in legs]
    assert dv01 == pytest.approx(list(sizes.values()), rel=1e-9)
    assert sum(dv01) == pytest.approx(0.0, abs=1e-12)


def test_rates_book_matches_model_rate_exposure():
    c = build_curve()
    legs = hist.RatesBook().legs(c)
    assert sum(w for _, w in legs) == pytest.approx(0.0, abs=1e-12)  # fully invested vs benchmark
    active = sum(w * b.eff_duration(c) for b, w in legs)
    model, bench = build_model(c), build_benchmark(c)
    # Credit lines swapped for SGS keep (almost) the same rate duration as in the model.
    assert active == pytest.approx(model.duration(c) - bench.duration(c), abs=0.05)


def test_flat_unchanged_curve_gives_near_zero_active_return():
    h = flat_history()
    for pos in (hist.DurationUnderweight(-0.35), hist.Steepener({5: 8e-5, 20: -4e-5, 30: -4e-5}), hist.RatesBook()):
        path = pos.path(h, 0, 2)
        assert abs(path[-1]) * 1e4 < 0.5, pos.name


def test_duration_underweight_gains_when_yields_rise():
    h = flat_history()
    h.y[1:] += 0.5   # +50 bp parallel after the start date
    assert hist.DurationUnderweight(-0.35).path(h, 0, 2)[-1] > 0


def test_backtest_summary_is_consistent(real):
    t = hist.backtest(hist.DurationUnderweight(-0.35), real, real.col(10))
    x = np.array(t["returns_bp"])
    assert t["n"] == len(x) == len(real.windows())
    assert t["hit_rate"] == pytest.approx((x > 0).mean())
    assert t["worst_bp"] == pytest.approx(x.min())
    assert t["max_dd_bp"] <= min(t["worst_bp"], 0) + 1e-9
    assert 0 < t["like_today"]["n"] < t["n"]
    assert hist.verdict(t) in ("Supports", "Mild support", "Weakens")
