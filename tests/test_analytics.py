import numpy as np
import pytest

from sgdfi import market as m
from sgdfi.bond import Bond
from sgdfi.curve import Curve, key_rate_profile
from sgdfi.portfolio import (Portfolio, build_benchmark, build_curve, build_model, sgs,
                             tracking_error)
from sgdfi.scenarios import SCENARIOS, Scenario, bond_attribution, portfolio_attribution


@pytest.fixture(scope="module")
def curve():
    return build_curve()


@pytest.fixture(scope="module")
def bench(curve):
    return build_benchmark(curve)


@pytest.fixture(scope="module")
def model(curve):
    return build_model(curve)


# ---- curve ----
@pytest.mark.parametrize("tenor", m.SGS_TENORS)
def test_par_bonds_reprice_to_par(curve, tenor):
    b = Bond("x", curve.par_yield(tenor), float(tenor), "SGS", "AAA")
    assert b.dirty(curve) == pytest.approx(100.0, abs=1e-8)


def test_ytm_of_par_bond_equals_par_yield(curve):
    for t in m.BENCHMARK_TENORS:
        assert sgs(curve, t).ytm(curve) == pytest.approx(curve.par_yield(t), abs=1e-9)


def test_key_rate_profiles_sum_to_parallel():
    total = sum(key_rate_profile(k, m.SGS_TENORS, m.KEY_RATE_TENORS) for k in m.KEY_RATE_TENORS)
    assert np.allclose(total, 1.0)


# ---- bond risk ----
def test_duration_matches_analytic_modified_duration():
    flat = Curve.from_par(m.SGS_TENORS, [2.0] * len(m.SGS_TENORS))  # par shift == yield shift
    b = Bond("x", 0.02, 10.0, "SGS", "AAA")
    y = b.ytm(flat)
    t, cf = b.cashflows()
    pv = cf * (1 + y / 2) ** (-2 * t)
    mod_dur = (t * pv).sum() / pv.sum() / (1 + y / 2)
    assert b.eff_duration(flat) == pytest.approx(mod_dur, rel=1e-4)


def test_krds_sum_to_effective_duration(curve, model):
    for b in model.bonds:
        assert b.key_rate_durations(curve, m.KEY_RATE_TENORS).sum() == pytest.approx(b.eff_duration(curve), rel=1e-3)


def test_convexity_positive_and_spread_duration_only_for_credit(curve, model):
    for b in model.bonds:
        assert b.eff_convexity(curve) > 0
        assert (b.spread_duration(curve) > 0) == b.is_credit


def test_accrued_and_clean_price(curve):
    hdb = Bond("HDB", 0.0346, 4.56, "Statutory board", "AAA", 0.0018)
    # 4.56y: next coupon in 0.06y -> 0.44y of a 0.5y period accrued
    assert hdb.accrued() == pytest.approx(1.73 * 0.44 / 0.5, rel=1e-6)
    assert hdb.clean(curve) == pytest.approx(hdb.dirty(curve) - hdb.accrued())


# ---- portfolios ----
def test_benchmark_is_duration_weighted(curve, bench):
    assert bench.weights.sum() == pytest.approx(1.0)
    contrib = [w * b.eff_duration(curve) for b, w in bench.holdings]
    assert np.allclose(contrib, contrib[0])


def test_model_positioning_matches_stated_view(curve, model, bench):
    assert model.weights.sum() == pytest.approx(1.0)
    active = model.krd(curve) - bench.krd(curve)
    k = dict(zip(m.KEY_RATE_TENORS, active))
    assert -0.75 < model.duration(curve) - bench.duration(curve) < 0     # modest underweight
    assert k[5] > 0 and k[20] < 0 and k[30] < 0                         # long belly, short long end
    assert model.credit_weight() > 0.25 and model.ytm(curve) > bench.ytm(curve)


def test_tracking_error_zero_for_benchmark_and_within_budget(curve, bench, model):
    assert tracking_error(bench, bench, curve)["te_bp"] == pytest.approx(0.0, abs=1e-9)
    te = tracking_error(model, bench, curve)
    assert 0 < te["te_bp"] < 100  # risk budget: 100bp p.a.


