"""4H manipulation candle backtest (rules: .github/skills/ctrader-mcp/references/strategy-m4h.md).
Usage (scanner folder):  python -m ictscan.m4h_backtest [--db PATH] [--symbols A,B] [--publish summary.json]
Data: DATA_DIR/hist-m4h.db (Open API history: D1, H4, M30 for config.M4H_SYMBOLS). The test starts when the M30
history starts (H4/D1 reach further back as warm-up); the test period is split in two halves (A = design, B = validation).
"""
from __future__ import annotations

import argparse
import bisect
import json
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, DB_PATH, M4H_SYMBOLS, M4H_TIMEFRAMES
from .engine import m4h
from .engine.core import make_bars
from .store import Store

PASS = {"avg_r": 0.15, "pf": 1.3, "min_trades": 200}
FILTERS = {
    "none": lambda r: True,
    "bias+momentum": lambda r: r["bias"] and r["momentum"],
    "bias+momentum+London/NY AM": lambda r: r["bias"] and r["momentum"] and r["session"] in ("London", "NY AM"),
    "London/NY AM": lambda r: r["session"] in ("London", "NY AM"),
}


def load(db, sym):
    st = Store(db)
    return {tf: make_bars(st.load_bars(sym, tf, 10 ** 7), tf) for tf in M4H_TIMEFRAMES}


def held_study(bars):
    """Tom's claim: the manipulation candle's high is 'probably the highest point of the day'. For every H4 candle that
    takes the previous H4 high and is the day's high so far: did that high hold to the 17:00 NY close? Split by whether
    the candle closed below the previous body (= the setup). Buy side mirrored. Returns counters."""
    out = defaultdict(lambda: [0, 0])
    for side in ("sell", "buy"):
        src = {k: bars[k] if side == "sell" else m4h.mirror(bars[k]) for k in ("h4", "m30")}
        h4, m30 = src["h4"], src["m30"]
        starts = [b.t for b in m30]
        for i in range(1, len(h4)):
            C, P = h4[i], h4[i - 1]
            if C.t < m30[0].t or not C.h > P.h:
                continue
            d0, d1 = m4h.day_bounds(C.t)
            if m30[-1].end < d1:
                continue
            k0, k1, k2 = (bisect.bisect_left(starts, x) for x in (d0, C.end, d1))
            if any(b.h > C.h for b in m30[k0:k1]):
                continue  # not the high of the day so far
            held = all(b.h <= C.h for b in m30[k1:k2])
            kind = "engulf (setup)" if C.c < min(P.o, P.c) else "no engulf"
            for key in (kind, f"{kind} · {m4h.session_of(C.t)} candle"):
                out[key][0] += 1
                out[key][1] += held
    return dict(out)


def run_symbol(args):
    db, sym, anchor = args
    bars = load(db, sym)
    if not bars["m30"] or not bars["h4"]:
        return sym, [], {}, None
    cfg = M4H_SYMBOLS[sym]
    start = bars["m30"][0].t
    if anchor == "tom":  # 4H candles rebuilt from M30 → skip 60 days so the H4 structure has warmed up
        bars["h4"] = m4h.aggregate_ny(bars["m30"])
        start += timedelta(days=60)
    setups = m4h.run(bars, cfg, since=start)
    rows = []
    for s in setups:
        if s["t"] < start:
            continue
        for pl in s["plans"]:
            for tr in pl["trades"]:
                rows.append({"symbol": sym, "class": cfg["class"], "dir": s["dir"], "conf_t": s["conf_t"].isoformat(),
                             "session": s["session"], "bias": s["bias"], "momentum": s["momentum"],
                             "hod_so_far": s["hod_so_far"], "hod_held": s["hod_held"], "range_atr": s["range_atr"],
                             "entry_id": pl["id"], "target": tr["target"], "outcome": tr["outcome"], "R": tr.get("R"),
                             "rr": tr.get("rr"), "risk": pl["stop"] - pl["entry"] if s["dir"] == "sell" else pl["entry"] - pl["stop"],
                             "exit_t": tr["exit_t"].isoformat() if tr.get("exit_t") else None})
    return sym, rows, held_study(bars), (start.isoformat(), bars["m30"][-1].end.isoformat())


def stats(rs):
    if not rs:
        return {"trades": 0}
    eq = pk = dd = 0.0
    run = worst = 0.0
    for r in rs:
        eq += r
        pk = max(pk, eq)
        dd = min(dd, eq - pk)
        run = run + r if r < 0 else 0.0
        worst = min(worst, run)
    w, l = sum(r for r in rs if r > 0), -sum(r for r in rs if r < 0)
    return {"trades": len(rs), "win_rate": round(sum(r > 0 for r in rs) / len(rs) * 100, 1),
            "avg_r": round(statistics.mean(rs), 3), "total_r": round(sum(rs), 1), "pf": round(w / l, 2) if l else None,
            "max_dd": round(dd, 1), "worst_run": round(worst, 1)}


def line(st):
    if not st["trades"]:
        return "   0 trades"
    pf = f"{st['pf']:.2f}" if st["pf"] is not None else "inf"
    return (f"{st['trades']:5d} tr  win {st['win_rate']:5.1f}%  avg {st['avg_r']:+.3f}R  tot {st['total_r']:+8.1f}R  "
            f"PF {pf:>5}  DD {st['max_dd']:7.1f}R  run {st['worst_run']:6.1f}R")


