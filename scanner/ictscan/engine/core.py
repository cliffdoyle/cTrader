"""Market-structure primitives (strategy-spec.md §1). Pure functions over closed candles; no look-ahead."""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

TF_SECONDS = {"m5": 300, "m15": 900, "h1": 3600, "h4": 14400, "d1": 86400, "w1": 604800}
PATH_ORDER = ("d1", "h4", "h1", "m15", "m5")


@dataclass(slots=True)
class Bar:
    t: datetime
    end: datetime
    o: float
    h: float
    l: float
    c: float


def make_bars(rows, tf):
    d = timedelta(seconds=TF_SECONDS[tf])
    out = []
    for r in rows:
        t = r["t"] if isinstance(r["t"], datetime) else datetime.fromtimestamp(r["t"], timezone.utc)
        out.append(Bar(t, t + d, r["o"], r["h"], r["l"], r["c"]))
    return out


@dataclass(slots=True)
class Swing:
    i: int
    price: float
    side: str
    conf: int
    body: float


def find_swings(bars, n=2):
    highs, lows = [], []
    for i in range(n, len(bars) - n):
        b = bars[i]
        if all(b.h > bars[k].h for k in range(i - n, i + n + 1) if k != i):
            highs.append(Swing(i, b.h, "high", i + n, max(b.o, b.c)))
        if all(b.l < bars[k].l for k in range(i - n, i + n + 1) if k != i):
            lows.append(Swing(i, b.l, "low", i + n, min(b.o, b.c)))
    return highs, lows


def atr_series(bars, length=14):
    n = len(bars)
    out = [None] * n
    if n < 2:
        return [max(b.h - b.l, 1e-12) for b in bars]
    trs, a = [], None
    for i in range(1, n):
        b, pc = bars[i], bars[i - 1].c
        tr = max(b.h - b.l, abs(b.h - pc), abs(b.l - pc))
        if a is None:
            trs.append(tr)
            if len(trs) < length:
                out[i] = sum(trs) / len(trs)
                continue
            a = sum(trs) / length
        else:
            a = (a * (length - 1) + tr) / length
        out[i] = a
    out[0] = out[1]
    return out


@dataclass(slots=True)
class Event:
    j: int
    kind: str          # BOS | CHoCH | initial
    dir: str           # bullish | bearish
    level: float
    ref_i: int         # index of the swing/protected level that was broken
    origin_i: int      # new protected level index (leg origin)
    prot: float        # new protected level price
    close: float


@dataclass(slots=True)
class Structure:
    events: list
    state: list        # state after bar j
    prot: list         # (price, index) after bar j
    ext: list          # leg extreme (dealing-range far end) after bar j


def structure(bars, highs, lows):
    """Close-based BOS/CHoCH with protected levels (spec §1).
    Bullish BOS = close above the latest unbroken confirmed swing high; protected low = lowest low between that
    swing and the break candle. The bias flips (CHoCH) only on a close beyond the protected level."""
    n = len(bars)
    state, prot, ext = "undefined", None, None
    hp = lp = 0
    last_h = last_l = None
    broken_h, broken_l = set(), set()
    events = []
    st, pr, ex = [None] * n, [None] * n, [None] * n
    for j in range(n):
        while hp < len(highs) and highs[hp].conf < j:
            last_h = highs[hp]
            hp += 1
        while lp < len(lows) and lows[lp].conf < j:
            last_l = lows[lp]
            lp += 1
        b = bars[j]
        c = b.c
        up_swing = last_h is not None and last_h.i not in broken_h and c > last_h.price
        dn_swing = last_l is not None and last_l.i not in broken_l and c < last_l.price
        up_prot = state == "bearish" and prot is not None and c > prot[0]
        dn_prot = state == "bullish" and prot is not None and c < prot[0]
        go_up = (state in ("bullish", "undefined") and up_swing) or up_prot
        go_dn = (state in ("bearish", "undefined") and dn_swing) or dn_prot
        if go_up and go_dn:
            go_dn = False
        if go_up:
            if up_swing:
                level, ref = last_h.price, last_h.i
                broken_h.add(last_h.i)
            else:
                level, ref = prot[0], prot[1]
            kind = {"bullish": "BOS", "bearish": "CHoCH"}.get(state, "initial")
            o = min(range(ref, j + 1), key=lambda k: bars[k].l)
            state, prot = "bullish", (bars[o].l, o)
            ext = max(x.h for x in bars[o:j + 1])
            events.append(Event(j, kind, "bullish", level, ref, o, prot[0], c))
        elif go_dn:
            if dn_swing:
                level, ref = last_l.price, last_l.i
                broken_l.add(last_l.i)
            else:
                level, ref = prot[0], prot[1]
            kind = {"bearish": "BOS", "bullish": "CHoCH"}.get(state, "initial")
            o = max(range(ref, j + 1), key=lambda k: bars[k].h)
            state, prot = "bearish", (bars[o].h, o)
            ext = min(x.l for x in bars[o:j + 1])
            events.append(Event(j, kind, "bearish", level, ref, o, prot[0], c))
        elif state == "bullish":
            ext = max(ext, b.h)
        elif state == "bearish":
            ext = min(ext, b.l)
        st[j], pr[j], ex[j] = state, prot, ext
    return Structure(events, st, pr, ex)


