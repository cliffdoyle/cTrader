"""SMT divergence across the correlated US indices (NAS100, US30, US500) — the "reversal is coming" warning.

Two views, both from closed candles only:
  cycle SMT — for the 90-minute, day and week cycles: did every index take the previous cycle's high (PXH) / low (PXL)?
              Some took it and some did not = divergence. Highs → bearish SMT, lows → bullish SMT.
  swing SMT — on M5/M15/H1: for each pair of consecutive swing highs (lows) of any index, compare every index's high
              (low) around the same two times. One makes a higher high while another makes a lower high = bearish SMT
              (mirror: lower low vs higher low = bullish SMT). Status afterwards:
                watch     — divergence formed, no shift yet
                confirmed — an index closed through the swing low (high) between the two points (MSS) → reversal under way
                cancelled — every index went on to a new high (low): the divergence was absorbed
SMT is a warning, not an entry: the MMXM rules still need the 5m shift + FVG. Trade the weaker index on a bearish SMT
(the one with the lower high) and the stronger one on a bullish SMT (the one with the higher low).
"""
from __future__ import annotations

import bisect
from datetime import timedelta

from .core import TF_SECONDS, atr_series, find_swings
from .cycles import KIND_TF, LABEL, NY, build_cycles, cycle_of

INDICES = ("NAS100", "US30", "US500")
SWING_TFS = {"m5": 6, "m15": 24, "h1": 72}    # look-back in hours for the later swing point
NEAR_BARS = 2                                  # time alignment: the other indices' extreme within ±2 bars
EQUAL_ATR = 0.1                                # closer than 0.1 × ATR = equal high/low (neither side of the divergence)
KEEP_PER_SIDE = 2


def _closed(bars, now):
    return [b for b in bars if b.end <= now]


def _ny(t):
    return t.astimezone(NY).strftime("%a %H:%M")


