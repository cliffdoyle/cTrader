"""Event study ("measure first"): what happens after every M15/M5 BOS/CHoCH, by context.

Usage: python -m ictscan.research events      # build the event table for all history (causal features)
       python -m ictscan.research analyze     # aggregate the RESEARCH period only
"""
from __future__ import annotations

import bisect
import json
import logging
import statistics
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from itertools import product
from zoneinfo import ZoneInfo

from .backtest import COSTS, HIST_DB
from .config import DATA_DIR, PARAMS, SYMBOLS, TIMEFRAMES
from .engine.core import (Path, atr_series, dealing_range, equal_levels, find_fvgs, find_swings, index_at, make_bars,
                          order_blocks, period_levels, structure, swing_levels)
from .store import Store

NY = ZoneInfo("America/New_York")
log = logging.getLogger("ictscan.research")

# Fixed date splits (decided before any results were seen). The sealed part is the untouched out-of-sample period.
RESEARCH_END = datetime(2026, 6, 17, tzinfo=timezone.utc)
VALIDATION_END = datetime(2026, 9, 11, 19, 46, tzinfo=timezone.utc)
TARGETS = (1.0, 1.5, 2.0, 3.0)
TIMEOUT = timedelta(hours=48)
LOOKBACK = timedelta(hours=12)
EVENTS_PATH = DATA_DIR / "events.jsonl"


def session(t):
    h = t.astimezone(NY).hour + t.astimezone(NY).minute / 60
    if 2 <= h < 5:
        return "London 02-05"
    if 8.5 <= h < 11:
        return "NY AM 08:30-11"
    if 5 <= h < 8.5:
        return "pre-NY 05-08:30"
    if 11 <= h < 14:
        return "NY lunch 11-14"
    if 14 <= h < 17:
        return "NY PM 14-17"
    return "Asia/other"


def fast_sweeps(bars, levels):
    """A level can only be swept by the bar that first trades beyond it (its broken_t)."""
    starts = [b.t for b in bars]
    out = []
    for lv in levels:
        if lv.broken_t is None or (lv.until is not None and lv.broken_t >= lv.until):
            continue
        k = bisect.bisect_right(starts, lv.broken_t) - 1
        if k < 0 or bars[k].end <= lv.known_t:
            continue
        b, p = bars[k], lv.price
        hi = lv.side == "high"
        if (b.h > p and b.c < p) if hi else (b.l < p and b.c > p):
            out.append((b.end, lv.side, p))
        elif k + 1 < len(bars) and ((b.c > p and bars[k + 1].c < p) if hi else (b.c < p and bars[k + 1].c > p)):
            out.append((bars[k + 1].end, lv.side, p))
    return sorted(out)


def simulate(path_bars, starts, t0, entry, stop, d):
    """First-touch times of the stop and each target from t0; timeout mark-to-market. Same-candle = stop first."""
    risk = abs(entry - stop)
    tg = [entry + k * risk if d == "bullish" else entry - k * risk for k in TARGETS]
    hit = [None] * len(TARGETS)
    i = bisect.bisect_left(starts, t0)
    end_t = t0 + TIMEOUT
    last = entry
    for b in path_bars[i:]:
        if b.t >= end_t:
            break
        last = b.c
        if (b.l <= stop) if d == "bullish" else (b.h >= stop):
            return [(-1.0 if h is None else TARGETS[n]) for n, h in enumerate(hit)]
        for n, level in enumerate(tg):
            if hit[n] is None and ((b.h >= level) if d == "bullish" else (b.l <= level)):
                hit[n] = b.t
        if hit[-1] is not None:
            break
    mtm = ((last - entry) if d == "bullish" else (entry - last)) / risk
    return [(TARGETS[n] if h is not None else round(mtm, 3)) for n, h in enumerate(hit)]


def simulate_limit(path_bars, starts, t0, limit, stop, d, window):
    """Limit at `limit` valid for `window`; skipped if 1R from the limit trades first. Fill candle: stop checked first,
    targets only from the next candle (conservative)."""
    risk = abs(limit - stop)
    one_r = limit + risk if d == "bullish" else limit - risk
    i = bisect.bisect_left(starts, t0)
    for b in path_bars[i:]:
        if b.t >= t0 + window:
            return None
        filled = (b.l < limit) if d == "bullish" else (b.h > limit)
        if not filled:
            if (b.h >= one_r) if d == "bullish" else (b.l <= one_r):
                return None
            continue
        if (b.l <= stop) if d == "bullish" else (b.h >= stop):
            return [-1.0] * len(TARGETS)
        return simulate(path_bars, starts, b.end, limit, stop, d)
    return None


