"""Download daily SGS benchmark yields from MAS and write data/sgs_benchmark_yields_history.csv.

Source: MAS, "SGS Prices and Yields - Benchmark Issues" (average buying rates of government securities
dealers), https://eservices.mas.gov.sg/statistics/fdanet/BenchmarkPricesAndYields.aspx. The script posts
the page's own form (frequency = Daily, yields only) and uses its "Download" CSV. Run it only to refresh
the history; `make analysis` reads the committed CSV and needs no network.

    python scripts/fetch_mas_history.py 2023-10-30 2026-10-30
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import sys
from pathlib import Path

import requests

URL = "https://eservices.mas.gov.sg/statistics/fdanet/BenchmarkPricesAndYields.aspx"
OUT = Path(__file__).resolve().parents[1] / "data" / "sgs_benchmark_yields_history.csv"
P = "ctl00$ContentPlaceHolder1$"
# MAS form checkbox -> tenor in years (column order of the download)
SERIES = [("SixMonthTreasuryBill", 0.5), ("OneYearTreasuryBill", 1), ("TwoYearBond", 2), ("FiveYearBond", 5),
          ("TenYearBond", 10), ("FifteenYearBond", 15), ("TwentyYearBond", 20), ("ThirtyYearBond", 30),
          ("FiftyYearBond", 50)]
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def download(start: dt.date, end: dt.date) -> str:
    s = requests.Session()
    page = s.get(URL, timeout=60).text

    def hidden(name):
        return re.search(rf'name="{name}" id="{name}" value="([^"]*)"', page).group(1)

    form = {k: hidden(k) for k in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION")}
    form.update({P + "StartYearDropDownList": str(start.year), P + "StartMonthDropDownList": str(start.month),
                 P + "EndYearDropDownList": str(end.year), P + "EndMonthDropDownList": str(end.month),
                 P + "FrequencyDropDownList": "D", P + "DownloadButton": "Download"})
    for name, _ in SERIES:
        form[P + name + "YieldCheckBox"] = "on"
    r = s.post(URL, data=form, timeout=120)
    r.raise_for_status()
    if "csv" not in r.headers.get("content-type", ""):
        raise RuntimeError(f"MAS did not return a CSV (content-type {r.headers.get('content-type')})")
    return r.content.decode("utf-8-sig")


def parse(text: str) -> list[tuple[dt.date, list]]:
    """MAS layout: year and month are only filled on their first row; blank cells are missing quotes."""
    rows, year, month = [], None, None
    header_seen = False
    for rec in csv.reader(io.StringIO(text)):
        if len(rec) < 3 + len(SERIES):
            continue
        if not rec[2].strip().isdigit():
            header_seen = header_seen or "Yield" in ",".join(rec)
            continue
        year = int(rec[0]) if rec[0].strip() else year
        month = MONTHS[rec[1].strip()] if rec[1].strip() else month
        vals = [float(v) if v.strip() else None for v in rec[3:3 + len(SERIES)]]
        rows.append((dt.date(year, month, int(rec[2])), vals))
    if not header_seen or not rows:
        raise RuntimeError("unexpected MAS CSV layout")
    return rows


def main(start: str, end: str) -> None:
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    rows = [(d, v) for d, v in parse(download(d0, d1)) if d0 <= d <= d1]
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date"] + [f"{t:g}" for _, t in SERIES])  # header: tenor in years
        for d, v in rows:
            w.writerow([d.isoformat()] + ["" if x is None else f"{x:.2f}" for x in v])
    print(f"{OUT}: {len(rows)} days, {rows[0][0]} to {rows[-1][0]}")


if __name__ == "__main__":
    main(*(sys.argv[1:3] if len(sys.argv) >= 3 else ("2023-10-30", "2026-10-30")))
