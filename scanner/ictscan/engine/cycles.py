"""Time cycles and previous-cycle highs/lows (PXH/PXL) — after Frank369's "Read Orderflow By Using Time Cycles".

Cycles (New York clock, DST-aware; the trading day rolls at 17:00 NY):
  90m   — 07:00–08:30, 08:30–10:00, 10:00–11:30 (AM) and 11:30–13:00, 13:00–14:30, 14:30–16:00 (PM), built from M5
  day   — trading day (18:00 → 17:00 NY), from H1          week — Sunday 18:00 → Friday 17:00, from H4
  month — calendar month of the trading day, from D1
For each previous cycle: PXH/PXL, EQ (50 %), and the full range of the candle that made the high/low (the "zone" that is
extended to the right). Read of the current cycle against the previous one:
  broke above PXH and still above the PXH candle's low → "holding above PXH" (bullish continuation)
  broke above PXH but closed back below that candle's low → "PXH failed" (bearish warning — first sign only)
  mirror for PXL; neither taken → inside the previous range, premium/discount by EQ.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .session import trading_day

NY = ZoneInfo("America/New_York")
KIND_TF = {"90m": "m5", "day": "h1", "week": "h4", "month": "d1"}
KINDS = ("month", "week", "day", "90m")
WINDOWS_90 = ((7, 0, 8, 30), (8, 30, 10, 0), (10, 0, 11, 30), (11, 30, 13, 0), (13, 0, 14, 30), (14, 30, 16, 0))
LABEL = {"90m": "90-min cycle", "day": "Day", "week": "Week", "month": "Month"}


def _at(d, hh, mm):
    return datetime.combine(d, time(hh, mm), NY).astimezone(timezone.utc)


def cycle_of(kind, t):
    """(key, start, end) of the cycle containing bar-start time t, or None (90m outside the six windows)."""
    td = trading_day(t)
    if kind == "90m":
        n = t.astimezone(NY)
        if n.date() != td:
            return None
        m = n.hour * 60 + n.minute
        for i, (h0, m0, h1, m1) in enumerate(WINDOWS_90):
            if h0 * 60 + m0 <= m < h1 * 60 + m1:
                return (td, i), _at(td, h0, m0), _at(td, h1, m1)
        return None
    if kind == "day":
        prev = td - timedelta(days=3 if td.weekday() == 0 else 1)
        return td, _at(prev, 18, 0), _at(td, 17, 0)
    if kind == "week":
        mon = td - timedelta(days=td.weekday())
        return mon, _at(mon - timedelta(days=1), 18, 0), _at(mon + timedelta(days=4), 17, 0)
    first = td.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    last = nxt - timedelta(days=1)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    start_day = first - timedelta(days=1)
    return (td.year, td.month), _at(start_day, 18, 0), _at(last, 17, 0)


def build_cycles(bars, kind, now=None):
    """Cycles in time order, each with high/low, EQ and the candle range that made the high/low."""
    out, cur = [], None
    for b in bars:
        if now is not None and b.end > now:
            break
        c = cycle_of(kind, b.t)
        if c is None:
            continue
        key, start, end = c
        if cur is None or cur["key"] != key:
            cur = {"key": key, "start": start, "end": end, "first_t": b.t, "last_end": b.end, "open": b.o, "close": b.c,
                   "high": b.h, "low": b.l, "hb": b, "lb": b}
            out.append(cur)
        cur["last_end"], cur["close"] = b.end, b.c
        if b.h > cur["high"]:
            cur["high"], cur["hb"] = b.h, b
        if b.l < cur["low"]:
            cur["low"], cur["lb"] = b.l, b
    for c in out:
        c["eq"] = (c["high"] + c["low"]) / 2
        c["hi_zone"] = (c["hb"].l, c["hb"].h)
        c["lo_zone"] = (c["lb"].l, c["lb"].h)
        c["high_t"], c["low_t"] = c["hb"].t, c["lb"].t
    return out


def read(prev, bars_after, price):
    """How the cycle after `prev` (bars_after, closed bars only) treats prev's PXH/PXL. Returns a dict for the UI."""
    took_h = next((b.t for b in bars_after if b.h > prev["high"]), None)
    took_l = next((b.t for b in bars_after if b.l < prev["low"]), None)
    last = bars_after[-1].c if bars_after else price
    side = None
    if took_h and took_l:
        side = "high" if took_h > took_l else "low"
    elif took_h:
        side = "high"
    elif took_l:
        side = "low"
    if side == "high":
        if last >= prev["hi_zone"][0]:
            state, flow, note = "holding_above_pxh", "bullish", "Broke above PXH; price stays at or above the PXH candle — continuation"
        else:
            state, flow, note = "pxh_failed", "bearish", "Swept PXH but closed back below its candle — first bearish warning"
    elif side == "low":
        if last <= prev["lo_zone"][1]:
            state, flow, note = "holding_below_pxl", "bearish", "Broke below PXL; price stays at or below the PXL candle — continuation"
        else:
            state, flow, note = "pxl_failed", "bullish", "Swept PXL but closed back above its candle — first bullish warning"
    else:
        pd = "premium" if price > prev["eq"] else "discount"
        state, flow, note = "inside", "range", f"Inside the previous range, in {pd} (vs EQ)"
    span = prev["high"] - prev["low"]
    return {"state": state, "flow": flow, "note": note, "took_high": took_h.isoformat() if took_h else None,
            "took_low": took_l.isoformat() if took_l else None,
            "pos": round((price - prev["low"]) / span * 100) if span > 0 else 50}