def build_symbol(sym):
    store = Store(HIST_DB)
    bars = {tf: make_bars(store.load_bars(sym, tf, 10**7), tf) for tf in TIMEFRAMES}
    p = PARAMS
    path = Path(bars)
    st, fv = {}, {}
    for tf in ("h4", "h1", "m15", "m5"):
        h, l = find_swings(bars[tf], p["swing_n"])
        st[tf] = (structure(bars[tf], h, l), h, l)
        fv[tf] = find_fvgs(bars[tf], p["fvg_mult"], p["fvg_lookback"])
    atr_h1 = atr_series(bars["h1"])
    h1s, h1h, h1l = st["h1"]
    levels = swing_levels(bars["h1"], h1h, h1l, "h1", path) + \
        equal_levels(bars["h1"], h1h, h1l, "h1", path, atr_h1, p["equal_tol_atr"]) + period_levels(bars["d1"], bars["w1"], path)
    sweeps = fast_sweeps(bars["h1"], levels)
    sweep_t = {"high": [s[0] for s in sweeps if s[1] == "high"], "low": [s[0] for s in sweeps if s[1] == "low"]}
    pois = []  # (start_touch_time, end_valid_time, dir, kind)
    for tf in ("h4", "h1"):
        b = bars[tf]
        for f in fv[tf]:
            if f.partial is None:
                continue
            tap = b[f.partial].t
            end = b[f.closed_through].end if f.closed_through is not None else None
            pois.append((tap, end, f.dir, f"{tf} FVG"))
        for ob in order_blocks(b, st[tf][0], fv[tf], tf, path):
            if ob.tap_t is not None:
                pois.append((ob.tap_t, ob.invalid_t, ob.dir, f"{tf} OB"))
    pois.sort(key=lambda x: x[0])
    poi_starts = [x[0] for x in pois]
    pb, ps = path.bars, path.starts
    cost = COSTS[sym]
    rows = []
    first_m5 = bars["m5"][0].t + timedelta(days=12)  # warm-up
    for tf in ("m15", "m5"):
        s, _, _ = st[tf]
        b = bars[tf]
        for e in s.events:
            if e.kind not in ("BOS", "CHoCH"):
                continue
            t = b[e.j].end
            if t < first_m5:
                continue
            d = e.dir
            j4, j1 = index_at(bars["h4"], t), index_at(bars["h1"], t)
            if j4 < 0 or j1 < 0:
                continue
            s4, s1 = st["h4"][0].state[j4], h1s.state[j1]
            r4, r1 = dealing_range(st["h4"][0], j4), dealing_range(h1s, j1)
            fav = lambda r: None if not r or r["high"] <= r["low"] else (
                (e.close - r["low"]) / (r["high"] - r["low"]) < 0.5 if d == "bullish" else (e.close - r["low"]) / (r["high"] - r["low"]) > 0.5)
            opp = "low" if d == "bullish" else "high"
            k = bisect.bisect_right(sweep_t[opp], t)
            swept = k > 0 and sweep_t[opp][k - 1] >= t - LOOKBACK
            lo = bisect.bisect_left(poi_starts, t - LOOKBACK)
            hi = bisect.bisect_right(poi_starts, t)
            tapped = sorted({x[3] for x in pois[lo:hi] if x[2] == d and (x[1] is None or x[1] > x[0])})
            atr = atr_h1[j1] or 1e-9
            row = {"sym": sym, "tf": tf, "t": t.isoformat(), "dir": d, "kind": e.kind, "entry": e.close,
                   "h4": "with" if s4 == d else ("against" if s4 in ("bullish", "bearish") else "none"),
                   "h1": "with" if s1 == d else ("against" if s1 in ("bullish", "bearish") else "none"),
                   "h4_pd": fav(r4), "h1_pd": fav(r1), "swept": swept, "poi": tapped, "session": session(t)}
            for name, stop in (("leg", e.prot), ("h1prot", h1s.prot[j1][0] if s1 == d and h1s.prot[j1] else None)):
                if stop is None or ((stop >= e.close) if d == "bullish" else (stop <= e.close)):
                    row[name] = None
                    continue
                risk = abs(e.close - stop)
                res = simulate(pb, ps, t, e.close, stop, d)
                row[name] = {"risk_atr": round(risk / atr, 3), "cost_r": round(cost / risk, 4),
                             "r": [round(x - cost / risk, 3) for x in res]}
            # Retracement entry: limit at 50% of the breaking leg (protected level → break close), stop at the leg extreme.
            limit = (e.prot + e.close) / 2
            risk = abs(limit - e.prot)
            res = simulate_limit(pb, ps, t, limit, e.prot, d, timedelta(minutes=12 * (15 if tf == "m15" else 5))) if risk > 0 else None
            row["ret"] = None if res is None else {"risk_atr": round(risk / atr, 3), "cost_r": round(cost / risk, 4),
                                                   "r": [round(x - cost / risk, 3) for x in res]}
            rows.append(row)
    return sym, rows


