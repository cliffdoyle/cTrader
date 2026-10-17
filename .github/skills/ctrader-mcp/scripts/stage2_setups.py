"""Stage 2 entry-setup finder (prompt.md). Reuses ict_analysis definitions.
Usage: python stage2_setups.py <stage1_bars.json> <stage2_bars.json> <out_dir> SYMBOL:bias [SYMBOL:bias ...]

Setup = BOS/CHoCH on the entry TF in the bias direction whose leg origin (extreme between the broken
swing and the break candle) either tapped an HTF FVG that was still unfilled, or swept liquidity
(wick beyond an intact level, close back inside) within the origin candle or the 2 before it.
Entry = consequent encroachment (50%) of the entry-TF FVG formed in the breaking leg.
Stop = origin extreme. Target = nearest intact opposing liquidity beyond entry.
Fill/outcome checks are conservative: if stop and target/entry are hit in the same candle, the
worse outcome is assumed and flagged.
"""
import json
import sys
from datetime import timedelta
from pathlib import Path

from ict_analysis import (analyse, daily_levels, fvgs, liquidity_levels_for, ny, parse, pip_size,
                          structure_breaks, swings, trading_date)

DUR = {"m5": 300, "m15": 900, "h1": 3600, "h4": 14400, "d1": 86400}
SWEEP_LOOKBACK = 2
# Optional interpretation: also accept a sweep made by the H4 candle containing the origin.
HTF_SWEEP = False


def bar_rows(bars, tf):
    d = timedelta(seconds=DUR[tf])
    return [{"start": parse(b["timestamp"]), "end": parse(b["timestamp"]) + d, **b} for b in bars]


def build_path(sources):
    """sources: list of row-lists from coarse to fine. Finer data replaces coarser where it exists."""
    path = []
    for rows in sources:
        if not rows:
            continue
        cutoff = rows[0]["start"]
        path = [r for r in path if r["end"] <= cutoff] + rows
    return path


def extreme_between(path, t0, t1, side):
    vals = [r["high"] if side == "high" else r["low"] for r in path if r["start"] >= t0 and r["end"] <= t1]
    if not vals:
        return None
    return max(vals) if side == "high" else min(vals)


def intact(path, level, side, t0, t1):
    ext = extreme_between(path, t0, t1, side)
    if ext is None:
        return True
    return ext <= level if side == "high" else ext >= level


def swing_levels(rows, highs, lows, tf, label):
    out = []
    for s, side in [(h, "high") for h in highs] + [(lw, "low") for lw in lows]:
        out.append({"name": f"{label} swing {side}", "side": side, "price": s["price"],
                    "formed": rows[s["i"]]["end"], "known": rows[s["confirmed_at"]]["end"]})
    return out


def fvg_status_at(path, f, t_formed, t_until):
    side = "high" if f["dir"] == "bearish" else "low"
    ext = extreme_between(path, t_formed, t_until, side)
    if ext is None:
        return "unfilled"
    if f["dir"] == "bearish":
        return "filled" if ext >= f["high"] else ("partly filled" if ext > f["low"] else "unfilled")
    return "filled" if ext <= f["low"] else ("partly filled" if ext < f["high"] else "unfilled")


def htf_fvgs(rows, tf, pip, label):
    out = []
    for f in fvgs(rows, pip):
        out.append({**f, "tf": label, "formed": rows[f["i"] + 1]["end"], "start": rows[f["i"] - 1]["start"]})
    return out