def dealing_range(struct, j):
    if j < 0:
        return None
    s, p, e = struct.state[j], struct.prot[j], struct.ext[j]
    if s not in ("bullish", "bearish") or p is None:
        return None
    lo, hi = (p[0], e) if s == "bullish" else (e, p[0])
    return {"low": lo, "high": hi, "mid": (lo + hi) / 2, "state": s}


@dataclass(slots=True)
class FVG:
    i: int             # displacement candle (candle 2)
    k: int             # candle 3
    dir: str
    low: float
    high: float
    ratio: float
    partial: int | None
    filled: int | None
    closed_through: int | None

    @property
    def near(self):
        return self.high if self.dir == "bullish" else self.low

    @property
    def far(self):
        return self.low if self.dir == "bullish" else self.high

    def status(self, j):
        if self.filled is not None and self.filled <= j:
            return "filled"
        if self.partial is not None and self.partial <= j:
            return "partly filled"
        return "unfilled"


def find_fvgs(bars, mult=1.5, lookback=20):
    out = []
    n = len(bars)
    for k in range(lookback + 2, n):
        c1, c2, c3 = bars[k - 2], bars[k - 1], bars[k]
        prior = bars[k - 1 - lookback:k - 1]
        avg = sum(abs(x.c - x.o) for x in prior) / lookback
        body = abs(c2.c - c2.o)
        if avg <= 0 or body < mult * avg:
            continue
        if c1.h < c3.l and c2.c > c2.o:
            d, lo, hi = "bullish", c1.h, c3.l
        elif c1.l > c3.h and c2.c < c2.o:
            d, lo, hi = "bearish", c3.h, c1.l
        else:
            continue
        partial = filled = through = None
        for m in range(k + 1, n):
            x = bars[m]
            if d == "bullish":
                if partial is None and x.l < hi:
                    partial = m
                if filled is None and x.l <= lo:
                    filled = m
                if x.c < lo:
                    through = m
                    break
            else:
                if partial is None and x.h > lo:
                    partial = m
                if filled is None and x.h >= hi:
                    filled = m
                if x.c > hi:
                    through = m
                    break
        out.append(FVG(k - 1, k, d, lo, hi, body / avg, partial, filled, through))
    return out


class Path:
    """Finest-available merged price path across timeframes, for cross-timeframe 'traded through' checks."""

    def __init__(self, bars_by_tf):
        path = []
        for tf in PATH_ORDER:
            rows = bars_by_tf.get(tf) or []
            if not rows:
                continue
            cutoff = rows[0].t
            path = [b for b in path if b.end <= cutoff] + list(rows)
        self.bars = path
        self.starts = [b.t for b in path]

    def first_beyond(self, side, price, after_t):
        """First bar starting at/after after_t trading strictly beyond price."""
        i = bisect.bisect_left(self.starts, after_t)
        for b in self.bars[i:]:
            if (side == "high" and b.h > price) or (side == "low" and b.l < price):
                return b
        return None

    def first_touch(self, side, price, after_t):
        """First bar starting at/after after_t reaching price (side high: high >= price)."""
        i = bisect.bisect_left(self.starts, after_t)
        for b in self.bars[i:]:
            if (side == "high" and b.h >= price) or (side == "low" and b.l <= price):
                return b
        return None

    def bars_from(self, t):
        return self.bars[bisect.bisect_left(self.starts, t):]


@dataclass(slots=True)
class Level:
    name: str
    side: str          # high = buy-side liquidity above, low = sell-side below
    price: float
    known_t: datetime
    tf: str
    broken_t: datetime | None = None
    until: datetime | None = None   # period levels stop being "previous day/week" when the next period closes

    def intact_at(self, t):
        return self.known_t <= t and (self.broken_t is None or self.broken_t >= t) and (self.until is None or t < self.until)


def _broken(path, side, price, after_t):
    hit = path.first_beyond(side, price, after_t)
    return hit.t if hit else None


def swing_levels(bars, highs, lows, tf, path):
    out = []
    for s in highs + lows:
        if s.conf >= len(bars):
            continue
        lv = Level(f"{tf.upper()} swing {s.side}", s.side, s.price, bars[s.conf].end, tf)
        lv.broken_t = _broken(path, s.side, s.price, bars[s.i].end)
        out.append(lv)
    return out