def _join(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def cycle_smt(bars_by_sym, now, fmt=lambda v: v, kinds=("90m", "day", "week")):
    """Per cycle kind: which indices took the previous cycle's PXH / PXL in the current (or latest) cycle."""
    syms = [s for s in INDICES if s in bars_by_sym]
    out = []
    for kind in kinds:
        tf = KIND_TF[kind]
        per, keys = {}, set()
        for s in syms:
            cyc = build_cycles(bars_by_sym[s].get(tf) or [], kind, now)
            if len(cyc) < 2:
                break
            cur, prev = cyc[-1], cyc[-2]
            keys.add(cur["key"])
            took_h = cur["high"] > prev["high"]
            took_l = cur["low"] < prev["low"]
            per[s] = {"pxh": fmt(prev["high"]), "pxl": fmt(prev["low"]), "high": fmt(cur["high"]), "low": fmt(cur["low"]),
                      "took_high": took_h, "took_low": took_l,
                      "high_t": cur["high_t"].isoformat(), "low_t": cur["low_t"].isoformat()}
        if len(per) != len(syms) or len(keys) != 1 or len(syms) < 2:
            continue
        cur_key = cycle_of(kind, now)
        live = cur_key is not None and cur_key[0] in keys
        any_cur = next(iter(per))
        cyc0 = build_cycles(bars_by_sym[any_cur][tf], kind, now)[-1]
        item = {"kind": kind, "label": LABEL[kind], "live": live,
                "window": f"{cyc0['start'].astimezone(NY):%a %H:%M}–{cyc0['end'].astimezone(NY):%H:%M} NY",
                "start": cyc0["start"].isoformat(), "end": cyc0["end"].isoformat(), "per": per, "smt": []}
        for side, flag, word in (("high", "took_high", "PXH"), ("low", "took_low", "PXL")):
            took = [s for s in syms if per[s][flag]]
            held = [s for s in syms if not per[s][flag]]
            if took and held:
                bear = side == "high"
                item["smt"].append({
                    "side": "bearish" if bear else "bullish", "leaders": took, "laggards": held,
                    "text": f"{_join(took)} took {word}; {_join(held)} did not",
                    "trade": (f"Bearish SMT — look to sell the weaker {_join(held)}" if bear
                              else f"Bullish SMT — look to buy the stronger {_join(held)}")})
        item["state"] = ("smt" if item["smt"] else "all took PXH" if all(per[s]["took_high"] for s in syms)
                         else "all took PXL" if all(per[s]["took_low"] for s in syms) else "inside")
        out.append(item)
    return out


class _Series:
    def __init__(self, bars):
        self.b = bars
        self.starts = [x.t for x in bars]
        self.atr = atr_series(bars) if len(bars) > 1 else [0.0] * len(bars)

    def atr_at(self, t):
        i = min(max(bisect.bisect_left(self.starts, t), 0), len(self.b) - 1)
        return self.atr[i] or 0.0

    def near(self, t, side, k):
        """Extreme (price, time) of the bars within ±k bars of time t."""
        i = bisect.bisect_left(self.starts, t)
        if i >= len(self.b):
            return None
        seg = self.b[max(0, i - k): i + k + 1]
        if not seg:
            return None
        x = max(seg, key=lambda b: b.h) if side == "high" else min(seg, key=lambda b: b.l)
        return (x.h if side == "high" else x.l), x.t

    def between(self, t0, t1, side):
        """Opposite extreme between two times (the swing low between two highs, or high between two lows)."""
        seg = self.b[bisect.bisect_right(self.starts, t0): bisect.bisect_left(self.starts, t1)]
        if not seg:
            return None
        return min(b.l for b in seg) if side == "high" else max(b.h for b in seg)

    def after(self, t):
        return self.b[bisect.bisect_right(self.starts, t):]


def _status(series, per, side, t_last):
    """watch / confirmed (MSS: a close through the swing between the two points) / cancelled (all made a new extreme)."""
    first_conf, cancel_t = None, []
    for s, p in per.items():
        later = series[s].after(t_last)
        ext = max(p["p1"], p["p2"]) if side == "high" else min(p["p1"], p["p2"])
        mss = next((b.end for b in later if (b.c < p["mid"] if side == "high" else b.c > p["mid"])), None)
        if mss and (first_conf is None or mss < first_conf):
            first_conf = mss
        beyond = next((b.end for b in later if (b.h > ext if side == "high" else b.l < ext)), None)
        cancel_t.append(beyond)
    cancelled = all(cancel_t) and max(cancel_t)
    if first_conf and (not cancelled or first_conf < cancelled):
        return "confirmed", first_conf
    if cancelled:
        return "cancelled", cancelled
    return "watch", None


def swing_smt(bars_by_sym, tf, now, lookback_h, fmt=lambda v: v, n=2, k=NEAR_BARS, keep=KEEP_PER_SIDE):
    syms = [s for s in INDICES if s in bars_by_sym]
    series = {s: _Series(_closed(bars_by_sym[s].get(tf) or [], now)) for s in syms}
    if len(syms) < 2 or any(len(series[s].b) < 20 for s in syms):
        return []
    since = now - timedelta(hours=lookback_h)
    span = timedelta(seconds=TF_SECONDS[tf] * k)
    found = []
    for leader in syms:
        bars = series[leader].b
        highs, lows = find_swings(bars, n)
        for side, sw in (("high", highs), ("low", lows)):
            for A, B in zip(sw, sw[1:]):
                tA, tB = bars[A.i].t, bars[B.i].t
                if tB < since or B.i - A.i < 2 * k + 2:
                    continue
                if any(abs(e["_tB"] - tB) <= span and e["side_of"] == side for e in found):
                    continue
                per = {}
                for s in syms:
                    a, b = series[s].near(tA, side, k), series[s].near(tB, side, k)
                    if not a or not b or a[1] >= b[1]:
                        per = None
                        break
                    mid = series[s].between(a[1], b[1], side)
                    if mid is None:
                        per = None
                        break
                    d = (b[0] - a[0]) * (1 if side == "high" else -1)
                    tol = EQUAL_ATR * series[s].atr_at(b[1])
                    per[s] = {"p1": a[0], "t1": a[1], "p2": b[0], "t2": b[1], "mid": mid,
                              "beyond": True if d > tol else False if d < -tol else None}
                if not per:
                    continue
                took = [s for s in syms if per[s]["beyond"] is True]
                held = [s for s in syms if per[s]["beyond"] is False]
                if not took or not held:
                    continue
                t_last = max(p["t2"] for p in per.values())
                status, st_t = _status(series, per, side, t_last)
                bear = side == "high"
                found.append({
                    "side_of": side, "_tA": tA, "_tB": tB, "tf": tf,
                    "side": "bearish" if bear else "bullish", "t1": tA.isoformat(), "t2": tB.isoformat(),
                    "when": _ny(tB), "status": status, "status_t": st_t.isoformat() if st_t else None,
                    "leaders": took, "laggards": held,
                    "per": {s: {"p1": fmt(p["p1"]), "t1": p["t1"].isoformat(), "p2": fmt(p["p2"]), "t2": p["t2"].isoformat(),
                                "mid": fmt(p["mid"]),
                                "label": ("EQH" if bear else "EQL") if p["beyond"] is None
                                else ("HH" if p["beyond"] else "LH") if bear else ("LL" if p["beyond"] else "HL")}
                            for s, p in per.items()},
                    "text": (f"{_join(took)} made a higher high; {_join(held)} made a lower high" if bear
                             else f"{_join(took)} made a lower low; {_join(held)} made a higher low"),
                    "trade": (f"Look to sell the weaker {_join(held)} after a 5m shift" if bear
                              else f"Look to buy the stronger {_join(held)} after a 5m shift"),
                })
    out = []
    for side in ("bearish", "bullish"):
        evs = sorted((e for e in found if e["side"] == side), key=lambda e: e["_tB"], reverse=True)
        out += evs[:keep]
    for e in out:
        e.pop("_tA"), e.pop("_tB"), e.pop("side_of")
    return sorted(out, key=lambda e: e["t2"], reverse=True)


def smt_summary(bars_by_sym, now, fmt=lambda v: round(v, 1)):
    """Everything the dashboard and the chart drawing need."""
    swings = []
    for tf, hours in SWING_TFS.items():
        swings += swing_smt(bars_by_sym, tf, now, hours, fmt)
    active = [e for e in swings if e["status"] != "cancelled"]
    latest = max(active, key=lambda e: e["t2"]) if active else None
    cyc = cycle_smt(bars_by_sym, now, fmt)
    live_cycle = [(c, s) for c in cyc if c["live"] for s in c["smt"]]
    if live_cycle:
        c, s = live_cycle[0]
        headline = {"side": s["side"], "text": f"{c['label']} SMT: {s['text']}", "trade": s["trade"]}
    elif latest:
        headline = {"side": latest["side"], "text": f"{latest['tf'].upper()} SMT {latest['when']} NY: {latest['text']}",
                    "trade": latest["trade"], "status": latest["status"]}
    else:
        headline = None
    return {"t": now.isoformat(), "symbols": list(INDICES), "cycles": cyc, "swings": swings, "headline": headline}