def _pub(c, fmt):
    return {"start": c["start"].isoformat(), "end": c["end"].isoformat(), "high": fmt(c["high"]), "low": fmt(c["low"]),
            "eq": fmt(c["eq"]), "hi_zone": [fmt(v) for v in c["hi_zone"]], "lo_zone": [fmt(v) for v in c["lo_zone"]],
            "high_t": c["high_t"].isoformat(), "low_t": c["low_t"].isoformat()}


def cycle_summary(bars_by_tf, now, price, fmt):
    """Per kind: the current cycle (so far), the previous two cycles, and the read of the current vs the previous."""
    out = {}
    for kind in KINDS:
        tf_bars = bars_by_tf.get(KIND_TF[kind]) or []
        cyc = build_cycles(tf_bars, kind, now)
        if len(cyc) < 2:
            continue
        cur_c = cycle_of(kind, now)
        live = cur_c is not None and cyc[-1]["key"] == cur_c[0]
        cur = cyc[-1] if live else None
        prev = cyc[-2] if live else cyc[-1]
        prev2 = (cyc[-3] if live else cyc[-2]) if len(cyc) >= (3 if live else 2) else None
        after = [b for b in tf_bars if cur and cur["start"] <= b.t and b.end <= now]
        out[kind] = {"label": LABEL[kind], "tf": KIND_TF[kind], "live": live,
                     "current": ({"start": cur["start"].isoformat(), "end": cur["end"].isoformat(), "high": fmt(cur["high"]),
                                  "low": fmt(cur["low"])} if cur else None),
                     "prev": _pub(prev, fmt), "prev2": _pub(prev2, fmt) if prev2 else None,
                     "read": read(prev, after, price) if cur else {"state": "between", "flow": "range",
                                                                    "note": "Between cycles — waiting for the next window",
                                                                    "pos": None, "took_high": None, "took_low": None}}
        if kind == "90m" and cur_c is None:
            out[kind]["next_window"] = _next_window(now)
    return out


def _next_window(now):
    td = trading_day(now)
    for d in (td, td + timedelta(days=1)):
        for h0, m0, h1, m1 in WINDOWS_90:
            s = _at(d, h0, m0)
            if s > now:
                return s.isoformat()
    return None


def chart_cycles(bars, kind, now, n=8, fmt=lambda v: v):
    """The last n cycles for the dashboard chart: each with its own range and the previous cycle's levels."""
    cyc = build_cycles(bars, kind, now)[-(n + 1):]
    res = []
    for i, c in enumerate(cyc):
        item = {"start": c["start"].isoformat(), "end": c["end"].isoformat(), "first_t": c["first_t"].isoformat(),
                "last_end": c["last_end"].isoformat(), "high": fmt(c["high"]), "low": fmt(c["low"]), "eq": fmt(c["eq"])}
        if i > 0:
            item["px"] = _pub(cyc[i - 1], fmt)
        res.append(item)
    return res[1:] if len(res) > 1 else res