def closed(rows):
    return sorted((r for r in rows if r["R"] is not None), key=lambda r: r["exit_t"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DATA_DIR / "hist-m4h.db"))
    ap.add_argument("--symbols", default=",".join(M4H_SYMBOLS))
    ap.add_argument("--anchor", choices=("broker", "tom"), default="broker",
                    help="4H candles: broker = Pepperstone's own; tom = rebuilt from M30 on an 18:00 NY anchor")
    ap.add_argument("--json", default=str(DATA_DIR / "m4h-backtest.json"))
    ap.add_argument("--summary", default=str(DATA_DIR / "m4h-summary.json"))
    ap.add_argument("--publish", help="store a saved summary JSON in the scanner database (shown on /m4h)")
    a = ap.parse_args()
    if a.publish:
        with open(a.publish, encoding="utf-8") as fh:
            Store(DB_PATH).set_meta("backtest:m4h", json.load(fh))
        print(f"Published {a.publish} to {DB_PATH}")
        return
    syms = [s for s in a.symbols.split(",") if s]
    rows, held, spans = [], defaultdict(lambda: [0, 0]), []
    with ProcessPoolExecutor() as ex:
        for sym, r, h, span in ex.map(run_symbol, [(a.db, s, a.anchor) for s in syms]):
            rows += r
            for k, (n, k_held) in h.items():
                held[k][0] += n
                held[k][1] += k_held
            if span:
                spans.append(span)
    t0, t1 = min(s[0] for s in spans), max(s[1] for s in spans)
    split = datetime.fromtimestamp((datetime.fromisoformat(t0).timestamp() + datetime.fromisoformat(t1).timestamp()) / 2,
                                   timezone.utc).isoformat()
    print(f"{len(syms)} symbols, {t0[:10]} → {t1[:10]}; A < {split[:10]} ≤ B. Costs included; stop+target in one M30 bar = stop.")

    print("\n=== Tom's claim: does the high (low) of a candle that took the previous H4 high (low) and is the day's high so far"
          " hold to the 17:00 NY close?")
    for k in sorted(held):
        n, h = held[k]
        print(f"   {k:40s} {n:6d} candles  held {h / n * 100:5.1f}%")

    cand = {}
    for r in rows:
        cand.setdefault((r["symbol"], r["conf_t"], r["dir"]), r)
    print(f"\n=== Setups: {len(cand)} manipulation candles; per filter: " + ", ".join(
        f"{f} {sum(FILTERS[f](r) for r in cand.values())}" for f in FILTERS))
    hh = [r["hod_held"] for r in cand.values() if r["hod_held"] is not None and r["hod_so_far"]]
    if hh:
        print(f"   setups that were the day's extreme so far: {len(hh)}, extreme held to day end {sum(hh) / len(hh) * 100:.1f}%")

    summary = {"period": f"{t0[:10]} → {t1[:10]}", "split": split[:10], "symbols": len(syms), "combos": {}}
    for fname, fn in FILTERS.items():
        print(f"\n=== filter: {fname}")
        for eid in m4h.ENTRIES:
            for tid in m4h.TARGETS:
                sub = [r for r in rows if r["entry_id"] == eid and r["target"] == tid and fn(r)]
                if not sub:
                    continue
                cl = closed(sub)
                rs = [r["R"] for r in cl]
                st_all = stats(rs)
                st_a = stats([r["R"] for r in cl if r["conf_t"] < split])
                st_b = stats([r["R"] for r in cl if r["conf_t"] >= split])
                by_sym = defaultdict(list)
                for r in cl:
                    by_sym[r["symbol"]].append(r["R"])
                prof = sum(sum(v) > 0 for v in by_sym.values())
                fills = sum(r["outcome"] in ("target", "stopped", "open") for r in sub)
                ok = (st_all["trades"] >= PASS["min_trades"] and st_all.get("avg_r", -9) >= PASS["avg_r"]
                      and (st_all.get("pf") or 0) >= PASS["pf"] and st_a.get("avg_r", -9) > 0 and st_b.get("avg_r", -9) > 0
                      and prof > len(by_sym) / 2)
                print(f"  {eid} {tid:4s} {line(st_all)}  A {st_a.get('avg_r', 0):+.3f} B {st_b.get('avg_r', 0):+.3f}  "
                      f"profitable {prof}/{len(by_sym)}  filled {fills}/{len(sub)}{'  PASS' if ok else ''}")
                summary["combos"][f"{fname}|{eid}|{tid}"] = {"all": st_all, "A": st_a, "B": st_b, "profitable": prof,
                                                              "symbols": len(by_sym), "pass": ok}

    print("\n=== by asset class / session / direction (filter bias+momentum, E2 × T2R)")
    base = [r for r in rows if r["entry_id"] == "E2" and r["target"] == "T2R" and FILTERS["bias+momentum"](r)]
    for key in ("class", "session", "dir"):
        groups = defaultdict(list)
        for r in closed(base):
            groups[r[key]].append(r["R"])
        for g in sorted(groups):
            print(f"   {key:8s} {g:10s} {line(stats(groups[g]))}")
    print("\n=== per symbol (filter none, E1 × T2R)")
    groups = defaultdict(list)
    for r in closed([r for r in rows if r["entry_id"] == "E1" and r["target"] == "T2R"]):
        groups[r["symbol"]].append(r["R"])
    for g in sorted(groups, key=lambda g: -sum(groups[g])):
        print(f"   {g:10s} {line(stats(groups[g]))}")

    with open(a.json, "w", encoding="utf-8") as fh:
        json.dump({"generated": datetime.now(timezone.utc).isoformat(), "rows": rows}, fh)
    summary["held"] = {k: {"candles": n, "held_pct": round(h / n * 100, 1)} for k, (n, h) in held.items()}
    with open(a.summary, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print(f"\nRows saved to {a.json}; summary to {a.summary} (publish with --publish {a.summary})")


if __name__ == "__main__":
    main()