def build_events():
    with ProcessPoolExecutor(max_workers=6) as ex, open(EVENTS_PATH, "w", encoding="utf-8") as fh:
        for sym, rows in ex.map(build_symbol, list(SYMBOLS)):
            for r in rows:
                fh.write(json.dumps(r) + "\n")
            log.info("%s: %d events", sym, len(rows))


def load_events(t_from=None, t_to=None):
    out = []
    with open(EVENTS_PATH, encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            t = datetime.fromisoformat(r["t"])
            if (t_from and t < t_from) or (t_to and t >= t_to):
                continue
            out.append(r)
    return out


def stats(rs):
    if not rs:
        return None
    wins = sum(x > 0 for x in rs)
    gp = sum(x for x in rs if x > 0)
    gl = -sum(x for x in rs if x < 0)
    return {"n": len(rs), "win": round(wins / len(rs) * 100, 1), "avg": round(statistics.mean(rs), 3),
            "pf": round(gp / gl, 2) if gl else None, "tot": round(sum(rs), 1)}


def analyze(t_from=None, t_to=RESEARCH_END, max_cost=0.1, min_n=150):
    ev = load_events(t_from, t_to)
    print(f"events in period: {len(ev)}  ({t_from or 'start'} → {t_to})")
    dims = {
        "tf": lambda r: r["tf"], "kind": lambda r: r["kind"], "h4": lambda r: r["h4"], "h1": lambda r: r["h1"],
        "h4_pd": lambda r: r["h4_pd"], "h1_pd": lambda r: r["h1_pd"], "swept": lambda r: r["swept"],
        "poi": lambda r: bool(r["poi"]), "session": lambda r: r["session"],
    }
    results = []
    for stop_name, ti in product(("leg", "h1prot", "ret"), range(len(TARGETS))):
        base = [r for r in ev if r.get(stop_name) and r[stop_name]["cost_r"] <= max_cost]
        allst = stats([r[stop_name]["r"][ti] for r in base])
        print(f"\n=== stop={stop_name} target={TARGETS[ti]}R (cost ≤ {max_cost}R): ALL {allst}")
        for dn, fn in dims.items():
            g = defaultdict(list)
            for r in base:
                g[fn(r)].append(r[stop_name]["r"][ti])
            print("  " + dn + ": " + " | ".join(f"{k}: n={v['n']} avg={v['avg']:+.3f}" for k, v in
                                                 ((k, stats(x)) for k, x in sorted(g.items(), key=lambda kv: str(kv[0]))) if v))
        # combinations of the ICT filters
        for combo in product(("with", "any"), ("with", "any"), (True, None), (True, None), (True, None), (True, None)):
            h4, h1, pd, sw, poi, ses = combo
            sel = [r for r in base if (h4 == "any" or r["h4"] == "with") and (h1 == "any" or r["h1"] == "with")
                   and (pd is None or r["h1_pd"] is True) and (sw is None or r["swept"]) and (poi is None or r["poi"])
                   and (ses is None or r["session"] in ("London 02-05", "NY AM 08:30-11"))]
            for tf in ("m15", "m5"):
                xs = [r[stop_name]["r"][ti] for r in sel if r["tf"] == tf]
                s = stats(xs)
                if s and s["n"] >= min_n:
                    syms = defaultdict(list)
                    for r in sel:
                        if r["tf"] == tf:
                            syms[r["sym"]].append(r[stop_name]["r"][ti])
                    pos_syms = sum(1 for v in syms.values() if statistics.mean(v) > 0)
                    results.append({"stop": stop_name, "target": TARGETS[ti], "tf": tf,
                                    "filters": {"h4": h4, "h1": h1, "discount/premium": pd, "swept": sw, "poi": poi, "killzone": ses},
                                    **s, "symbols_positive": pos_syms})
    results.sort(key=lambda x: -x["avg"])
    print("\n=== Top filter combinations (n ≥ %d), research period ===" % min_n)
    for x in results[:25]:
        f = ", ".join(k for k, v in x["filters"].items() if v not in ("any", None))
        print(f"  {x['tf']} stop={x['stop']:6} tgt={x['target']}R  n={x['n']:5} win={x['win']:5}% avg={x['avg']:+.3f} pf={x['pf']} "
              f"symbols+={x['symbols_positive']}/6  [{f}]")
    (DATA_DIR / "research-combos.json").write_text(json.dumps(results, indent=1), encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cmd = sys.argv[1] if len(sys.argv) > 1 else "analyze"
    if cmd == "events":
        build_events()
    else:
        analyze()
