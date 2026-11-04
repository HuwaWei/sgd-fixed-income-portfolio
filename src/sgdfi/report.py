"""Turn the analytics into the numbers, tables and chart quoted in the commentary and README."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import history as hist
from . import market as m
from .portfolio import build_benchmark, build_curve, build_model, tracking_error
from .scenarios import SCENARIOS, scenario_table

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = {
    "portfolio_commentary.md.j2": "report/portfolio_commentary.md",
    "README.md.j2": "README.md",
}


def compute() -> dict:
    c = build_curve()
    b, p = build_benchmark(c), build_model(c)
    te = tracking_error(p, b, c)
    keys = m.KEY_RATE_TENORS
    kp, kb = p.krd(c), b.krd(c)
    py = dict(zip(m.SGS_TENORS, m.SGS_YIELDS))

    def stats(P):
        return {"duration": P.duration(c), "ytm_pct": P.ytm(c) * 100, "convexity": P.convexity(c),
                "spread_duration": P.spread_duration(c), "dv01_sgd": P.dv01_sgd(c),
                "credit_weight": P.credit_weight(), "avg_spread_bp": P.avg_spread_bp()}

    sp, sb = stats(p), stats(b)
    holdings = []
    bw = {bo.name: w for bo, w in b.holdings}
    for bo, w in p.holdings:
        holdings.append({"name": bo.name, "sector": bo.sector, "rating": bo.rating,
                         "coupon_pct": bo.coupon * 100, "maturity": bo.maturity,
                         "spread_bp": bo.spread * 1e4, "ytm_pct": bo.ytm(c) * 100,
                         "price": bo.clean(c), "duration": bo.eff_duration(c),
                         "w_port": w, "w_bench": bw.get(bo.name, 0.0), "note": bo.note})
    scen = []
    for r in scenario_table(p, b, c):
        sc = r["scenario"]
        scen.append({"name": sc.name, "narrative": sc.narrative,
                     "d2y": sc.curve_bp[2], "d10y": sc.curve_bp[10], "d30y": sc.curve_bp[30],
                     "spreads": sc.spread_bp,
                     "port_pct": r["port"]["total"] * 100, "bench_pct": r["bench"]["total"] * 100,
                     "active_bp": {k: v * 1e4 for k, v in r["active"].items()}})
    sc = {x["name"]: x for x in scen}
    adv = (kp - kb) * m.AUM_SGD * 1e-4

    # Trade levels. Curve: target = entry + base-case 5s30s move; stop at half that distance (2:1 reward/risk).
    s5s30 = (py[30] - py[5]) * 100
    base_sc = next(x for x in SCENARIOS if x.name == "Base")
    tgt = s5s30 + base_sc.curve_bp[30] - base_sc.curve_bp[5]
    levels = {
        "curve": {"entry_bp": s5s30, "target_bp": tgt, "stop_bp": s5s30 - 0.5 * (tgt - s5s30),
                  "dv01_5y": adv[1], "dv01_20_30y": adv[4] + adv[5]},
        "duration": {"entry_pct": py[10], "target_pct": py[10] + base_sc.curve_bp[10] / 100, "stop_ust_pct": 4.50},
        "credit": {"entry_avg_spread_bp": sp["avg_spread_bp"] / sp["credit_weight"], "stop_widening_bp": 25,
                   "spread_carry_6m_bp": sp["avg_spread_bp"] * m.HORIZON_YEARS},  # spread income only, portfolio level
    }

    # Sensitivity of the credit case to the statutory-board spread calibration.
    sens = []
    for lab, hdb_s, sb_s in [("Prior assumption", 18, 25), ("Calibrated (used)", 40, 47),
                             ("Top of Sep-2026 range", m.HDB_4Y_IMPLIED_SPREAD_BP[1], m.HDB_4Y_IMPLIED_SPREAD_BP[1] + 7)]:
        q = build_model(c, spread_bp={"HDB 3.46% 21-May-2031": hdb_s, "Statutory board AAA 10Y": sb_s})
        rows = {x["scenario"].name: x["active"]["total"] * 1e4 for x in scenario_table(q, b, c)}
        sens.append({"label": lab, "hdb_bp": hdb_s, "sb10_bp": sb_s, "pickup_bp": (q.ytm(c) - b.ytm(c)) * 1e4,
                     "base_bp": rows["Base"], "stress_bp": rows["Credit stress"]})

    out = {
        "as_of": m.AS_OF, "aum_sgd": m.AUM_SGD, "macro": m.MACRO, "levels": levels, "spread_sensitivity": sens,
        "hdb_comparable": {"yield_pct": m.HDB_4Y_PRINT, "implied_spread_bp": list(m.HDB_4Y_IMPLIED_SPREAD_BP)},
        "breakeven_6m_bp": (sp["ytm_pct"] - sb["ytm_pct"]) * 100 * m.HORIZON_YEARS / sp["spread_duration"],
        "oct_move": {"sgs10_bp": (py[10] - m.SGS10Y_PREV) * 100, "ust10_bp": (m.UST10Y - m.UST10Y_PREV) * 100,
                     "beta": (py[10] - m.SGS10Y_PREV) / (m.UST10Y - m.UST10Y_PREV),
                     "sgs10_prev": m.SGS10Y_PREV, "ust10_prev": m.UST10Y_PREV},
        "front_gap_bp": [(m.FED_FUNDS[0] - py[0.5]) * 100, (m.FED_FUNDS[1] - py[0.5]) * 100],
        "curve": {"yields": py, "s2s10": (py[10] - py[2]) * 100, "s10s30": (py[30] - py[10]) * 100,
                  "s5s30": (py[30] - py[5]) * 100, "s6m2y": (py[2] - py[0.5]) * 100,
                  "sgs_ust_10y": (py[10] - m.UST10Y) * 100, "ust10y": m.UST10Y},
        "port": sp, "bench": sb,
        "active": {k: sp[k] - sb[k] for k in sp},
        "krd": {"keys": keys, "port": kp.tolist(), "bench": kb.tolist(), "active": (kp - kb).tolist(),
                "active_dv01_sgd": ((kp - kb) * m.AUM_SGD * 1e-4).tolist()},
        "sector_sd": p.spread_duration_by_sector(c),
        "high_grade_share": sum(h["w_port"] for h in holdings if h["sector"] in ("Statutory board", "Bank senior"))
                            / sp["credit_weight"],
        "te": {k: v for k, v in te.items() if k != "exposures"},
        "holdings": holdings, "scenarios": scen,
        "bench_weights": {bo.name: w for bo, w in b.holdings},
    }
    out["history"] = hist.compute(out)
    return out


# ---------------- formatting ----------------
def _f(x, d=2, sign=False):
    return f"{x:+.{d}f}" if sign else f"{x:.{d}f}"


def risk_table(r) -> str:
    P, B, A = r["port"], r["bench"], r["active"]
    rows = [("Effective duration, yrs", "duration", 2, 1),
            ("Yield to maturity (MV-weighted), %", "ytm_pct", 2, 1),
            ("DV01, S\\$k per bp (S\\$500m; + = long)", "dv01_sgd", 1, 1e-3),
            ("Spread duration, yrs", "spread_duration", 2, 1),
            ("Credit weight, %", "credit_weight", 1, 100),
            ("Avg spread over SGS, bp", "avg_spread_bp", 0, 1),
            ("Effective convexity, yrs²", "convexity", 1, 1)]
    out = ["| Metric | Model | Benchmark | Active |", "|---|---:|---:|---:|"]
    for lab, k, d, s in rows:
        out.append(f"| {lab} | {_f(P[k]*s, d)} | {_f(B[k]*s, d)} | {_f(A[k]*s, d, True)} |")
    out.append(f"| Ex-ante tracking error, bp p.a. | {_f(r['te']['te_bp'], 0)} | – | – |")
    return "\n".join(out)


def krd_table(r) -> str:
    k = r["krd"]
    head = "| Key rate | " + " | ".join(f"{t}Y" for t in k["keys"]) + " | Total |"
    sep = "|---|" + "---:|" * (len(k["keys"]) + 1)
    def row(lab, v, d=2, sign=False, scale=1):
        return f"| {lab} | " + " | ".join(_f(x * scale, d, sign) for x in v) + f" | {_f(sum(v)*scale, d, sign)} |"
    return "\n".join([head, sep, row("Model KRD", k["port"]), row("Benchmark KRD", k["bench"]),
                      row("Active KRD", k["active"], sign=True),
                      row("Active DV01, S\\$k/bp", k["active_dv01_sgd"], 1, True, 1e-3)])


def holdings_table(r) -> str:
    out = ["| Holding | Rating | Cpn % | Mat (y) | Spread bp | YTM % | Dur | Model % | Bench % | Active % |",
           "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for h in r["holdings"]:
        flag = " †" if "ILLUSTRATIVE" in h["note"] else ""
        out.append(f"| {h['name']}{flag} | {h['rating']} | {h['coupon_pct']:.3f} | {h['maturity']:.2f} | "
                   f"{h['spread_bp']:.0f} | {h['ytm_pct']:.2f} | {h['duration']:.2f} | {h['w_port']*100:.1f} | "
                   f"{h['w_bench']*100:.1f} | {(h['w_port']-h['w_bench'])*100:+.1f} |")
    return "\n".join(out)


def scenario_tables(r) -> tuple[str, str]:
    a = ["| Scenario (6M) | Narrative | SGS 2Y / 10Y / 30Y, bp | Spreads Stat board / Bank / Corp-REIT, bp | Model % | Bench % | Active bp |",
         "|----|------------------|------|------|---:|---:|---:|"]
    t = ["| Active, bp | Carry | Roll | Duration | Curve | Spread | Residual | Total |",
         "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for s in r["scenarios"]:
        sp = s["spreads"]
        a.append(f"| **{s['name']}** | {s['narrative']} | {s['d2y']:+.0f} / {s['d10y']:+.0f} / {s['d30y']:+.0f} | "
                 f"{sp['Statutory board']:+.0f} / {sp['Bank senior']:+.0f} / {sp['Corporate/REIT']:+.0f} | "
                 f"{s['port_pct']:+.2f} | {s['bench_pct']:+.2f} | {s['active_bp']['total']:+.0f} |")
        x = s["active_bp"]
        t.append(f"| {s['name']} | " + " | ".join(f"{x[k]:+.1f}" for k in
                 ["carry", "roll", "duration", "curve", "spread", "residual", "total"]) + " |")
    return "\n".join(a), "\n".join(t)


def levels_table(r) -> str:
    L = r["levels"]; cu, du, cr = L["curve"], L["duration"], L["credit"]
    return "\n".join([
        "| Trade | Size | Entry | Target (6M) | Stop |",
        "|---|---|---|---|---|",
        f"| **5s30s steepener**: long 5Y vs short 20Y–30Y | {cu['dv01_5y']/1e3:+.1f} / {cu['dv01_20_30y']/1e3:+.1f} S\\$k/bp (DV01-matched) "
        f"| {cu['entry_bp']:+.0f} bp | {cu['target_bp']:+.0f} bp (base case) | {cu['stop_bp']:+.0f} bp |",
        f"| **Duration underweight** | {r['active']['dv01_sgd']/1e3:+.1f} S\\$k/bp | SGS 10Y {du['entry_pct']:.2f}% "
        f"| {du['target_pct']:.2f}% (base case) | UST 10Y close < {du['stop_ust_pct']:.2f}% |",
        f"| **SGD IG credit overweight** | {r['port']['credit_weight']*100:.0f}% MV, SD {r['port']['spread_duration']:.2f} yrs "
        f"| avg {cr['entry_avg_spread_bp']:.0f} bp | spread carry {cr['spread_carry_6m_bp']:+.0f} bp, spreads unchanged "
        f"| SGD IG +{cr['stop_widening_bp']:.0f} bp, or HY stress reaching BB/IG: cut corp/REIT first |",
    ])


def history_table(r) -> str:
    H, L = r["history"], r["history"]["levels"]
    lv = {"Duration underweight": f"SGS 10Y {L['sgs10y_pct']['now']:.2f}% vs {L['sgs10y_pct']['mean']:.2f}% "
                                  f"± {L['sgs10y_pct']['sd']:.2f}; z {L['sgs10y_pct']['z']:+.2f}",
          "5s30s steepener": f"5s30s {L['s5s30_bp']['now']:+.0f} vs {L['s5s30_bp']['mean']:+.0f} "
                             f"± {L['s5s30_bp']['sd']:.0f} bp; z {L['s5s30_bp']['z']:+.2f}",
          "Rates book (credit as SGS)": "All active rates exposures together"}
    like = {"Duration underweight": "10Y ≤ today", "5s30s steepener": "5s30s ≤ today",
            "Rates book (credit as SGS)": "10Y ≤ today"}
    out = ["| Position | Today vs 3Y mean ± sd | 6M hit rate | Avg active, bp | Worst 6M / max drawdown, bp "
           "| Starts like today: hit / avg | History |",
           "|---|---|---:|---:|---:|---:|---|"]
    for name, t in H["tests"].items():
        lt = t["like_today"]
        out.append(f"| {name} | {lv[name]} | {t['hit_rate']*100:.0f}% | {t['mean_bp']:+.1f} | "
                   f"{t['worst_bp']:+.0f} / {t['max_dd_bp']:+.0f} | {like[name]}: {lt['hit_rate']*100:.0f}% / "
                   f"{lt['mean_bp']:+.1f} | {t['verdict']} |")
    out.append("| SGD IG credit overweight | No public SGD IG spread history | – | – | – | – | Not testable |")
    return "\n".join(out)


def history_table_short(r) -> str:
    """Compact version for the 2-page PDF (credit row is covered in the text)."""
    H, L = r["history"], r["history"]["levels"]
    lv = {"Duration underweight": f"10Y {L['sgs10y_pct']['now']:.2f}% vs {L['sgs10y_pct']['mean']:.2f} ± "
                                  f"{L['sgs10y_pct']['sd']:.2f}% (z {L['sgs10y_pct']['z']:+.2f})",
          "5s30s steepener": f"5s30s {L['s5s30_bp']['now']:+.0f} vs {L['s5s30_bp']['mean']:+.0f} ± "
                             f"{L['s5s30_bp']['sd']:.0f} bp (z {L['s5s30_bp']['z']:+.2f})",
          "Rates book (credit as SGS)": "All rates exposures together"}
    out = ["| 6M holding, active bp | Today vs 3Y mean ± sd | Hit rate | Avg | Worst 6M | Max DD "
           "| Starts like today: hit / avg | History |",
           "|------------|-------------------------|-----:|----:|-----:|----:|--------:|--------|"]
    for name, t in H["tests"].items():
        lt = t["like_today"]
        out.append(f"| {name.replace(' (credit as SGS)', '')} | {lv[name]} | {t['hit_rate']*100:.0f}% | "
                   f"{t['mean_bp']:+.1f} | {t['worst_bp']:+.0f} | {t['max_dd_bp']:+.0f} | "
                   f"{lt['hit_rate']*100:.0f}% / {lt['mean_bp']:+.1f} | {t['verdict']} |")
    return "\n".join(out)


def history_figure(r, path: Path) -> None:
    import datetime as dt
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    H = r["history"]; S = H["series"]
    d = [dt.date.fromisoformat(x) for x in S["dates"]]
    fig, (ax1, ax2, ax3) = plt.subplots(1, 3, figsize=(9, 1.75), dpi=200)
    for k, lab, col in [("y2", "2Y", "#7fa7d0"), ("y10", "10Y", "#1f4e79"), ("y30", "30Y", "#e07b39")]:
        ax1.plot(d, S[k], lw=1.0, color=col, label=lab)
    ax1.set_title("SGS benchmark yields, 3Y daily (%)", fontsize=8.5)
    ax1.legend(fontsize=6.5, frameon=False, loc="upper right", ncol=3)
    L = H["levels"]["s5s30_bp"]
    ax2.plot(d, S["s5s30"], lw=1.0, color="#1f4e79")
    ax2.axhline(L["mean"], color="grey", lw=0.8)
    ax2.axhspan(L["mean"] - L["sd"], L["mean"] + L["sd"], color="grey", alpha=0.15, lw=0)
    ax2.plot(d[-1], L["now"], "o", color="#c62828", ms=4)
    ax2.set_title(f"5s30s (bp), 3Y mean ± 1 sd: today {L['now']:+.0f}, z {L['z']:+.2f}", fontsize=8.5)
    short = {"Duration underweight": "Duration UW", "5s30s steepener": "Steepener",
             "Rates book (credit as SGS)": "Rates book"}
    cols = {"Duration underweight": "#c62828", "5s30s steepener": "#2e7d32", "Rates book (credit as SGS)": "#555555"}
    for name, t in H["tests"].items():
        ax3.plot([dt.date.fromisoformat(x) for x in t["starts"]], t["returns_bp"], lw=0.9, color=cols[name],
                 label=f"{short[name]} (hit {t['hit_rate']*100:.0f}%)")
    ax3.axhline(0, color="black", lw=0.6)
    ax3.set_title("Active return over next 6M, by start date (bp)", fontsize=8.5)
    ax3.set_ylim(min(min(t["returns_bp"]) for t in H["tests"].values()) - 5,
                 max(max(t["returns_bp"]) for t in H["tests"].values()) + 45)
    ax3.legend(fontsize=6, frameon=False, loc="upper left", ncol=2, columnspacing=0.8, handlelength=1.2)
    for ax in (ax1, ax2, ax3):
        ax.tick_params(labelsize=6.5); ax.grid(alpha=0.3)
        ax.xaxis.set_major_locator(matplotlib.dates.YearLocator())
        ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%Y"))
    fig.tight_layout(); path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path); plt.close(fig)


def figure(r, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(9, 2.2), dpi=200)
    y = r["curve"]["yields"]
    ax1.plot(list(y), list(y.values()), marker="o", color="#1f4e79", lw=1.6)
    ax1.set_xscale("log"); ax1.set_xticks(list(y)); ax1.set_xticklabels(["6M", "1Y", "2Y", "5Y", "10Y", "15Y", "20Y", "30Y", "50Y"], fontsize=7)
    ax1.set_title(f"SGS par curve, {r['as_of']} (%)", fontsize=9); ax1.tick_params(labelsize=7); ax1.grid(alpha=0.3)
    k = r["krd"]
    v = np.array(k["active_dv01_sgd"]) / 1e3
    ax2.bar([f"{t}Y" for t in k["keys"]], v, color=["#2e7d32" if x > 0 else "#c62828" for x in v])
    ax2.axhline(0, color="black", lw=0.6)
    ax2.set_title("Active key-rate DV01 vs benchmark (S$k/bp)", fontsize=9); ax2.tick_params(labelsize=7); ax2.grid(alpha=0.3, axis="y")
    fig.tight_layout(); path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path); plt.close(fig)


def render(r) -> dict:
    sc_summary, sc_attr = scenario_tables(r)
    tables = {"risk": risk_table(r), "krd": krd_table(r), "holdings": holdings_table(r),
              "scenarios": sc_summary, "attribution": sc_attr, "levels": levels_table(r),
              "history": history_table(r), "history_short": history_table_short(r)}
    out = ROOT / "output"; out.mkdir(exist_ok=True)
    for k, v in tables.items():
        if k != "history_short":  # PDF-only variant of history.md
            (out / f"{k}.md").write_text(v + "\n")
    # results.json keeps the history summary, not the daily series or per-window returns (those feed the chart)
    slim = dict(r, history={k: v for k, v in r["history"].items() if k != "series"})
    slim["history"]["tests"] = {n: {k: v for k, v in t.items() if k not in ("starts", "returns_bp")}
                                for n, t in r["history"]["tests"].items()}
    (out / "results.json").write_text(json.dumps(slim, indent=2, default=float))
    figure(r, ROOT / "report" / "figures" / "positioning.png")
    history_figure(r, ROOT / "report" / "figures" / "history.png")

    env = Environment(loader=FileSystemLoader(ROOT / "report" / "templates"), undefined=StrictUndefined,
                      keep_trailing_newline=True)
    env.filters["mon"] = lambda iso: __import__("datetime").date.fromisoformat(iso).strftime("%b %Y")
    ctx = {"r": r, "t": tables, "sc": {s["name"]: s for s in r["scenarios"]}}
    for tpl, target in TEMPLATES.items():
        (ROOT / target).write_text(env.get_template(tpl).render(**ctx))
    return tables


def main() -> None:
    r = compute()
    render(r)
    a = r["active"]
    print(f"Active duration {a['duration']:+.2f}y | yield pickup {a['ytm_pct']*100:+.0f}bp | "
          f"spread duration {r['port']['spread_duration']:.2f}y | TE {r['te']['te_bp']:.0f}bp")
    for s in r["scenarios"]:
        print(f"  {s['name']:<14} model {s['port_pct']:+.2f}%  bench {s['bench_pct']:+.2f}%  active {s['active_bp']['total']:+.0f}bp")
