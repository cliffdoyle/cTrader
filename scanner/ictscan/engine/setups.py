"""Setup detectors S1–S4 (strategy-spec.md §4). Each detector replays history candle by candle up to ctx.now and
returns setup records with a stage timeline, so a restart reproduces exactly the same states."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from .core import dealing_range, find_sweeps

NY = ZoneInfo("America/New_York")
NAMES = {
    "S1": "HTF order block + LTF CHoCH",
    "S2": "Trend pullback: discount FVG + LTF sweep",
    "S3": "H1 liquidity sweep + M5 BOS/FVG",
    "S4": "Equal highs/lows + FVG limit",
}
FINAL = {"target", "stopped", "invalidated", "expired", "missed"}


def ny(t):
    return t.astimezone(NY).strftime("%a %d %b %H:%M")


def _bull(d):
    return d == "bullish"


def new_setup(ctx, kind, direction, poi_tf, conf_tf, key_t, poi):
    return {
        "id": f"{ctx.symbol}-{kind}-{poi_tf}-{direction[:4]}-{int(key_t.timestamp())}",
        "symbol": ctx.symbol, "setup": kind, "name": NAMES[kind], "dir": direction,
        "poi_tf": poi_tf, "confirm_tf": conf_tf, "key_t": key_t.isoformat(), "poi": poi,
        "timeline": [], "stage": None, "stage_t": None,
        "entry": None, "stop": None, "tp1": None, "tp1_name": None, "tp2": None, "tp3": None,
        "rr": None, "result_r": None, "checklist": [], "score": 0, "reasons": [],
    }


def stage(s, name, t, note, price=None):
    s["timeline"].append({"stage": name, "t": t.isoformat(), "ny": ny(t), "note": note, "price": price})
    s["stage"], s["stage_t"] = name, t.isoformat()


def set_targets(ctx, s, tfs, t):
    d, entry, stop = s["dir"], s["entry"], s["stop"]
    risk = abs(entry - stop)
    lv = None if ctx.p.get("tp_mode") == "fixed_2r" else ctx.nearest_target(tfs, d, entry, t)
    if lv is not None:
        s["tp1"], s["tp1_name"] = ctx.fmt(lv.price), lv.name
    else:
        s["tp1"] = ctx.fmt(entry + 2 * risk if _bull(d) else entry - 2 * risk)
        s["tp1_name"] = "fixed 2R" if ctx.p.get("tp_mode") == "fixed_2r" else "2R (no liquidity found)"
    beyond = s["tp1"]
    z2 = ctx.zone_target("d1", d, beyond)
    z3 = ctx.zone_target("w1", d, beyond)
    s["tp2"] = ctx.fmt((z2["low"] if _bull(d) else z2["high"])) if z2 else None
    s["tp3"] = ctx.fmt((z3["low"] if _bull(d) else z3["high"])) if z3 else None
    s["rr"] = round(abs(s["tp1"] - entry) / risk, 2) if risk else None


def run_trade(ctx, s, start_t):
    d, stop, tp = s["dir"], s["stop"], s["tp1"]
    for b in ctx.path.bars_from(start_t):
        hit_stop = b.l <= stop if _bull(d) else b.h >= stop
        hit_tp = b.h >= tp if _bull(d) else b.l <= tp
        if hit_stop:
            stage(s, "stopped", b.end, "Stop hit" + (" (TP1 was also inside this candle; counted as a loss)" if hit_tp else ""), stop)
            s["result_r"] = -1.0
            return
        if hit_tp:
            stage(s, "target", b.end, f"TP1 reached ({s['tp1_name']})", tp)
            s["result_r"] = s["rr"]
            return


def run_limit(ctx, s, ready_t, expire_t, invalid_t=None):
    d, entry, tp = s["dir"], s["entry"], s["tp1"]
    for b in ctx.path.bars_from(ready_t):
        if b.t >= expire_t:
            stage(s, "expired", expire_t, "Limit not filled in time")
            return
        if invalid_t is not None and b.end > invalid_t:
            stage(s, "invalidated", invalid_t, "Zone closed through before the limit filled")
            return
        filled = b.l < entry if _bull(d) else b.h > entry  # spec §7: price must trade beyond the limit
        if not filled and (b.h >= tp if _bull(d) else b.l <= tp):
            stage(s, "missed", b.end, "TP1 traded before the limit filled")
            return
        if filled:
            stage(s, "triggered", b.end, f"Limit filled at {entry}", entry)
            if b.l <= s["stop"] if _bull(d) else b.h >= s["stop"]:
                stage(s, "stopped", b.end, "Stop hit in the fill candle", s["stop"])
                s["result_r"] = -1.0
                return
            run_trade(ctx, s, b.end)
            return
    if expire_t <= ctx.now:
        stage(s, "expired", expire_t, "Limit not filled in time")


def checklist(ctx, s, t, poi_ok, swept):
    d, p = s["dir"], ctx.p
    h4, h1 = ctx.state_at("h4", t), ctx.state_at("h1", t)
    items = [
        ("With H4 bias", h4 == d, f"H4 is {h4}"),
        ("H1 aligned", h1 == d, f"H1 is {h1}"),
        ("POI in " + ("discount" if _bull(d) else "premium"), poi_ok[0], poi_ok[1]),
        ("Liquidity swept before entry", swept[0], swept[1]),
    ]
    lo, hi = s["poi"]["low"], s["poi"]["high"]
    pad = 0.25 * ctx.atr["d1"][-1]
    ov = [z for z in ctx.zones_w + ctx.zones_d
          if z["strength"] >= p["zone_min_strength"] and z["low"] - pad <= hi and z["high"] + pad >= lo]
    items.append(("POI at a W/D key level", bool(ov),
                  "; ".join(f"{z['tf'].upper()} zone {ctx.fmt(z['low'])}-{ctx.fmt(z['high'])} (strength {z['strength']}/5)" for z in ov[:2])
                  or "no weekly/daily zone at the POI"))
    if s["entry"] is None:
        items.append((f"Room ≥ {p['room_r']:g}R to next key level", None, "pending entry"))
        items.append((f"R:R to TP1 ≥ {p['min_rr']:g}", None, "pending entry"))
    else:
        risk = abs(s["entry"] - s["stop"])
        opp = [z for z in (ctx.zone_target("d1", d, s["entry"]), ctx.zone_target("w1", d, s["entry"])) if z]
        if opp:
            edge = min((z["low"] for z in opp)) if _bull(d) else max((z["high"] for z in opp))
            room = abs(edge - s["entry"]) / risk if risk else 0
            items.append((f"Room ≥ {p['room_r']:g}R to next key level", room >= p["room_r"], f"{room:.1f}R to {ctx.fmt(edge)}"))
        else:
            items.append((f"Room ≥ {p['room_r']:g}R to next key level", True, "no opposing W/D zone nearby"))
        items.append((f"R:R to TP1 ≥ {p['min_rr']:g}", (s["rr"] or 0) >= p["min_rr"], f"{s['rr']}R to {s['tp1_name']} {s['tp1']}"))
    s["checklist"] = [{"name": n, "ok": ok, "detail": det} for n, ok, det in items]
    s["score"] = sum(1 for _, ok, _ in items if ok is True)


def _swept_between(ctx, tf, side, t0, t1):
    bars = ctx.bars[tf]
    start = max(ctx.idx(tf, t0), 0)
    lv = [x for x in ctx.liq[tf] if x.side == side]
    hits = [sw for sw in find_sweeps(bars, lv, start) if t0 <= sw.bar_t and sw.t <= t1]
    if not hits:
        return False, f"no {tf.upper()} {'sell' if side == 'low' else 'buy'}-side sweep before entry"
    h = hits[-1]
    return True, f"{h.level.name} {ctx.fmt(h.level.price)} swept {ny(h.bar_t)} ({h.kind})"


def detect_s1(ctx, since):
    out, p = [], ctx.p
    for poi_tf, conf_tf in (("h4", "m15"), ("h1", "m5")):
        cb, cst = ctx.bars[conf_tf], ctx.st[conf_tf]
        for ob in ctx.obs[poi_tf]:
            if ob.formed_t < max(since, ctx.now - timedelta(days=p["s1_lookback_days"][poi_tf])):
                continue
            d = ob.dir
            if ctx.state_at("h4", ob.formed_t) != d:
                continue
            rng = dealing_range(ctx.st[poi_tf], ctx.idx(poi_tf, ob.formed_t))
            if not rng:
                continue
            mid = (ob.low + ob.high) / 2
            if (mid >= rng["mid"]) if _bull(d) else (mid <= rng["mid"]):
                continue
            a = ctx.atr_at(poi_tf, ob.formed_t)
            near = ob.near + (a if _bull(d) else -a) * p["s1_watch_atr"]
            wb = ctx.path.first_touch("low" if _bull(d) else "high", near, ob.formed_t)
            if wb is None:
                continue
            s = new_setup(ctx, "S1", d, poi_tf, conf_tf, ob.formed_t, {
                "kind": f"{poi_tf.upper()} order block", "tf": poi_tf, "low": ctx.fmt(ob.low), "high": ctx.fmt(ob.high),
                "formed": ob.formed_t.isoformat(), "candle": ctx.bars[poi_tf][ob.i].t.isoformat(),
                "range": {k: ctx.fmt(v) for k, v in rng.items() if k != "state"}})
            ev = ob.event
            s["reasons"] = [
                f"{poi_tf.upper()} {ev.kind} {d} on the {ny(ctx.bars[poi_tf][ev.j].t)} candle: close {ctx.fmt(ev.close)} beyond {ctx.fmt(ev.level)}.",
                f"Order block = displacement candle of {ny(ctx.bars[poi_tf][ob.i].t)} ({ctx.fmt(ob.low)}–{ctx.fmt(ob.high)}), "
                f"in {'discount' if _bull(d) else 'premium'} of the {poi_tf.upper()} range {ctx.fmt(rng['low'])}–{ctx.fmt(rng['high'])} (50% {ctx.fmt(rng['mid'])}).",
                f"H4 bias {d}. Waiting for price to tap the block and a {conf_tf.upper()} CHoCH {d}.",
            ]
            stage(s, "watch", max(wb.end, ob.formed_t), f"Price within {p['s1_watch_atr']:g} ATR of the {poi_tf.upper()} order block")
            inv = ob.invalid_t
            if ob.tap_t is None or (inv is not None and ob.tap_t >= inv):
                if inv is not None and inv <= ctx.now:
                    stage(s, "invalidated", inv, f"{poi_tf.upper()} candle closed beyond the order block")
                checklist(ctx, s, ctx.now, (True, "order block in the correct half"), (None, "pending"))
                out.append(s)
                continue
            stage(s, "armed", ob.tap_t, f"Price traded into the {poi_tf.upper()} order block")
            if ob.tap_t < cb[0].t:
                checklist(ctx, s, ctx.now, (True, "order block in the correct half"), (None, "pending"))
                out.append(s)
                continue
            expiry = ob.tap_t + timedelta(hours=p["s1_expiry_hours"])
            tol = p["s1_leg_tolerance_atr"] * a
            trig = None
            for e in cst.events:
                be = cb[e.j].end
                if e.kind != "CHoCH" or e.dir != d or be <= ob.tap_t:
                    continue
                if (inv is not None and be > inv) or be > expiry:
                    break
                if (e.prot > ob.high + tol) if _bull(d) else (e.prot < ob.low - tol):
                    continue
                if ctx.state_at("h4", be) != d:
                    continue
                trig = e
                break
            if trig is None:
                ends = sorted([x for x in ((inv, "invalidated", f"{poi_tf.upper()} candle closed beyond the order block"),
                                           (expiry, "expired", f"No {conf_tf.upper()} CHoCH within {p['s1_expiry_hours']}h of the tap")) if x[0]])
                for tt, name, note in ends:
                    if tt <= ctx.now:
                        stage(s, name, tt, note)
                        break
                checklist(ctx, s, ctx.now, (True, "order block in the correct half"), (None, "pending"))
                out.append(s)
                continue
            be = cb[trig.j].end
            buf = ctx.cfg["buffer"]
            s["entry"] = ctx.fmt(trig.close)
            if p.get("s1_stop") == "ob_far":
                s["stop"] = ctx.fmt(ob.low - buf if _bull(d) else ob.high + buf)
            else:
                s["stop"] = ctx.fmt(trig.prot - buf if _bull(d) else trig.prot + buf)
            if (s["entry"] <= s["stop"]) if _bull(d) else (s["entry"] >= s["stop"]):
                continue
            set_targets(ctx, s, [poi_tf], be)
            stop_desc = "beyond the order block's far edge" if p.get("s1_stop") == "ob_far" else f"beyond the {conf_tf.upper()} leg extreme {ctx.fmt(trig.prot)}"
            s["reasons"].append(f"{conf_tf.upper()} CHoCH {d} on the {ny(cb[trig.j].t)} candle: close {ctx.fmt(trig.close)} beyond {ctx.fmt(trig.level)}; "
                                f"stop {stop_desc}.")
            stage(s, "triggered", be, f"{conf_tf.upper()} CHoCH confirmed; entry at candle close", s["entry"])
            checklist(ctx, s, be, (True, "order block in the correct half"),
                      _swept_between(ctx, conf_tf, "low" if _bull(d) else "high", ob.tap_t, be))
            run_trade(ctx, s, be)
            out.append(s)
    return out


def detect_s2(ctx, since):
    out = []
    hb, st = ctx.bars["h1"], ctx.st["h1"]
    m5 = ctx.bars["m5"]
    for n, e in enumerate(st.events):
        et = hb[e.j].end
        if e.kind not in ("BOS", "CHoCH") or et < since:
            continue
        d = e.dir
        if ctx.state_at("h4", et) != d:
            continue
        leg_ext = max(b.h for b in hb[e.origin_i:e.j + 1]) if _bull(d) else min(b.l for b in hb[e.origin_i:e.j + 1])
        lo, hi = (e.prot, leg_ext) if _bull(d) else (leg_ext, e.prot)
        mid = (lo + hi) / 2
        cands = [f for f in ctx.fvg["h1"] if f.dir == d and e.origin_i <= f.i <= e.j
                 and (((f.low + f.high) / 2 < mid) if _bull(d) else ((f.low + f.high) / 2 > mid))]
        if not cands:
            continue
        f = max(cands, key=lambda x: x.high) if _bull(d) else min(cands, key=lambda x: x.low)
        fidx = max(f.k, e.j)
        if f.closed_through is not None and f.closed_through <= fidx:
            continue
        formed_t = hb[fidx].end
        nxt = next((x for x in st.events[n + 1:]), None)
        end_t = hb[nxt.j].end if nxt else None
        ct = hb[f.closed_through].end if f.closed_through is not None else None
        s = new_setup(ctx, "S2", d, "h1", "m5", formed_t, {
            "kind": "H1 FVG (discount)" if _bull(d) else "H1 FVG (premium)", "tf": "h1",
            "low": ctx.fmt(f.low), "high": ctx.fmt(f.high), "formed": formed_t.isoformat(),
            "candle": hb[f.i].t.isoformat(), "range": {"low": ctx.fmt(lo), "high": ctx.fmt(hi), "mid": ctx.fmt(mid)}})
        s["reasons"] = [
            f"H1 {e.kind} {d} on the {ny(hb[e.j].t)} candle (close {ctx.fmt(e.close)} beyond {ctx.fmt(e.level)}); protected {'low' if _bull(d) else 'high'} {ctx.fmt(e.prot)}.",
            f"H1 FVG {ctx.fmt(f.low)}–{ctx.fmt(f.high)} sits in {'discount' if _bull(d) else 'premium'} of the leg {ctx.fmt(lo)}–{ctx.fmt(hi)} (50% {ctx.fmt(mid)}).",
            f"H4 bias {d}. Waiting for price to enter the FVG and an M5 {'sell' if _bull(d) else 'buy'}-side sweep inside it.",
        ]
        stage(s, "watch", formed_t, "Discount FVG identified after the H1 break" if _bull(d) else "Premium FVG identified after the H1 break")
        stops = [x for x in ((ct, "invalidated", "H1 candle closed through the FVG"),
                             (end_t, "expired", "A newer H1 break replaced this leg")) if x[0]]
        stop_t = min((x[0] for x in stops), default=None)
        ab = ctx.path.first_touch("low" if _bull(d) else "high", f.high if _bull(d) else f.low, formed_t)
        if ab is None or (stop_t is not None and ab.t >= stop_t):
            for tt, name, note in sorted(stops):
                if tt <= ctx.now:
                    stage(s, name, tt, note)
                    break
            checklist(ctx, s, ctx.now, (True, "FVG midpoint in the correct half"), (None, "pending"))
            out.append(s)
            continue
        stage(s, "armed", ab.end, "Price entered the H1 FVG")
        side = "low" if _bull(d) else "high"
        lv = [x for x in ctx.liq["m5"] if x.side == side and f.low <= x.price <= f.high]
        start = max(ctx.idx("m5", ab.t), 0)
        hits = [sw for sw in find_sweeps(m5, lv, start) if sw.side == side and sw.bar_t >= ab.t
                and (stop_t is None or sw.t <= stop_t)] if ab.t >= m5[0].t else []
        if not hits:
            for tt, name, note in sorted(stops):
                if tt <= ctx.now:
                    stage(s, name, tt, note)
                    break
            checklist(ctx, s, ctx.now, (True, "FVG midpoint in the correct half"), (None, "pending"))
            out.append(s)
            continue
        sw = min(hits, key=lambda x: x.t)
        buf = ctx.cfg["buffer"]
        s["entry"] = ctx.fmt(m5[sw.k].c)
        if ctx.p.get("s2_stop") == "protected":
            s["stop"] = ctx.fmt(e.prot - buf if _bull(d) else e.prot + buf)
            stop_desc = f"beyond the H1 protected {'low' if _bull(d) else 'high'} {ctx.fmt(e.prot)}"
        else:
            s["stop"] = ctx.fmt(sw.extreme - buf if _bull(d) else sw.extreme + buf)
            stop_desc = f"beyond the sweep extreme {ctx.fmt(sw.extreme)}"
        j = ctx.idx("h1", sw.t)
        tp = st.ext[j] if st.state[j] == d else leg_ext
        if ctx.p.get("tp_mode") != "fixed_2r" and ((tp > s["entry"]) if _bull(d) else (tp < s["entry"])):
            risk = abs(s["entry"] - s["stop"])
            s["tp1"], s["tp1_name"] = ctx.fmt(tp), "H1 range extreme (recent swing)"
            s["rr"] = round(abs(tp - s["entry"]) / risk, 2) if risk else None
            z2, z3 = ctx.zone_target("d1", d, tp), ctx.zone_target("w1", d, tp)
            s["tp2"] = ctx.fmt(z2["low"] if _bull(d) else z2["high"]) if z2 else None
            s["tp3"] = ctx.fmt(z3["low"] if _bull(d) else z3["high"]) if z3 else None
        else:
            set_targets(ctx, s, ["h1"], sw.t)
        s["reasons"].append(f"M5 {sw.kind} sweep of {sw.level.name} {ctx.fmt(sw.level.price)} inside the FVG on the {ny(sw.bar_t)} candle; "
                            f"stop {stop_desc}.")
        stage(s, "triggered", sw.t, "M5 liquidity sweep inside the FVG; entry at candle close", s["entry"])
        checklist(ctx, s, sw.t, (True, "FVG midpoint in the correct half"),
                  (True, f"{sw.level.name} {ctx.fmt(sw.level.price)} swept ({sw.kind})"))
        run_trade(ctx, s, sw.t)
        out.append(s)
    return out


def detect_s3(ctx, since):
    out, p = [], ctx.p
    hb, m5, mst = ctx.bars["h1"], ctx.bars["m5"], ctx.st["m5"]
    start = max(ctx.idx("h1", since), 0)
    levels = ctx.liq["h1"] + ctx.period
    groups = {}
    for sw in find_sweeps(hb, levels, start):
        groups.setdefault((sw.k, sw.side), []).append(sw)
    for (k, side), sws in sorted(groups.items()):
        d = "bearish" if side == "high" else "bullish"
        sw = max(sws, key=lambda x: x.level.price) if side == "high" else min(sws, key=lambda x: x.level.price)
        extreme = max(x.extreme for x in sws) if side == "high" else min(x.extreme for x in sws)
        names = ", ".join(sorted({f"{x.level.name} {ctx.fmt(x.level.price)}" for x in sws}))
        s = new_setup(ctx, "S3", d, "h1", "m5", sw.t, {
            "kind": "H1 liquidity sweep", "tf": "h1",
            "low": ctx.fmt(min(sw.level.price, extreme)), "high": ctx.fmt(max(sw.level.price, extreme)),
            "formed": sw.t.isoformat(), "candle": sw.bar_t.isoformat()})
        s["reasons"] = [
            f"H1 {sw.kind} sweep of {'buy' if side == 'high' else 'sell'}-side liquidity ({names}) on the {ny(sw.bar_t)} candle; "
            f"extreme {ctx.fmt(extreme)}, closed back inside.",
            f"Looking for an M5 BOS/CHoCH {d} with an FVG in the breaking leg within {p['s3_window_h1_bars']}h.",
        ]
        stage(s, "armed", sw.t, f"H1 swept {names} and closed back inside")
        if sw.bar_t < m5[0].t:
            checklist(ctx, s, ctx.now, (None, "pending"), (True, f"{names} swept"))
            out.append(s)
            continue
        window_end = sw.t + timedelta(hours=p["s3_window_h1_bars"])
        found = None
        for e in mst.events:
            if e.dir != d or e.kind not in ("BOS", "CHoCH") or m5[e.j].t < sw.t:
                continue
            if m5[e.j].end > window_end:
                break
            fv = [f for f in ctx.fvg["m5"] if f.dir == d and e.origin_i <= f.i <= e.j and m5[f.i].t >= sw.bar_t]
            if fv:
                found = (e, max(fv, key=lambda x: x.i))
                break
        if found is None:
            if window_end <= ctx.now:
                stage(s, "expired", window_end, "No M5 BOS + FVG in time")
            checklist(ctx, s, ctx.now, (None, "pending"), (True, f"{names} swept"))
            out.append(s)
            continue
        e, f = found
        ready_t = m5[max(f.k, e.j)].end
        buf = ctx.cfg["buffer"]
        entry = f.near
        stop = extreme - buf if _bull(d) else extreme + buf
        if (entry <= stop) if _bull(d) else (entry >= stop):
            continue
        s["entry"], s["stop"] = ctx.fmt(entry), ctx.fmt(stop)
        set_targets(ctx, s, ["h1"], ready_t)
        rng = ctx.range_at("h1", ready_t)
        poi_ok = (None, "no H1 dealing range") if not rng else (
            (entry < rng["mid"]) if _bull(d) else (entry > rng["mid"]),
            f"entry {ctx.fmt(entry)} vs H1 50% {ctx.fmt(rng['mid'])}")
        s["reasons"].append(f"M5 {e.kind} {d} on the {ny(m5[e.j].t)} candle (close {ctx.fmt(e.close)} beyond {ctx.fmt(e.level)}); "
                            f"M5 FVG {ctx.fmt(f.low)}–{ctx.fmt(f.high)}: limit at its near edge {s['entry']}.")
        stage(s, "limit", ready_t, f"Limit {'buy' if _bull(d) else 'sell'} at {s['entry']} (M5 FVG near edge)", s["entry"])
        checklist(ctx, s, ready_t, poi_ok, (True, f"{names} swept ({sw.kind})"))
        expire = ready_t + timedelta(minutes=5 * p["s3_fill_m5_bars"])
        run_limit(ctx, s, ready_t, expire)
        out.append(s)
    return out


def detect_s4(ctx, since):
    out, p = [], ctx.p
    hb = ctx.bars["h1"]
    eqs = [lv for lv in ctx.liq["h1"] if "EQ" in lv.name]
    for f in ctx.fvg["h1"]:
        fk_end = hb[f.k].end
        if fk_end < since:
            continue
        d = f.dir
        side = "high" if d == "bearish" else "low"
        best = None
        for eq in eqs:
            if eq.side != side:
                continue
            t0 = max(eq.known_t, fk_end)
            if t0 > ctx.now or not eq.intact_at(t0):
                continue
            gap = (f.low - eq.price) if d == "bearish" else (eq.price - f.high)
            if not (0 < gap <= p["s4_max_gap_atr"] * ctx.atr_at("h1", t0)):
                continue
            if f.status(ctx.idx("h1", t0)) != "unfilled" or ctx.state_at("h4", t0) != d:
                continue
            if best is None or gap < best[1]:
                best = (eq, gap, t0)
        if best is None:
            continue
        eq, gap, t0 = best
        buf = ctx.cfg["buffer"]
        s = new_setup(ctx, "S4", d, "h1", "h1", t0, {
            "kind": "H1 FVG beyond equal " + ("highs" if side == "high" else "lows"), "tf": "h1",
            "low": ctx.fmt(f.low), "high": ctx.fmt(f.high), "formed": t0.isoformat(), "candle": hb[f.i].t.isoformat(),
            "eq_level": ctx.fmt(eq.price)})
        s["entry"] = ctx.fmt(f.near)
        s["stop"] = ctx.fmt(f.far + buf if d == "bearish" else f.far - buf)
        set_targets(ctx, s, ["h1"], t0)
        s["reasons"] = [
            f"H1 equal {'highs' if side == 'high' else 'lows'} at {ctx.fmt(eq.price)} hold liquidity; "
            f"an unfilled {d} H1 FVG {ctx.fmt(f.low)}–{ctx.fmt(f.high)} sits just beyond them ({gap / ctx.cfg['pip']:.1f} {ctx.cfg['unit']}).",
            f"H4 bias {d}. A run on the equal {'highs' if side == 'high' else 'lows'} should deliver price into the FVG: "
            f"limit {'sell' if d == 'bearish' else 'buy'} at {s['entry']}, stop beyond the gap.",
        ]
        rng = ctx.range_at("h1", t0)
        poi_ok = (None, "no H1 dealing range") if not rng else (
            (f.low > rng["mid"]) if d == "bearish" else (f.high < rng["mid"]), f"FVG vs H1 50% {ctx.fmt(rng['mid'])}")
        stage(s, "watch", t0, f"Pending {'sell' if d == 'bearish' else 'buy'} limit at {s['entry']}", s["entry"])
        checklist(ctx, s, t0, poi_ok, (True, f"equal {'highs' if side == 'high' else 'lows'} {ctx.fmt(eq.price)} taken before the fill"))
        inv = hb[f.closed_through].end if f.closed_through is not None else None
        run_limit(ctx, s, t0, t0 + timedelta(hours=p["s4_expiry_hours"]), inv)
        out.append(s)
    return out


def detect_all(ctx):
    since = ctx.now - timedelta(days=ctx.p["setup_lookback_days"])
    enabled = set(ctx.p.get("enabled_setups", ["S1", "S2", "S3", "S4"]))
    out = []
    for name, fn in (("S1", detect_s1), ("S2", detect_s2), ("S3", detect_s3), ("S4", detect_s4)):
        if name in enabled:
            out.extend(fn(ctx, since))
    return out
