"""Today's session key levels and daily-bias context (New York clock, DST-aware).

Key levels for the current trading day D (the day rolls at 17:00 NY, Friday evening rolls to Monday):
  PWH/PWL, PDH/PDL (last completed W1/D1 candle), Asia high/low (20:00–00:00 NY), London high/low (02:00–05:00 NY),
  NY AM high/low (09:30–11:30 NY), midnight open (00:00 NY) and NY open (09:30 NY).
Each high/low says whether it is still untouched or when price first traded through it after its window closed.

Bias is reported as context only. Study 2026-10-22 on the saved NAS100/US30 year (bias_study.py): no input — D1/4H/1H/W1
structure, premium/discount, previous-day candle, London direction, pre-NY sweeps — called the 09:30→16:00 direction
better than 40–57%, and results flipped between halves of the year. "Nearest untouched level is hit first" was right
88–95% of the time, which is what a random walk gives (87–89%), so the odds shown for the draw are the random-walk odds.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
WINDOWS = (  # name, start (day offset, hh, mm), end (day offset, hh, mm), label
    ("Asia", (-1, 20, 0), (0, 0, 0), "Asia 20:00–00:00"),
    ("London", (0, 2, 0), (0, 5, 0), "London 02:00–05:00"),
    ("NY AM", (0, 9, 30), (0, 11, 30), "New York AM 09:30–11:30"),
)
BIAS_EVIDENCE = ("Measured on the last year of NAS100/US30: none of these inputs called the 09:30–16:00 direction better than "
                 "40–57% (a coin flip), so treat the lean as context, not a forecast.")


def trading_day(now):
    """New York trading date of `now`: the session that opens 18:00 NY the evening before and closes 17:00 NY."""
    d = (now.astimezone(NY) + timedelta(hours=7)).date()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def ny_time(d, offset, hh, mm):
    return datetime.combine(d + timedelta(days=offset), time(hh, mm), NY).astimezone(timezone.utc)


def _range(bars, t0, t1):
    seg = [b for b in bars if t0 <= b.t < t1]
    if not seg:
        return None
    hi = max(seg, key=lambda b: b.h)
    lo = min(seg, key=lambda b: b.l)
    return {"high": hi.h, "high_t": hi.t, "low": lo.l, "low_t": lo.t, "open": seg[0].o, "close": seg[-1].c, "end": seg[-1].end}


def _taken(bars, side, price, after_t, now):
    for b in bars:
        if b.t < after_t or b.end > now:
            continue
        if (side == "high" and b.h > price) or (side == "low" and b.l < price):
            return b.t
    return None


def _open_at(bars, t):
    b = next((b for b in bars if b.t >= t), None)
    return b.o if b is not None and b.t - t < timedelta(minutes=30) else None


def key_levels(m5, d1, w1, now):
    """Session key levels for the trading day containing `now` (bars = engine Bar lists, UTC)."""
    d = trading_day(now)
    levels, opens = [], {}

    def add(name, side, price, formed_t, window, status_after):
        tk = _taken(m5, side, price, status_after, now)
        levels.append({"name": name, "side": side, "price": price, "window": window, "formed_t": formed_t,
                       "taken_t": tk, "untouched": tk is None})

    for bars, tag, label in ((w1, "PW", "previous week"), (d1, "PD", "previous day")):
        done = [b for b in bars if b.end <= now]
        if done:
            b = done[-1]
            add(f"{tag}H", "high", b.h, b.t, label, b.end)
            add(f"{tag}L", "low", b.l, b.t, label, b.end)
    for name, (o0, h0, m0), (o1, h1, m1), label in WINDOWS:
        t0, t1 = ny_time(d, o0, h0, m0), ny_time(d, o1, h1, m1)
        if now <= t0:
            continue
        r = _range(m5, t0, min(t1, now))
        if r is None:
            continue
        forming = now < t1
        win = label + (" (forming)" if forming else "")
        add(f"{name} H", "high", r["high"], r["high_t"], win, t1 if not forming else now)
        add(f"{name} L", "low", r["low"], r["low_t"], win, t1 if not forming else now)
        opens[name] = r
    for key, (hh, mm) in (("midnight", (0, 0)), ("ny_open", (9, 30))):
        t = ny_time(d, 0, hh, mm)
        opens[key] = _open_at(m5, t) if now > t else None
    return {"day": d.isoformat(), "levels": levels, "opens": opens}


def daily_context(ctx, kl):
    """Bias inputs with their current reading, the overall lean, and the nearest untouched liquidity on each side."""
    price = ctx.price
    comps = []

    def comp(name, reading, note):
        comps.append({"name": name, "reading": reading, "note": note})

    for tf, label in (("d1", "Daily structure"), ("h4", "4H structure"), ("h1", "1H structure")):
        st = ctx.st[tf].state[len(ctx.bars[tf]) - 1]
        prot = ctx.st[tf].prot[len(ctx.bars[tf]) - 1]
        comp(label, st, f"protected level {ctx.fmt(prot[0])}" if prot else "")
    mo = kl["opens"].get("midnight")
    if mo is not None:
        comp("Price vs midnight open", "bullish" if price > mo else "bearish" if price < mo else "neutral",
             f"midnight open {ctx.fmt(mo)}; price {ctx.fmt(price)}")
    lon = kl["opens"].get("London")
    if lon and ctx.now >= ny_time(trading_day(ctx.now), 0, 5, 0):
        comp("London session direction", "bullish" if lon["close"] > lon["open"] else "bearish",
             f"{ctx.fmt(lon['open'])} → {ctx.fmt(lon['close'])}")
    asia = {lv["name"]: lv for lv in kl["levels"] if lv["name"].startswith("Asia")}
    if len(asia) == 2:
        h, lo = not asia["Asia H"]["untouched"], not asia["Asia L"]["untouched"]
        reading = "bullish" if lo and not h else "bearish" if h and not lo else "neutral"
        comp("Asia range raid (Judas swing)", reading,
             "Asia low taken, high intact" if reading == "bullish" else "Asia high taken, low intact" if reading == "bearish"
             else "both sides taken" if h and lo else "neither side taken yet")
    bulls = sum(c["reading"] == "bullish" for c in comps)
    bears = sum(c["reading"] == "bearish" for c in comps)
    lean = "bullish" if bulls - bears >= 2 else "bearish" if bears - bulls >= 2 else "mixed"

    untouched = [lv for lv in kl["levels"] if lv["untouched"]]
    above = sorted((lv for lv in untouched if lv["price"] > price), key=lambda lv: lv["price"])
    below = sorted((lv for lv in untouched if lv["price"] < price), key=lambda lv: -lv["price"])
    draw = None
    if above and below:
        da, db = above[0]["price"] - price, price - below[0]["price"]
        draw = {"above": above[0]["name"], "above_price": ctx.fmt(above[0]["price"]), "above_dist": ctx.fmt(da),
                "below": below[0]["name"], "below_price": ctx.fmt(below[0]["price"]), "below_dist": ctx.fmt(db),
                "p_above_first": round(db / (da + db) * 100)}
    return {"lean": lean, "bulls": bulls, "bears": bears, "components": comps, "draw": draw, "evidence": BIAS_EVIDENCE}


def session_summary(ctx):
    kl = key_levels(ctx.bars["m5"], ctx.bars["d1"], ctx.bars["w1"], ctx.now)
    out_levels = []
    for lv in sorted(kl["levels"], key=lambda x: -x["price"]):
        out_levels.append({"name": lv["name"], "side": lv["side"], "price": ctx.fmt(lv["price"]), "window": lv["window"],
                           "untouched": lv["untouched"], "taken_t": lv["taken_t"].isoformat() if lv["taken_t"] else None,
                           "formed_t": lv["formed_t"].isoformat(),
                           "distance": ctx.fmt(lv["price"] - ctx.price)})
    opens = {k: ctx.fmt(v) for k, v in kl["opens"].items() if not isinstance(v, dict)}
    return {"day": kl["day"], "levels": out_levels, "opens": opens, "context": daily_context(ctx, kl)}
