"""MMXM backtest on the three US indices (rules: .github/skills/ctrader-mcp/references/strategy-mmxm.md).
Usage (scanner folder):  python -m ictscan.mmxm_backtest [--db PATH]
Default data: DATA_DIR/hist-indices.db (Open API history for NAS100/US30/US500). Design period < 2026-05-01, validation after.
Only setups confirmed after the test start (2025-10-16) count; earlier candles are warm-up.
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .backtest import COSTS
from .config import DATA_DIR, DB_PATH, SYMBOLS, TIMEFRAMES
from .engine import mmxm
from .engine.core import make_bars
from .store import Store

NY = ZoneInfo("America/New_York")
START = datetime(2025, 10, 16, 20, 16, tzinfo=timezone.utc)
SPLIT = datetime(2026, 5, 1, tzinfo=timezone.utc)
KILLZONES = ((2, 0, 5, 0), (8, 30, 11, 0))  # London 02:00–05:00, New York AM 08:30–11:00


def in_killzone(t):
    n = t.astimezone(NY)
    m = n.hour * 60 + n.minute
    return any(h0 * 60 + m0 <= m < h1 * 60 + m1 for h0, m0, h1, m1 in KILLZONES)


def summ(rs):
    if not rs:
        return "  0 trades"
    eq = pk = dd = 0.0
    for r in rs:
        eq += r
        pk = max(pk, eq)
        dd = min(dd, eq - pk)
    w = sum(r > 0 for r in rs)
    pf_w, pf_l = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    pf = f"{pf_w / pf_l:.2f}" if pf_l else "inf"
    return (f"{len(rs):4d} trades  win {w / len(rs) * 100:5.1f}%  avg {statistics.mean(rs):+.3f}R  total {sum(rs):+7.1f}R  "
            f"PF {pf:>5}  maxDD {dd:6.1f}R")


def load(db):
    st = Store(db)
    return {s: {tf: make_bars(st.load_bars(s, tf, 10 ** 7), tf) for tf in TIMEFRAMES} for s in mmxm.INDICES}


def trades(results, extra=None):
    out = sorted((r for r in results if "R" in r and r["conf_t"] >= START), key=lambda r: r["exit_t"])  # chronological for drawdown
    return [r for r in out if extra(r)] if extra else out


def report(label, results):
    tr = trades(results)
    print(f"\n{label}\n   ALL {summ([r['R'] for r in tr])}\n   A   {summ([r['R'] for r in tr if r['conf_t'] < SPLIT])}"
          f"\n   B   {summ([r['R'] for r in tr if r['conf_t'] >= SPLIT])}")
    return tr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DATA_DIR / "hist-indices.db"))
    ap.add_argument("--json", default=str(DATA_DIR / "mmxm-backtest.json"))
    ap.add_argument("--summary", default=str(DATA_DIR / "mmxm-summary.json"))
    ap.add_argument("--publish", help="store a saved summary JSON in the scanner database (shown on MMXM setup pages)")
    a = ap.parse_args()
    if a.publish:
        with open(a.publish, encoding="utf-8") as fh:
            Store(DB_PATH).set_meta("backtest:mmxm", json.load(fh))
        print(f"Published {a.publish} to {DB_PATH}")
        return
    bars = load(a.db)
    m5 = bars["NAS100"]["m5"]
    print(f"Data: {m5[0].t:%d %b %Y} → {m5[-1].t:%d %b %Y} (test from {START:%d %b %Y}; A < {SPLIT:%d %b %Y} ≤ B)")
    buffers = {s: SYMBOLS[s]["buffer"] for s in mmxm.INDICES}
    costs = {s: COSTS[s] for s in mmxm.INDICES}
    variants = [
        ("AS SPECIFIED (SMT required, adds, stop below MSS FVG, target = original consolidation)", {}),
        ("SMT off (to see what SMT adds)", {"smt": "off"}),
        ("no adds", {"max_adds": 0}),
        ("stop below the reversal low", {"stop": "low"}),
    ]
    store = {}
    for label, ov in variants:
        res, funnels = mmxm.run(bars, ov, buffers=buffers, costs=costs)
        store[label] = (res, funnels)
        tr = report(label, res)
        for mode in ("intraday", "swing"):
            sub = [r['R'] for r in tr if r["mode"] == mode]
            print(f"   {mode:8s} {summ(sub)}")
    res, funnels = store[variants[0][0]]
    tr = trades(res)
    print("\n--- AS SPECIFIED, detail")
    kz = [r["R"] for r in tr if in_killzone(r["conf_t"])]
    nkz = [r["R"] for r in tr if not in_killzone(r["conf_t"])]
    print(f"   MSS in killzone        {summ(kz)}\n   MSS outside killzone   {summ(nkz)}")
    by = defaultdict(list)
    for r in tr:
        by[f"{r['mode']} {r['dir']}"].append(r["R"])
        by[f"symbol {r['symbol']}"].append(r["R"])
        by[f"exit {r['outcome']}"].append(r["R"])
        by[f"adds {r['adds']}"].append(r["R"])
    for k in sorted(by):
        print(f"   {k:24s} {summ(by[k])}")
    rr = [r["rr"] for r in tr]
    if rr:
        print(f"   planned R:R to terminus: median {statistics.median(rr):.2f}, p25 {sorted(rr)[len(rr) // 4]:.2f}, "
              f"p75 {sorted(rr)[3 * len(rr) // 4]:.2f}")
    outcomes = Counter(r["outcome"] for r in res if r["conf_t"] >= START)
    print("   setups by outcome (incl. never filled):", dict(outcomes))
    print("   funnel (summed over symbols):")
    agg = defaultdict(lambda: Counter())
    for (mode, d, s), f in funnels.items():
        agg[f"{mode} {d}"].update(f)
    for k, f in agg.items():
        print(f"     {k:14s} " + " → ".join(f"{n} {v}" for n, v in f.items()))
    print("   last 8 trades:")
    for r in sorted(tr, key=lambda x: x["fill_t"])[-8:]:
        print(f"   {r['fill_t'].astimezone(NY):%a %d %b %H:%M} {r['symbol']:6s} {r['mode']:8s} {r['dir']:4s} at {r['array']:8s} "
              f"entry {r['entry']:.1f} stop {r['stop']:.1f} T {r['T']:.1f} adds {r['adds']} → {r['outcome']:6s} {r['R']:+.2f}R")
    with open(a.json, "w", encoding="utf-8") as fh:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "trades": [
            {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in tr]}, fh, default=str, indent=1)
    print(f"\nTrades saved to {a.json}")
    summary = dashboard_summary(tr, m5)
    with open(a.summary, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(f"Dashboard summary (as specified) saved to {a.summary}; load it with: python -m ictscan.mmxm_backtest --publish {a.summary}")


def dashboard_summary(tr, m5):
    def stat(rs):
        if not rs:
            return {"trades": 0}
        return {"trades": len(rs), "wins": sum(r > 0 for r in rs), "win_rate": round(sum(r > 0 for r in rs) / len(rs) * 100, 1),
                "avg_r": round(statistics.mean(rs), 3), "total_r": round(sum(rs), 1)}
    by_mode, by_ms = defaultdict(list), defaultdict(list)
    for r in tr:
        by_mode[r["mode"]].append(r["R"])
        by_ms[f"{r['mode']} {r['symbol']}"].append(r["R"])
    return {"by_mode": {k: stat(v) for k, v in by_mode.items()}, "by_mode_symbol": {k: stat(v) for k, v in by_ms.items()},
            "period": f"{START:%d %b %Y} → {m5[-1].t:%d %b %Y}, as specified, costs included"}


if __name__ == "__main__":
    main()