def evaluate(sym, bias, s1, s2):
    pip, unit = pip_size(sym, s1["details"])
    rows = {tf: bar_rows(s1[tf] if tf in ("d1", "h4") else s2[tf], tf) for tf in ("d1", "h4", "h1", "m15", "m5")}
    ctx = analyse(sym, s1)
    days, weeks = daily_levels(s1["d1"])
    rg = ctx["range"]
    d1_trend = ctx["D1"]["trend"]
    end_time = rows["m5"][-1]["end"]

    htf = {}
    for tf, label in (("d1", "D1"), ("h4", "H4"), ("h1", "H1")):
        h, lw = swings(rows[tf])
        htf[tf] = {"levels": swing_levels(rows[tf], h, lw, tf, label),
                   "fvgs": htf_fvgs(rows[tf], tf, pip, label)}

    want_side = "high" if bias == "bearish" else "low"     # side swept at origin
    tgt_side = "low" if bias == "bearish" else "high"       # liquidity targeted
    fvg_dir = bias

    results = {}
    for tf in ("h1", "m15", "m5"):
        r = rows[tf]
        path = build_path([rows["d1"], rows["h4"], rows["h1"], rows["m15"] if tf == "m5" else [], r])
        hs, ls = swings(r)
        events, state = structure_breaks(r, hs, ls)
        own_levels = swing_levels(r, hs, ls, tf, tf.upper())
        own_fvgs = fvgs(r, pip)
        higher = [x for x in ("d1", "h4", "h1") if DUR[x] > DUR[tf]]
        setups = []
        for e in events:
            if e["dir"] != bias or e["kind"] not in ("BOS", "CHoCH"):
                continue
            j, si = e["j"], e["swing_i"]
            span = range(si, j + 1)
            o = max(span, key=lambda k: r[k]["high"]) if bias == "bearish" else min(span, key=lambda k: r[k]["low"])
            ob = r[o]
            origin_px = ob["high"] if bias == "bearish" else ob["low"]

            taps = []
            for x in higher:
                for f in htf[x]["fvgs"]:
                    if f["dir"] != fvg_dir or f["formed"] > ob["start"]:
                        continue
                    entered = (ob["high"] >= f["low"] and ob["close"] <= f["high"]) if bias == "bearish" \
                        else (ob["low"] <= f["high"] and ob["close"] >= f["low"])
                    if not entered:
                        continue
                    st = fvg_status_at(path, f, f["formed"], ob["start"])
                    taps.append({"tf": f["tf"], "low": f["low"], "high": f["high"], "status_before": st,
                                 "formed_ny": ny(f["start"].isoformat())})
            tap_ok = any(t["status_before"] == "unfilled" for t in taps)

            sweeps = []
            for k in range(max(0, o - SWEEP_LOOKBACK), o + 1):
                b = r[k]
                cands = [lv for lv in own_levels if lv["side"] == want_side and own_levels and lv["known"] <= b["start"]]
                for x in higher:
                    cands += [lv for lv in htf[x]["levels"] if lv["side"] == want_side and lv["known"] <= b["start"]]
                td_start = next(rr["start"] for rr in rows["h4"] if trading_date(rr["timestamp"]) == trading_date(b["timestamp"]))
                for name, side, price in liquidity_levels_for(b["timestamp"], days, weeks):
                    if side == want_side:
                        cands.append({"name": name, "side": side, "price": price, "formed": td_start, "known": td_start})
                for lv in cands:
                    if not intact(path, lv["price"], want_side, lv["formed"], b["start"]):
                        continue
                    hit = (b["high"] > lv["price"] and b["close"] < lv["price"]) if bias == "bearish" \
                        else (b["low"] < lv["price"] and b["close"] > lv["price"])
                    if hit:
                        sweeps.append({"k": k, "name": lv["name"], "level": lv["price"], "time_ny": ny(b["timestamp"])})
            dedup = {}
            for sw in sweeps:
                dedup.setdefault((sw["name"], sw["level"]), sw)
            if HTF_SWEEP:
                for x in ("h4",):
                    if DUR[x] <= DUR[tf]:
                        continue
                    hb = next((rr for rr in rows[x] if rr["start"] <= ob["start"] < rr["end"]), None)
                    if hb is None:
                        continue
                    cands = [lv for y in ("d1", "h4") for lv in htf[y]["levels"] if lv["side"] == want_side and lv["known"] <= hb["start"]]
                    for name, side, price in liquidity_levels_for(hb["timestamp"], days, weeks):
                        if side == want_side:
                            cands.append({"name": name, "side": side, "price": price, "formed": hb["start"], "known": hb["start"]})
                    for lv in cands:
                        if not intact(path, lv["price"], want_side, lv["formed"], hb["start"]):
                            continue
                        hit = (hb["high"] > lv["price"] and hb["close"] < lv["price"]) if bias == "bearish" \
                            else (hb["low"] < lv["price"] and hb["close"] > lv["price"])
                        if hit:
                            dedup.setdefault((lv["name"], lv["price"]), {"k": None, "name": lv["name"] + " (swept by H4 candle)",
                                                                         "level": lv["price"], "time_ny": ny(hb["timestamp"])})
            sweeps = list(dedup.values())

            if not (tap_ok or sweeps):
                continue

            leg = [f for f in own_fvgs if f["dir"] == fvg_dir and o < f["i"] <= j + 1]
            if not leg:
                setups.append({"j": j, "kind": e["kind"], "origin": o, "status": "no entry FVG in breaking leg",
                               "taps": taps, "sweeps": sweeps, "event": e})
                continue
            f = max(leg, key=lambda x: x["i"])
            entry = (f["low"] + f["high"]) / 2
            stop = origin_px
            ready = r[f["i"] + 1]["end"]

            tcands = []
            for lv in own_levels + [lv for x in ("d1", "h4", "h1") for lv in htf[x]["levels"]]:
                if lv["side"] == tgt_side and lv["known"] <= ready:
                    tcands.append(lv)
            nxt = trading_date(r[-1]["timestamp"]) + timedelta(days=1)
            while nxt.weekday() >= 5:
                nxt += timedelta(days=1)
            for name, side, price in liquidity_levels_for(rows["d1"][-1]["timestamp"], days, weeks):
                if side == tgt_side:
                    tcands.append({"name": name, "side": side, "price": price, "formed": rows["d1"][-1]["start"], "known": ready})
            for name, side, price in [("PDH", "high", rows["d1"][-1]["high"]), ("PDL", "low", rows["d1"][-1]["low"])]:
                if side == tgt_side:
                    tcands.append({"name": f"{name} (for {nxt})", "side": side, "price": price, "formed": rows["d1"][-1]["end"], "known": rows["d1"][-1]["end"]})
            beyond = [lv for lv in tcands
                      if (lv["price"] < entry if bias == "bearish" else lv["price"] > entry)
                      and intact(path, lv["price"], tgt_side, lv["formed"], ready)]
            if not beyond:
                target = None
            elif bias == "bearish":
                target = max(beyond, key=lambda x: x["price"])
            else:
                target = min(beyond, key=lambda x: x["price"])

            status, flag, t_entry = "pending", "", None
            for k in range(f["i"] + 2, len(r)):
                b = r[k]
                if status == "pending":
                    if target and ((b["low"] <= target["price"]) if bias == "bearish" else (b["high"] >= target["price"])) \
                            and not ((b["high"] >= entry) if bias == "bearish" else (b["low"] <= entry)):
                        status = "missed (target reached before entry)"
                        break
                    if (b["high"] >= entry) if bias == "bearish" else (b["low"] <= entry):
                        status, t_entry = "triggered", b["timestamp"]
                        if (b["high"] >= stop) if bias == "bearish" else (b["low"] <= stop):
                            status = "stopped"
                            flag = "entry and stop in same candle"
                            break
                        if target and ((b["low"] <= target["price"]) if bias == "bearish" else (b["high"] >= target["price"])):
                            status, flag = "ambiguous", "entry and target in same candle"
                            break
                        continue
                if status == "triggered":
                    hit_stop = (b["high"] >= stop) if bias == "bearish" else (b["low"] <= stop)
                    hit_tgt = target and ((b["low"] <= target["price"]) if bias == "bearish" else (b["high"] >= target["price"]))
                    if hit_stop:
                        status = "stopped"
                        if hit_tgt:
                            flag = "stop and target in same candle (assumed stop)"
                        break
                    if hit_tgt:
                        status = "target hit"
                        break

            risk = abs(stop - entry)
            rr = abs(entry - target["price"]) / risk if target and risk else None
            in_zone = entry > rg["mid"] if bias == "bearish" else entry < rg["mid"]
            fvg_now = next((g["status"] for g in own_fvgs if g["i"] == f["i"]), "?")
            crit = [
                ("H4 bias direction", True, f"{e['kind']} {bias}"),
                ("D1 agrees", d1_trend == bias, f"D1 {d1_trend}"),
                ("From unfilled HTF FVG", tap_ok, "; ".join(f"{t['tf']} {t['low']}-{t['high']} ({t['status_before']} before tap)" for t in taps) or "no HTF FVG tapped"),
                ("From liquidity sweep", bool(sweeps), "; ".join(f"{s['name']} {s['level']} @ {s['time_ny']}" for s in sweeps) or "no sweep at origin"),
                ("Entry in " + ("premium" if bias == "bearish" else "discount"), in_zone,
                 f"entry {entry:.{6 if pip < 0.001 else 3 if pip < 0.1 else 1}f} vs H4 50% {rg['mid']:.6g}"),
                ("Setup still live", status in ("pending", "triggered"), status + (f" [{flag}]" if flag else "")),
            ]
            setups.append({
                "tf": tf, "j": j, "kind": e["kind"], "break_time_ny": ny(r[j]["timestamp"]), "break_close": e["close"],
                "broken_level": e["level"], "broken_swing_time_ny": ny(r[si]["timestamp"]), "broken_swing_ts": r[si]["timestamp"],
                "break_ts": r[j]["timestamp"],
                "origin": o, "origin_time_ny": ny(ob["timestamp"]), "origin_px": origin_px, "origin_ts": ob["timestamp"],
                "fvg": {"i": f["i"], "low": f["low"], "high": f["high"], "time_ny": ny(r[f["i"]]["timestamp"]),
                        "c1_ts": r[f["i"] - 1]["timestamp"], "c3_end": r[f["i"] + 1]["end"].isoformat(), "status_now": fvg_now,
                        "size": f["size_pips"]},
                "entry": entry, "stop": stop, "target": target and {"name": target["name"], "price": target["price"]},
                "risk": risk / pip, "rr": rr, "status": status, "flag": flag, "entry_time_ny": t_entry and ny(t_entry),
                "criteria": [[a, b2, c] for a, b2, c in crit], "score": sum(1 for _, ok, _ in crit if ok),
            })
        last_event = events[-1] if events else None
        results[tf] = {
            "trend": __import__("ict_analysis").trend_from(hs, ls), "state": state,
            "last_event": last_event and {**last_event, "time_ny": ny(r[last_event["j"]]["timestamp"]),
                                          "ts": r[last_event["j"]]["timestamp"], "swing_ts": r[last_event["swing_i"]]["timestamp"],
                                          "swing_time_ny": ny(r[last_event["swing_i"]]["timestamp"])},
            "unfilled_fvgs": [{**g, "time_ny": ny(r[g["i"]]["timestamp"]), "c1_ts": r[g["i"] - 1]["timestamp"]}
                              for g in own_fvgs if g["status"] == "unfilled"],
            "setups": setups,
        }

    h4_open = [{**f, "start": f["start"].isoformat(), "formed": f["formed"].isoformat(),
                "status_now": fvg_status_at(build_path([rows["d1"], rows["h4"], rows["h1"], rows["m15"], rows["m5"]]), f, f["formed"], end_time)}
               for f in htf["h4"]["fvgs"] + htf["d1"]["fvgs"] if f["dir"] == fvg_dir]
    h4_open = [f for f in h4_open if f["status_now"] != "filled"]
    last = rows["m5"][-1]
    return {"symbol": sym, "bias": bias, "pip": pip, "unit": unit, "d1_trend": d1_trend, "h4_trend": ctx["H4"]["trend"],
            "price": last["close"], "price_time_ny": ny(last["timestamp"]), "last_ts": last["timestamp"],
            "range": rg, "atr_h4": ctx["atr_h4"] / pip,
            "pdh": rows["d1"][-1]["high"], "pdl": rows["d1"][-1]["low"],
            "pwh": max(b["high"] for b in rows["d1"] if trading_date(b["timestamp"]).isocalendar()[:2] == trading_date(rows["d1"][-1]["timestamp"]).isocalendar()[:2]),
            "pwl": min(b["low"] for b in rows["d1"] if trading_date(b["timestamp"]).isocalendar()[:2] == trading_date(rows["d1"][-1]["timestamp"]).isocalendar()[:2]),
            "htf_open_fvgs": h4_open, "tf": results}