# ---- scenarios & attribution ----
ZERO = Scenario("flat", "", dict.fromkeys(m.SGS_TENORS, 0.0), dict.fromkeys(m.SECTORS, 0.0))


def test_static_scenario_is_carry_plus_roll(curve, model):
    a = portfolio_attribution(model, curve, ZERO)
    assert a["duration"] == a["curve"] == a["spread"] == 0
    assert a["residual"] == pytest.approx(0.0, abs=1e-12)


def test_roll_down_on_flat_curve_is_zero():
    flat = Curve.from_par(m.SGS_TENORS, [2.0] * len(m.SGS_TENORS))
    b = Bond("x", 0.02, 10.0, "SGS", "AAA")
    a = bond_attribution(b, flat, ZERO)
    assert a["roll"] == pytest.approx(0.0, abs=2e-5)


def test_attribution_residual_small_for_small_shock(curve, model):
    small = Scenario("p5", "", dict.fromkeys(m.SGS_TENORS, 5.0), dict.fromkeys(m.SECTORS, 5.0))
    a = portfolio_attribution(model, curve, small)
    assert abs(a["residual"]) < 0.5e-4  # < 0.5bp


def test_scenario_directions(curve, model, bench):
    res = {s.name: (portfolio_attribution(model, curve, s), portfolio_attribution(bench, curve, s)) for s in SCENARIOS}
    assert res["Bull"][1]["total"] > 0 > res["Bear"][1]["total"]
    assert res["Bear"][0]["total"] > res["Bear"][1]["total"]           # underweight helps in sell-off
    assert res["Credit stress"][0]["total"] < res["Credit stress"][1]["total"]  # credit OW hurts in stress


def test_tracking_error_decomposition_identity(curve, bench, model):
    te = tracking_error(model, bench, curve)
    # total^2 = rates^2 + spread^2 + cross-term; sign of cross-term drives the commentary wording
    assert te["te_bp"] ** 2 == pytest.approx(te["rates_only_bp"] ** 2 + te["spread_only_bp"] ** 2 + te["cross_term_bp2"])
    assert te["uncorrelated_bp"] ** 2 == pytest.approx(te["rates_only_bp"] ** 2 + te["spread_only_bp"] ** 2)
    # short duration + long spread with negative spread/rate correlation: cross-term adds risk
    assert te["cross_term_bp2"] > 0 and te["te_bp"] > te["uncorrelated_bp"]
    assert te["te_6m_bp"] == pytest.approx(te["te_bp"] / np.sqrt(2))


# ---- trade levels and spread calibration ----
def test_trade_levels_are_coherent():
    from sgdfi.report import compute
    r = compute()
    cu = r["levels"]["curve"]
    assert cu["entry_bp"] == pytest.approx((dict(zip(m.SGS_TENORS, m.SGS_YIELDS))[30] - dict(zip(m.SGS_TENORS, m.SGS_YIELDS))[5]) * 100)
    assert cu["stop_bp"] < cu["entry_bp"] < cu["target_bp"]                                   # steepener: profit if 5s30s widens
    assert (cu["target_bp"] - cu["entry_bp"]) == pytest.approx(2 * (cu["entry_bp"] - cu["stop_bp"]))  # 2:1 reward/risk
    assert abs(cu["dv01_5y"] + cu["dv01_20_30y"]) < 0.05 * cu["dv01_5y"]                     # legs DV01-matched within 5%
    assert r["levels"]["duration"]["target_pct"] > r["levels"]["duration"]["entry_pct"]      # underweight profits if yields rise


def test_statutory_board_spreads_within_observed_comparable_range(curve):
    from sgdfi.portfolio import credit_bonds
    lo, hi = m.HDB_4Y_IMPLIED_SPREAD_BP
    hdb = next(b for b in credit_bonds(curve) if b.name.startswith("HDB"))
    assert lo <= hdb.spread * 1e4 <= hi
