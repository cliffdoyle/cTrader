"""4H manipulation candle (Tom Camp) — rules: .github/skills/ctrader-mcp/references/strategy-m4h.md.

Pure functions over closed candles (broker D1, H4 and M30; H1 is built from M30). The sell logic is written once; buy
setups run the same code on a price-mirrored series (every price negated, so highs become lows) and are mirrored back.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .core import Bar, atr_series, find_swings, index_at, structure

NY = ZoneInfo("America/New_York")
RULES_VERSION = 1
DEFAULTS = {
    "fib50": 0.5,           # E2 level (fraction of the candle range, measured from the low for a sell)
    "ote": 0.705,           # E5 level
    "expiry_h4": 3,         # pending limits / the duplicate search live this many H4 candles after C
    "rr_min": 2.0,          # TLIQ only counts when the swing is at least this many R away
    "fvg_tfs": ("m30", "h1"),
    "swing_n": 2,
    "liq_lookback_h4": 360,  # H4 swings older than this (≈ 2.5 months) are not used as targets
    "max_hold_bars": 1440,  # M30 bars (≈ 30 trading days) before an open trade is left as "open"
}
ENTRIES = ("E1", "E2", "E3", "E4", "E5")
ENTRY_NAMES = {"E1": "4H close", "E2": "50% of the candle", "E3": "FVG inside the candle", "E4": "Duplicate (30m)",
               "E5": "OTE 70.5%"}
TARGETS = ("T2R", "TLIQ", "TLOW")
TARGET_NAMES = {"T2R": "2R", "TLIQ": "nearest swing liquidity (≥ 2R)", "TLOW": "the 4H candle's far end"}
# (name, start minute, end minute) in New York time; Asia wraps over midnight.
SESSIONS = (("London", 150, 450), ("NY AM", 450, 690), ("Lunch", 690, 780), ("PM", 780, 1005))
DAY_OPEN_NY = 17  # broker trading day: 17:00 → 17:00 New York


def session_of(t):
    n = t.astimezone(NY)
    m = n.hour * 60 + n.minute
    for name, a, b in SESSIONS:
        if a <= m < b:
            return name
    return "Asia" if m >= 1080 or m < 150 else "Close"


def day_bounds(t):
    n = t.astimezone(NY)
    start = n.replace(hour=DAY_OPEN_NY, minute=0, second=0, microsecond=0)
    if n < start:
        start -= timedelta(days=1)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def aggregate(bars, seconds):
    """Candles of `seconds` built from smaller ones, aligned to UTC multiples of `seconds` (H1 from M30)."""
    out = []
    for b in bars:
        start = datetime.fromtimestamp(int(b.t.timestamp()) // seconds * seconds, timezone.utc)
        if out and out[-1].t == start:
            x = out[-1]
            x.h, x.l, x.c = max(x.h, b.h), min(x.l, b.l), b.c
        else:
            out.append(Bar(start, start + timedelta(seconds=seconds), b.o, b.h, b.l, b.c))
    return out


def aggregate_ny(bars, hours=4, anchor=18):
    """H4 candles built from smaller ones on a New York anchor (Tom Camp's CME candles: 18, 22, 02, 06, 10, 14 NY)."""
    out = []
    step = timedelta(hours=hours)
    for b in bars:
        n = b.t.astimezone(NY)
        base = n.replace(hour=anchor, minute=0, second=0, microsecond=0)
        if n < base:
            base -= timedelta(days=1)
        start = (base + step * int((n - base) / step)).astimezone(timezone.utc)
        if out and out[-1].t == start:
            x = out[-1]
            x.h, x.l, x.c = max(x.h, b.h), min(x.l, b.l), b.c
        else:
            out.append(Bar(start, start + step, b.o, b.h, b.l, b.c))
    return out


def mirror(bars):
    return [Bar(b.t, b.end, -b.o, -b.l, -b.h, -b.c) for b in bars]


class RangeMin:
    """Sparse table: minimum low over bars[i..j] in O(1)."""

    def __init__(self, lows):
        self.t = [list(lows)]
        k = 1
        while 2 * k <= len(lows):
            p = self.t[-1]
            self.t.append([min(p[i], p[i + k]) for i in range(len(p) - k)])
            k *= 2

    def __call__(self, i, j):
        if j < i:
            return float("inf")
        k = (j - i + 1).bit_length() - 1
        return min(self.t[k][i], self.t[k][j - (1 << k) + 1])


@dataclass(slots=True)
class Frame:
    """One side's view of a symbol (the real series for sells, the mirrored one for buys)."""
    d1: list
    h4: list
    h1: list
    m30: list
    d1_state: list
    h4_state: list
    h4_lows: list
    d1_lows: list
    atr: list
    h4_min: RangeMin
    h4_starts: list
    m30_starts: list
    h1_starts: list


def frame(d1, h4, m30, n):
    h1 = aggregate(m30, 3600)
    dh, dl = find_swings(d1, n)
    hh, hl = find_swings(h4, n)
    return Frame(d1, h4, h1, m30, structure(d1, dh, dl).state, structure(h4, hh, hl).state, hl, dl, atr_series(h4),
                 RangeMin([b.l for b in h4]), [b.t for b in h4], [b.t for b in m30], [b.t for b in h1])


def gaps_inside(bars, starts, t0, t1):
    """Bearish imbalances (candle 1 low above candle 3 high) fully inside [t0, t1) and not filled by t1."""
    out = []
    i0 = bisect.bisect_left(starts, t0)
    i1 = bisect.bisect_left(starts, t1)
    for k in range(i0 + 2, i1):
        c1, c3 = bars[k - 2], bars[k]
        if c1.l <= c3.h or c3.end > t1:
            continue
        lo, hi = c3.h, c1.l
        if any(bars[m].h >= hi for m in range(k + 1, i1)):
            continue
        out.append((lo, hi, c3.end))
    return out


def nearest_liquidity(F, entry, t_dec, p):
    """Nearest untaken H4/D1 swing low below `entry`, known at t_dec (swing confirmed, never traded below since)."""
    i_dec = index_at(F.h4, t_dec)
    best = None
    lo_k = i_dec - p["liq_lookback_h4"]
    for s in F.h4_lows:
        if s.i < lo_k or s.conf > i_dec or F.h4[s.conf].end > t_dec or s.price >= entry:
            continue
        if F.h4_min(s.i + 1, i_dec) > s.price and (best is None or s.price > best[0]):
            best = (s.price, "H4 swing low", F.h4[s.i].t)
    for s in F.d1_lows:
        if s.conf >= len(F.d1) or F.d1[s.conf].end > t_dec or s.price >= entry:
            continue
        k = bisect.bisect_left(F.h4_starts, F.d1[s.i].end)
        if F.h4_min(k, i_dec) > s.price and (best is None or s.price > best[0]):
            best = (s.price, "D1 swing low", F.d1[s.i].t)
    return best


def simulate(F, kind, entry, stop, target, t0, cost, p):
    """Sell trade on M30. kind "market": filled at t0. kind "limit": a bar must trade above `entry`; the order expires
    after expiry_h4 × 8 M30 bars. Same-bar ambiguity is resolved against the trade (fill+target → missed,
    stop+target → stop). Returns a dict with outcome, fill_t, exit_t and net R (target/stopped only)."""
    risk = stop - entry
    m30 = F.m30
    i = bisect.bisect_left(F.m30_starts, t0)
    out = {"outcome": "pending" if kind == "limit" else "open", "fill_t": None, "exit_t": None}
    if kind == "market":
        out["fill_t"] = t0
    else:
        left = p["expiry_h4"] * 8
        while i < len(m30) and left > 0:
            b = m30[i]
            i += 1
            left -= 1
            if b.l <= target:
                out.update(outcome="missed", exit_t=b.end)
                return out
            if b.h > entry:
                out.update(outcome="open", fill_t=b.t)
                if b.h >= stop:
                    out.update(outcome="stopped", exit_t=b.end, R=round(-1 - cost / risk, 3))
                    return out
                break
        else:
            if left <= 0:
                out.update(outcome="expired", exit_t=m30[i - 1].end if i else t0)
            return out
    for b in m30[i:i + p["max_hold_bars"]]:
        if b.h >= stop:
            out.update(outcome="stopped", exit_t=b.end, R=round(-1 - cost / risk, 3))
            return out
        if b.l <= target:
            out.update(outcome="target", exit_t=b.end, R=round((entry - target - cost) / risk, 3))
            return out
    return out


def duplicate(F, C, t0, p):
    """First M30 candle d after C closes with d.high above the previous M30 high, a close below the previous body and
    d.high inside C's range. None if C's high trades first or the window ends."""
    i = bisect.bisect_left(F.m30_starts, t0)
    for k in range(i, min(i + p["expiry_h4"] * 8, len(F.m30))):
        d, prev = F.m30[k], F.m30[k - 1]
        if d.h > C.h:
            return None
        if d.h > prev.h and d.c < min(prev.o, prev.c) and d.h >= C.l:
            return d
    return None


def plans_for(F, i, cfg, p):
    """Entries (sell side) for H4 candle C = F.h4[i]."""
    C = F.h4[i]
    rng = C.h - C.l
    stop = C.h + cfg["spread"]
    out = [{"id": "E1", "kind": "market", "entry": C.c, "stop": stop, "t0": C.end},
           {"id": "E2", "kind": "limit", "entry": C.l + p["fib50"] * rng, "stop": stop, "t0": C.end},
           {"id": "E5", "kind": "limit", "entry": C.l + p["ote"] * rng, "stop": stop, "t0": C.end}]
    mid = C.l + p["fib50"] * rng
    best = None
    for tf in p["fvg_tfs"]:
        src = (F.m30, F.m30_starts) if tf == "m30" else (F.h1, F.h1_starts)
        for lo, hi, _ in gaps_inside(*src, C.t, C.end):
            if lo > C.c and (best is None or abs((lo + hi) / 2 - mid) < abs((best[0] + best[1]) / 2 - mid)):
                best = (lo, hi, tf)
    if best:
        out.append({"id": "E3", "kind": "limit", "entry": best[0], "stop": stop, "t0": C.end,
                    "fvg": {"low": best[0], "high": best[1], "tf": best[2]}})
    d = duplicate(F, C, C.end, p)
    if d is not None:
        out.append({"id": "E4", "kind": "market", "entry": d.c, "stop": d.h + cfg["spread"], "t0": d.end,
                    "dup": {"t": d.t, "high": d.h, "close": d.c}})
    return sorted(out, key=lambda x: x["id"])


def candle_setup(F, i, cfg, p):
    """Context and trades for one sell-side manipulation candle at F.h4[i] (caller checked the pattern)."""
    C, P = F.h4[i], F.h4[i - 1]
    j = bisect.bisect_left(F.m30_starts, C.t)
    inside = [b for b in F.m30[j:j + 8] if b.end <= C.end]
    ext_t = max(inside, key=lambda b: b.h).t if inside else C.t
    d0, d1 = day_bounds(ext_t)
    k0 = bisect.bisect_left(F.m30_starts, d0)
    k1 = bisect.bisect_left(F.m30_starts, C.end)
    k2 = bisect.bisect_left(F.m30_starts, d1)
    hod_so_far = all(b.h <= C.h for b in F.m30[k0:k1])
    held = None if not F.m30 or F.m30[-1].end < d1 else all(b.h <= C.h for b in F.m30[k1:k2])
    jd = index_at(F.d1, C.end)
    s = {"t": C.t, "conf_t": C.end, "C": (C.o, C.h, C.l, C.c), "P": (P.o, P.h, P.l, P.c),
         "d1_state": F.d1_state[jd] if jd >= 0 else None, "h4_state": F.h4_state[i],
         "ext_t": ext_t, "session": session_of(ext_t), "hod_so_far": hod_so_far, "hod_held": held,
         "range_atr": round((C.h - C.l) / F.atr[i], 2) if F.atr[i] else None, "plans": []}
    s["bias"] = s["d1_state"] == "bearish"
    s["momentum"] = s["h4_state"] == "bearish"
    for pl in plans_for(F, i, cfg, p):
        risk = pl["stop"] - pl["entry"]
        if risk <= 0:
            continue
        targets = {"T2R": (pl["entry"] - p["rr_min"] * risk, None)}
        liq = nearest_liquidity(F, pl["entry"], pl["t0"], p)
        targets["TLIQ"] = (liq[0], liq) if liq else (None, None)
        if pl["kind"] == "limit":
            targets["TLOW"] = (C.l, None)
        pl["trades"] = []
        for tid, (tp, info) in targets.items():
            tr = {"target": tid, "tp": tp, "rr": None if tp is None else round((pl["entry"] - tp) / risk, 2)}
            if info:
                tr["tp_name"], tr["tp_t"] = info[1], info[2]
            if tp is None:
                tr["outcome"] = "no_target"
            elif tid == "TLIQ" and tr["rr"] < p["rr_min"]:
                tr["outcome"] = "too_close"
            else:
                tr.update(simulate(F, pl["kind"], pl["entry"], pl["stop"], tp, pl["t0"], cfg["cost"], p))
            pl["trades"].append(tr)
        s["plans"].append(pl)
    return s


def _unmirror(s):
    """Mirrored (buy) setup → real prices and real-world names."""
    neg = lambda v: None if v is None else -v  # noqa: E731
    flip = {"bullish": "bearish", "bearish": "bullish"}
    s["C"] = tuple(-v for v in (s["C"][0], s["C"][2], s["C"][1], s["C"][3]))
    s["P"] = tuple(-v for v in (s["P"][0], s["P"][2], s["P"][1], s["P"][3]))
    s["d1_state"], s["h4_state"] = flip.get(s["d1_state"], s["d1_state"]), flip.get(s["h4_state"], s["h4_state"])
    for pl in s["plans"]:
        pl["entry"], pl["stop"] = -pl["entry"], -pl["stop"]
        if "fvg" in pl:
            pl["fvg"] = {"low": -pl["fvg"]["high"], "high": -pl["fvg"]["low"], "tf": pl["fvg"]["tf"]}
        if "dup" in pl:
            pl["dup"] = {"t": pl["dup"]["t"], "low": -pl["dup"]["high"], "close": -pl["dup"]["close"]}
        for tr in pl["trades"]:
            tr["tp"] = neg(tr["tp"])
            if tr.get("tp_name"):
                tr["tp_name"] = tr["tp_name"].replace("low", "high")
    return s


def run(bars, cfg, params=None, since=None):
    """All manipulation candles of one symbol. bars = {"d1", "h4", "m30"} (lists of core.Bar, oldest first);
    cfg = {"spread", "cost"} in price; `since` = only candles closing at/after this time. Newest last."""
    p = {**DEFAULTS, **(params or {})}
    out = []
    for side in ("sell", "buy"):
        src = {k: bars[k] if side == "sell" else mirror(bars[k]) for k in ("d1", "h4", "m30")}
        F = frame(src["d1"], src["h4"], src["m30"], p["swing_n"])
        for i in range(1, len(F.h4)):
            C, P = F.h4[i], F.h4[i - 1]
            if since is not None and C.end < since:
                continue
            if C.h > P.h and C.c < min(P.o, P.c):
                s = candle_setup(F, i, cfg, p)
                s["dir"] = side
                out.append(s if side == "sell" else _unmirror(s))
    return sorted(out, key=lambda s: (s["conf_t"], s["dir"]))