def main():
    global HTF_SWEEP
    args = [a for a in sys.argv[1:] if a != "--htf-sweep"]
    HTF_SWEEP = "--htf-sweep" in sys.argv
    s1 = json.loads(Path(args[0]).read_text(encoding="utf-8-sig"))["symbols"]
    s2 = json.loads(Path(args[1]).read_text(encoding="utf-8-sig"))["symbols"]
    out = Path(args[2])
    allres = {}
    for arg in args[3:]:
        sym, bias = arg.split(":")
        res = evaluate(sym, bias, s1[sym], s2[sym])
        res["open0930"] = [{"time_ny": ny(b["timestamp"]), "high": b["high"], "low": b["low"],
                            "range": (b["high"] - b["low"]) / res["pip"]} for b in s2[sym]["open0930"]]
        allres[sym] = res
        print(f"===== {sym} bias {bias} | D1 {res['d1_trend']} H4 {res['h4_trend']} | last {res['price']} @ {res['price_time_ny']}")
        print(f"H4 range {res['range']['low']}-{res['range']['high']} 50% {res['range']['mid']:.6g} | ATR H4 {res['atr_h4']:.1f} {res['unit']} | Fri D1 H {res['pdh']} L {res['pdl']} | week H {res['pwh']} L {res['pwl']}")
        for f in res["htf_open_fvgs"]:
            print(f"  HTF {f['dir']} FVG {f['tf']} {f['low']}-{f['high']} formed {ny(f['start'])} now {f['status_now']}")
        for tf, t in res["tf"].items():
            le = t["last_event"]
            print(f"[{tf}] trend {t['trend']} state {t['state']} | last event {le and (le['kind'] + ' ' + le['dir'] + ' ' + le['time_ny'] + ' close ' + str(le['close']) + ' vs ' + str(le['level']))}")
            for g in t["unfilled_fvgs"][-3:]:
                print(f"   unfilled {g['dir']} FVG {g['low']}-{g['high']} @ {g['time_ny']} ({g['size_pips']:.1f})")
            for s in t["setups"]:
                if "entry" not in s:
                    print(f"   setup {s['kind']} #{s['j']}: {s['status']}")
                    continue
                tg = s["target"]
                print(f"   SETUP {s['kind']} @ {s['break_time_ny']} close {s['break_close']} vs {s['broken_level']} | origin {s['origin_px']} @ {s['origin_time_ny']} | "
                      f"FVG {s['fvg']['low']}-{s['fvg']['high']} @ {s['fvg']['time_ny']} ({s['fvg']['status_now']}) | entry {s['entry']:.6g} stop {s['stop']} "
                      f"target {tg and (tg['name'] + ' ' + str(tg['price']))} RR {s['rr'] and round(s['rr'], 2)} risk {s['risk']:.1f} | {s['status']} {s['flag']} | score {s['score']}/6")
                for c in s["criteria"]:
                    print(f"       {'+' if c[1] else '-'} {c[0]}: {c[2]}")
        for o in res["open0930"]:
            print(f"  09:30 M5 {o['time_ny']}: H {o['high']} L {o['low']} ({o['range']:.1f} {res['unit']})")
    (out / ("stage2_results_htfsweep.json" if HTF_SWEEP else "stage2_results.json")).write_text(json.dumps(allres, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
