"""Backtest: replay history through the live engine day by day (no future candles), then report results.

Usage (from the scanner folder):
  python -m ictscan.backtest fetch --months 6        # download history into backtest.db (separate from the live DB)
  python -m ictscan.backtest run --part in-sample    # replay the first 80% and publish the report to the dashboard
  python -m ictscan.backtest run --part out-of-sample  # run ONCE, after the rules are final
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import statistics
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import DATA_DIR, DB_PATH, PARAMS, SYMBOLS, TIMEFRAMES, WINDOW
from .data import CALENDAR, fetch_range, parse
from .engine.context import Context
from .engine.core import TF_SECONDS, make_bars
from .engine.setups import FINAL, detect_all
from .mcp import MCPClient
from .config import MCP_ENDPOINT
from .store import Store

HIST_DB = DATA_DIR / "backtest.db"
NY = ZoneInfo("America/New_York")
log = logging.getLogger("ictscan.backtest")

# Round-trip cost per trade in price units: typical spread + commission (Pepperstone Razor ≈ $7/lot FX, none on CFDs).
COSTS = {"NAS100": 1.5, "US30": 2.5, "EURUSD": 0.00017, "GBPUSD": 0.00022, "XAUUSD": 0.25, "GBPJPY": 0.031}
IS_FRACTION = 0.8


def fetch(months):
    store = Store(HIST_DB)
    client = MCPClient(MCP_ENDPOINT, timeout=120)
    now = parse(client.call("get_server_time")["utcTime"])
    start_test = now - timedelta(days=30.5 * months)
    store.set_meta("range", {"start": start_test.isoformat(), "end": now.isoformat(), "months": months})
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            warm = timedelta(seconds=TF_SECONDS[tf] * WINDOW[tf] * CALENDAR[tf])
            raw = fetch_range(client, sym, tf, start_test - warm, now, max_pages=200)
            rows = []
            for b in raw:
                t = parse(b["timestamp"])
                if t + timedelta(seconds=TF_SECONDS[tf]) <= now:
                    rows.append({"t": int(t.timestamp()), "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"], "v": b.get("volume", 0)})
            store.upsert_bars(sym, tf, rows)
            first = datetime.fromtimestamp(rows[0]["t"], timezone.utc).isoformat() if rows else None
            log.info("%s %s: %d bars from %s", sym, tf, len(rows), first)


def _replay_symbol(args):
    sym, t0, t1, params = args
    store = Store(HIST_DB)
    full = {tf: make_bars(store.load_bars(sym, tf, 10**7), tf) for tf in TIMEFRAMES}
    ends = {tf: [b.end for b in full[tf]] for tf in TIMEFRAMES}
    found = {}
    step = t0
    last_m5 = None
    while step <= t1:
        cut = {}
        for tf in TIMEFRAMES:
            k = bisect.bisect_right(ends[tf], step)
            cut[tf] = full[tf][max(0, k - WINDOW[tf]):k]
        if cut["m5"] and cut["m5"][-1].end != last_m5 and all(len(cut[tf]) >= 50 for tf in TIMEFRAMES):
            last_m5 = cut["m5"][-1].end
            ctx = Context(sym, cut, SYMBOLS[sym], params)
            for s in detect_all(ctx):
                if datetime.fromisoformat(s["key_t"]) < t0:
                    continue
                prev = found.get(s["id"])
                if prev is not None and prev["stage"] in FINAL:
                    continue  # freeze the first final version
                s["_seen"] = step.isoformat()
                found[s["id"]] = s
        step += timedelta(days=1)
    return sym, list(found.values())


def replay(t0, t1, params):
    out = []
    with ProcessPoolExecutor(max_workers=min(6, len(SYMBOLS))) as ex:
        for sym, setups in ex.map(_replay_symbol, [(s, t0, t1, params) for s in SYMBOLS]):
            log.info("%s: %d setups", sym, len(setups))
            out.extend(setups)
    return out


def trade_r(s):
    """Net R after costs for a closed trade, or None if the setup never became a trade or is unresolved."""
    if s["stage"] not in ("target", "stopped") or s["entry"] is None:
        return None
    risk = abs(s["entry"] - s["stop"])
    if risk <= 0:
        return None
    gross = s["result_r"]
    return round(gross - COSTS[s["symbol"]] / risk, 3)


def summarize(trades):
    rs = [t["net_r"] for t in trades]
    if not rs:
        return {"trades": 0}
    eq, peak, dd = 0.0, 0.0, 0.0
    for r in rs:
        eq += r
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    wins = sum(r > 0 for r in rs)
    return {"trades": len(rs), "wins": wins, "win_rate": round(wins / len(rs) * 100, 1), "avg_r": round(statistics.mean(rs), 3),
            "total_r": round(sum(rs), 2), "max_dd_r": round(dd, 2), "best_r": round(max(rs), 2), "worst_r": round(min(rs), 2)}


def report(setups, part, t0, t1):
    trades = []
    for s in setups:
        r = trade_r(s)
        if r is None:
            continue
        exit_t = s["timeline"][-1]["t"]
        trades.append({"id": s["id"], "symbol": s["symbol"], "setup": s["setup"], "dir": s["dir"], "score": s["score"],
                       "entry": s["entry"], "stop": s["stop"], "tp1": s["tp1"], "rr": s["rr"], "gross_r": s["result_r"],
                       "net_r": r, "exit_t": exit_t, "key_t": s["key_t"],
                       "trigger_ny": next((e["ny"] for e in s["timeline"] if e["stage"] == "triggered"), ""),
                       "exit_ny": s["timeline"][-1]["ny"], "checklist": {c["name"]: c["ok"] for c in s["checklist"]},
                       "risk_units": round(abs(s["entry"] - s["stop"]) / SYMBOLS[s["symbol"]]["pip"], 1)})
    trades.sort(key=lambda x: x["exit_t"])
    by = lambda key: {k: summarize(v) for k, v in sorted(group(trades, key).items())}
    funnel = defaultdict(lambda: defaultdict(int))
    for s in setups:
        funnel[s["setup"]][s["stage"]] += 1
    checklist_effect = {}
    names = sorted({n for t in trades for n in t["checklist"]})
    for n in names:
        yes = [t for t in trades if t["checklist"].get(n) is True]
        no = [t for t in trades if t["checklist"].get(n) is False]
        checklist_effect[n] = {"pass": summarize(yes), "fail": summarize(no)}
    examples = {}
    for k, v in group(trades, lambda t: t["setup"]).items():
        examples[k] = [v[i] for i in sorted({0, len(v) // 3, 2 * len(v) // 3, len(v) - 1}) if i < len(v)]
    s3 = [t for t in trades if t["setup"] == "S3"]
    return {
        "part": part, "from": t0.isoformat(), "to": t1.isoformat(),
        "generated": datetime.now(timezone.utc).isoformat(),
        "assumptions": {
            "costs_price_units": COSTS,
            "fills": "Limits fill only if price trades beyond the limit; stop touched = loss; TP must be touched; stop and TP in one candle = loss.",
            "replay": "Daily replay through the live engine with only candles closed by each replay time. Each setup is frozen at its first final state.",
            "caveat": "W/D key zones used for TP2/TP3 and two checklist items can include up to 7 days of later data (entries, stops and TP1 cannot).",
        },
        "overall": summarize(trades),
        "by_setup": by(lambda t: t["setup"]),
        "by_setup_symbol": by(lambda t: f"{t['setup']} {t['symbol']}"),
        "by_symbol": by(lambda t: t["symbol"]),
        "by_month": by(lambda t: t["exit_t"][:7]),
        "by_score": by(lambda t: f"{t['setup']} score {'5+' if t['score'] >= 5 else '≤4'}"),
        "s3_bias": {"with H4 bias": summarize([t for t in s3 if t["checklist"].get("With H4 bias")]),
                    "against H4 bias": summarize([t for t in s3 if t["checklist"].get("With H4 bias") is False])},
        "checklist_effect": checklist_effect,
        "funnel": {k: dict(v) for k, v in funnel.items()},
        "examples": examples,
        "trades": trades,
    }


def group(items, key):
    g = defaultdict(list)
    for x in items:
        g[key(x)].append(x)
    return g


def run(part, overrides=None, label="baseline", publish=True):
    hist = Store(HIST_DB)
    rng = hist.get_meta("range")
    if not rng:
        raise SystemExit("No history: run 'python -m ictscan.backtest fetch' first")
    start, end = parse(rng["start"]), parse(rng["end"])
    split = start + (end - start) * IS_FRACTION
    t0, t1 = (start, split) if part == "in-sample" else (split, end)
    live = Store(DB_PATH)
    if part == "out-of-sample" and live.get_meta("backtest_oos_done"):
        raise SystemExit("The out-of-sample test has already been run once; re-running it would no longer be a fair test.")
    params = {**PARAMS, **(overrides or {})}
    setups = replay(t0, t1, params)
    rep = report(setups, part, t0, t1)
    rep["label"], rep["params"] = label, {k: params[k] for k in ("s1_stop", "s2_stop", "tp_mode", "enabled_setups")}
    (DATA_DIR / f"backtest-{part}-{label}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    slim = {k: v for k, v in rep.items() if k != "trades"}
    variants = live.get_meta(f"backtest_variants:{part}", {})
    variants[label] = {"params": rep["params"], "overall": rep["overall"], "by_setup": rep["by_setup"]}
    live.set_meta(f"backtest_variants:{part}", variants)
    if publish:
        live.set_meta(f"backtest:{part}", rep)
    if part == "out-of-sample":
        live.set_meta("backtest_oos_done", True)
    log.info("%s [%s] %s→%s: %s", part, label, t0.date(), t1.date(), rep["overall"])
    for k, v in rep["by_setup"].items():
        log.info("  %s: %s", k, v)
    return slim


def _parse_overrides(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        if k not in PARAMS:
            raise SystemExit(f"Unknown parameter {k}")
        out[k] = v.split(",") if isinstance(PARAMS[k], list) else type(PARAMS[k])(v) if not isinstance(PARAMS[k], str) else v
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--months", type=int, default=6)
    r = sub.add_parser("run")
    r.add_argument("--part", choices=["in-sample", "out-of-sample"], default="in-sample")
    r.add_argument("--label", default="baseline")
    r.add_argument("--set", action="append", help="parameter override, e.g. --set tp_mode=fixed_2r")
    r.add_argument("--no-publish", action="store_true", help="store as a variant only, not as the dashboard's main report")
    a = ap.parse_args()
    if a.cmd == "fetch":
        fetch(a.months)
    else:
        run(a.part, _parse_overrides(a.set), a.label, not a.no_publish)


if __name__ == "__main__":
    main()