def equal_levels(bars, highs, lows, tf, path, atr, tol_mult=0.1):
    out = []
    for group, side, name in ((highs, "high", "EQH"), (lows, "low", "EQL")):
        for a_idx, s2 in enumerate(group):
            if s2.conf >= len(bars):
                continue
            tol = tol_mult * (atr[s2.conf] or 0)
            for s1 in reversed(group[:a_idx]):
                if abs(s1.price - s2.price) > tol:
                    continue
                b1 = _broken(path, side, s1.price, bars[s1.i].end)
                if b1 is not None and b1 < bars[s2.i].t:
                    continue
                price = max(s1.price, s2.price) if side == "high" else min(s1.price, s2.price)
                lv = Level(f"{tf.upper()} {name}", side, price, bars[s2.conf].end, tf)
                lv.broken_t = _broken(path, side, price, bars[s2.i].end)
                out.append(lv)
                break
    return out


def period_levels(d1, w1, path):
    """PDH/PDL and PWH/PWL: each completed D1/W1 candle becomes a level once it closes."""
    out = []
    for bars, hi, lo, tf in ((d1, "PDH", "PDL", "d1"), (w1, "PWH", "PWL", "w1")):
        for idx, b in enumerate(bars):
            until = bars[idx + 1].end if idx + 1 < len(bars) else None
            for side, name, price in (("high", hi, b.h), ("low", lo, b.l)):
                lv = Level(name, side, price, b.end, tf, until=until)
                lv.broken_t = _broken(path, side, price, b.end)
                out.append(lv)
    return out


def current_period_levels(levels, t):
    """Only the most recent completed day/week counts as PDH/PDL/PWH/PWL at time t."""
    best = {}
    for lv in levels:
        if lv.name in ("PDH", "PDL", "PWH", "PWL") and lv.known_t <= t:
            if lv.name not in best or lv.known_t > best[lv.name].known_t:
                best[lv.name] = lv
    return list(best.values())


@dataclass(slots=True)
class Sweep:
    k: int             # confirming bar index
    t: datetime        # confirmation time (end of confirming bar)
    bar_t: datetime    # start of the first sweeping bar
    side: str          # high = buy-side swept (bearish reversal), low = sell-side swept
    level: Level
    extreme: float
    kind: str          # wick | two-candle


def find_sweeps(bars, levels, start=0):
    """Wick sweep (beyond and close back inside on one candle) or two-candle sweep (close beyond, next closes back)."""
    out = []
    n = len(bars)
    for k in range(start, n):
        b = bars[k]
        for lv in levels:
            if not lv.intact_at(b.t):
                continue
            p = lv.price
            if lv.side == "high":
                if b.h > p and b.c < p:
                    out.append(Sweep(k, b.end, b.t, "high", lv, b.h, "wick"))
                elif b.c > p and k + 1 < n and bars[k + 1].c < p:
                    out.append(Sweep(k + 1, bars[k + 1].end, b.t, "high", lv, max(b.h, bars[k + 1].h), "two-candle"))
            else:
                if b.l < p and b.c > p:
                    out.append(Sweep(k, b.end, b.t, "low", lv, b.l, "wick"))
                elif b.c < p and k + 1 < n and bars[k + 1].c > p:
                    out.append(Sweep(k + 1, bars[k + 1].end, b.t, "low", lv, min(b.l, bars[k + 1].l), "two-candle"))
    return out


def index_at(bars, t):
    """Index of the last bar closed at or before t, or -1."""
    lo, hi = 0, len(bars)
    while lo < hi:
        mid = (lo + hi) // 2
        if bars[mid].end <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo - 1


@dataclass(slots=True)
class OrderBlock:
    tf: str
    dir: str
    i: int             # displacement candle index
    low: float
    high: float
    event: Event
    formed_t: datetime
    tap_t: datetime | None
    invalid_t: datetime | None

    @property
    def near(self):
        return self.high if self.dir == "bullish" else self.low

    @property
    def far(self):
        return self.low if self.dir == "bullish" else self.high


def order_blocks(bars, struct, fvgs, tf, path):
    """Video-3 literal: the displacement candle (FVG candle 2) of the leg that produced a BOS/CHoCH.
    The earliest displacement candle in the leg is used. Zone = that candle's full range."""
    out = []
    for e in struct.events:
        if e.kind not in ("BOS", "CHoCH"):
            continue
        leg = [f for f in fvgs if f.dir == e.dir and e.origin_i <= f.i <= e.j]
        if not leg:
            continue
        f = min(leg, key=lambda x: x.i)
        c2 = bars[f.i]
        formed_idx = max(f.k, e.j)
        formed_t = bars[formed_idx].end
        if e.dir == "bullish":
            tap = path.first_touch("low", c2.h, formed_t)
            inv = next((bars[m].end for m in range(formed_idx + 1, len(bars)) if bars[m].c < c2.l), None)
        else:
            tap = path.first_touch("high", c2.l, formed_t)
            inv = next((bars[m].end for m in range(formed_idx + 1, len(bars)) if bars[m].c > c2.h), None)
        out.append(OrderBlock(tf, e.dir, f.i, c2.l, c2.h, e, formed_t, tap.t if tap else None, inv))
    return out
