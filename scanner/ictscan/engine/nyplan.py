"""New York session plan per index: lean, liquidity map, 90-minute window timeline and if/then scenarios.

Built from what the engine already measures (no new signals): weekly/daily cycle reads (PXH/PXL held or failed), D1/4H
structure, today's untouched key levels and the 90-minute windows. Scenarios are conditional ("if … then look for the
MMXM model …"); the backtested bias study found no input that predicts the NY direction by itself, so the lean only
orders the scenarios, it is not a forecast.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .cycles import WINDOWS_90
from .session import trading_day

NY = ZoneInfo("America/New_York")
WINDOW_NOTES = (
    "Pre-open: often builds the range New York will raid",
    "08:30 data + 09:30 open: main manipulation (Judas) window",
    "Watch the 10:30 turn: the morning trend usually shows here",
    "Lunch: often slow; levels frequently hold",
    "PM manipulation: raid of the morning high/low",
    "PM distribution into the 16:00 close",
)


def _at(d, hh, mm):
    return datetime.combine(d, time(hh, mm), NY).astimezone(timezone.utc)


def _flow_vote(read):
    return {"bullish": 1, "bearish": -1}.get((read or {}).get("flow"), 0)


def ny_plan(ctx, sess, cyc):
    fmt, price, now = ctx.fmt, ctx.price, ctx.now
    d = trading_day(now)
    if now >= _at(d, 16, 0):  # after the close: plan the next trading day (Friday evening → Monday)
        d = trading_day(_at(d, 18, 0))
    m5 = ctx.bars["m5"]
    windows = []
    for i, (h0, m0, h1, m1) in enumerate(WINDOWS_90):
        s, e = _at(d, h0, m0), _at(d, h1, m1)
        seg = [b for b in m5 if s <= b.t < e and b.end <= now]
        windows.append({"label": f"{h0:02d}:{m0:02d}", "start": s.isoformat(), "end": e.isoformat(),
                        "status": "done" if now >= e else "now" if now >= s else "next",
                        "high": fmt(max(b.h for b in seg)) if seg else None, "low": fmt(min(b.l for b in seg)) if seg else None,
                        "note": WINDOW_NOTES[i]})
    session = "before" if now < _at(d, 7, 0) else "after" if now >= _at(d, 16, 0) else "live"

    # Lean: weekly + daily cycle reads and D1/H4 structure (each one vote; needs a 2-vote margin)
    votes = []
    for k in ("week", "day"):
        c = (cyc or {}).get(k) or {}
        v = _flow_vote(c.get("read"))
        if v:
            votes.append((f"{c.get('label', k)} cycle: {c['read']['note'].split(' — ')[0]}", v))
    for tf, name in (("d1", "Daily structure"), ("h4", "4H structure")):
        st = ctx.st[tf].state[len(ctx.bars[tf]) - 1]
        v = {"bullish": 1, "bearish": -1}.get(st, 0)
        if v:
            votes.append((f"{name} {st}", v))
    score = sum(v for _, v in votes)
    lean = "bullish" if score >= 2 else "bearish" if score <= -2 else "two-sided"

    # Liquidity map: untouched session levels + previous day/week cycle extremes, nearest first; same price → one entry
    levels = {}

    def put(name, px):
        if px in levels:
            if name not in levels[px]["name"].split(" / "):
                levels[px]["name"] += f" / {name}"
        else:
            levels[px] = {"name": name, "price": px}
    for lv in (sess or {}).get("levels") or []:
        if lv.get("untouched"):
            put(lv["name"], lv["price"])
    for k, tag in (("day", "Day"), ("week", "Week")):
        p = ((cyc or {}).get(k) or {}).get("prev")
        if p:
            put(f"{tag} PXH", p["high"])
            put(f"{tag} PXL", p["low"])
    above = sorted((x for x in levels.values() if x["price"] > price), key=lambda x: x["price"])[:4]
    below = sorted((x for x in levels.values() if x["price"] < price), key=lambda x: -x["price"])[:4]
    for x in above + below:
        x["dist"] = fmt(abs(x["price"] - price))

    def names(xs, n=2):
        return " → ".join(f"{x['name']} {x['price']}" for x in xs[:n]) or "no untouched level nearby"

    a1 = above[0] if above else None
    b1 = below[0] if below else None
    sell = {"side": "sell", "title": "Sell the buy-side raid",
            "if": f"a 90-min window trades above {a1['name']} {a1['price']} and closes back below it (the sweep fails)" if a1
            else "price rallies into a premium array and fails",
            "then": f"wait for the MMXM sell model (5m shift + FVG); targets {names(below)}",
            "invalid": f"15m close holding above {above[1]['name']} {above[1]['price']}" if len(above) > 1 else "a 15m close above the raid high"}
    buy = {"side": "buy", "title": "Buy the sell-side raid",
           "if": f"a 90-min window trades below {b1['name']} {b1['price']} and closes back above it (the sweep fails)" if b1
           else "price drops into a discount array and holds",
           "then": f"wait for the MMXM buy model (5m shift + FVG); targets {names(above)}",
           "invalid": f"15m close holding below {below[1]['name']} {below[1]['price']}" if len(below) > 1 else "a 15m close below the raid low"}
    scenarios = [sell, buy] if lean == "bearish" else [buy, sell] if lean == "bullish" else [sell, buy] if (
        a1 and b1 and a1["dist"] <= b1["dist"]) else [buy, sell]
    scenarios[0]["rank"], scenarios[1]["rank"] = "primary", "alternative"
    if lean == "two-sided":
        scenarios[0]["rank"] = scenarios[1]["rank"] = "either"

    nxt = next((w for w in windows if w["status"] in ("now", "next")), None)
    return {"day": d.isoformat(), "session": session, "lean": lean, "score": score,
            "votes": [{"why": w, "vote": v} for w, v in votes], "above": above, "below": below,
            "windows": windows, "focus": nxt["label"] if nxt else None, "scenarios": scenarios,
            "evidence": "Lean orders the scenarios only — the bias study found no input that predicts the NY direction alone."}
