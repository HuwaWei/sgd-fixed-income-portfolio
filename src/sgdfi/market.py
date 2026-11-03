"""Market inputs as at 30 Oct 2026 and model assumptions.

Market data are read from ``data/*.csv``, where every row carries its source and as-of date.
Parameters defined in this module (fund size, factor vols and correlations) are the author's
assumptions, used for sizing and risk only; they are listed in the README.
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"


def _num(x: str):
    v = float(x)
    return int(v) if v.is_integer() else v


def _read(name: str) -> list[dict]:
    with open(DATA / name, newline="") as f:
        return list(csv.DictReader(f))


AS_OF = "30 Oct 2026"
HORIZON_YEARS = 0.5          # scenario horizon (6M)
AUM_SGD = 500_000_000        # assumption: notional fund size used to express DV01 in S$

# SGS benchmark par yields (%): MAS benchmark closing yields, 30 Oct 2026 (data/sgs_benchmark_yields.csv)
_curve = _read("sgs_benchmark_yields.csv")
SGS_TENORS = [_num(r["tenor_years"]) for r in _curve]
SGS_YIELDS = [float(r["yield_pct"]) for r in _curve]

# Benchmark universe: SGS 2Y-30Y benchmark lines, modelled as par bonds at the curve.
BENCHMARK_TENORS = [2, 5, 10, 15, 20, 30]
KEY_RATE_TENORS = [2, 5, 10, 15, 20, 30]

# Macro backdrop quoted in the commentary, with sources and as-of dates.
MACRO = {
    "fed": {
        "text": "Fed raised the target range 25bp to 3.75-4.00% on 16 Sep 2026 (12-0), its first hike since Jul 2023; "
                "Sep projections imply one more hike in 2026. Unchanged on 28 Oct 2026 (10-2)",
        "source": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20260916a.htm",
        "source_oct": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20261028a.htm",
        "as_of": "28 Oct 2026",
    },
    "mas": {
        "text": "MAS raised the S$NEER slope slightly (14 Apr 2026) and very slightly (27 Jul 2026), then left slope, "
                "width and centre unchanged on 14 Oct 2026. Slope of ~1.25% p.a. is a market estimate (UOB)",
        "source": "https://www.mas.gov.sg/monetary-policy/past-monetary-policy-decisions",
        "as_of": "14 Oct 2026",
    },
    "ust10y": {
        "text": "UST 10Y closed 4.94% on 30 Oct 2026 (5.29% end-Sep)",
        "source": "https://fred.stlouisfed.org/series/DGS10",
        "as_of": "30 Oct 2026",
    },
    "sgd_credit": {
        "text": "OCBC Monthly Credit View: weakness concentrated in lower-quality HY; Asia USD IG spreads +1bp m/m "
                "to 56bp; SGD supply S$2.3bn in Sep (Aug S$5.6bn)",
        "source": "https://www.ocbc.com/iwov-resources/sg/ocbc/gbc/pdf/Credit%20Research/Monthly%20Reports/2026/OCBC%20Asia%20Credit%20Monthly%20Credit%20View%20-%20Public%20(October%202026).pdf",
        "as_of": "6 Oct 2026 (data to 30 Sep)",
    },
}
_context = {r["item"]: r for r in _read("market_context.csv")}
UST10Y = float(_context["ust_10y_yield"]["value"])  # FRED DGS10 close, 30 Oct 2026
UST10Y_PREV = float(_context["ust_10y_yield_prev_month_end"]["value"])   # FRED DGS10 close, 30 Sep 2026
SGS10Y_PREV = float(_context["sgs_10y_yield_prev_month_end"]["value"])   # MAS benchmark close, 30 Sep 2026
FED_FUNDS = (float(_context["fed_funds_target_low"]["value"]), float(_context["fed_funds_target_high"]["value"]))
HDB_4Y_PRINT = float(_context["hdb_4y_green_new_issue_yield"]["value"])  # Sep-2026 new issue, OCBC
HDB_4Y_IMPLIED_SPREAD_BP = (35, 69)  # 2.586% less MAS SGS 4Y (2Y-5Y linear) across Sep 2026: low 28 Sep, high 8 Sep

# Credit sectors: spread-risk factor parameters for tracking error. Assumption (annualised, bp).
SECTORS = {
    "Statutory board": {"spread_vol_bp": 10.0, "idio_vol_bp": 5.0},
    "Bank senior":     {"spread_vol_bp": 30.0, "idio_vol_bp": 15.0},
    "Corporate/REIT":  {"spread_vol_bp": 45.0, "idio_vol_bp": 30.0},
}

# Rates factor model. Assumption: annualised vol of SGS par-yield changes by key tenor (bp)
# and exponential decay correlation rho_ij = exp(-LAMBDA*|Ti-Tj|). Not fitted to history.
RATE_VOL_BP = {2: 55.0, 5: 65.0, 10: 70.0, 15: 70.0, 20: 70.0, 30: 72.0}
RATE_CORR_DECAY = 0.03
SPREAD_SPREAD_CORR = 0.70
SPREAD_RATE_CORR = -0.20
