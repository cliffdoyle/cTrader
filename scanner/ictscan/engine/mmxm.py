"""ICT Market Maker Model (MMXM) detector — rules in .github/skills/ctrader-mcp/references/strategy-mmxm.md.

Causal: a setup only uses candles closed by its confirmation time (FVG/OB fill flags from core are compared with bar
indices, never read ahead). The sell model is the buy model run on a price-mirrored copy of every series (high ↔ −low),
so both directions share one implementation; prices are mirrored back in the results.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import timedelta

from .core import Bar, Path, TF_SECONDS, atr_series, find_fvgs, find_swings, order_blocks, structure

MMXM_DEFAULTS = {
    "intraday": {"exec": "m5", "levels": ("h1", "h4"), "atr_tf": "h1", "window_h": 24, "curve_atr": 2.0, "mss_h": 12,
                 "fill_h": 2, "hold_h": 48, "bear_fvg_h": 6},
    "swing": {"exec": "m15", "levels": ("d1", "h4"), "atr_tf": "h4", "window_h": 7 * 24, "curve_atr": 2.0, "mss_h": 24,
              "fill_h": 12, "hold_h": 240, "bear_fvg_h": 24},
    "smt": "required",      # required | off | report
    "stop": "fvg",          # fvg = below the MSS FVG | low = below the reversal low
    "min_stop_pips": {"NAS100": 15, "US30": 20, "US500": 20},  # every stop (entry + adds) at least this far from its
    # entry (user 2026-10-27: 15–20 pips; backtest 2025-10 → 2026-10 best mix). A plain number applies to all; 0 = off
    "max_adds": 2,
    "add_risk": 0.5,
    "fvg_mult": 1.5,
}
INDICES = ("NAS100", "US30", "US500")
RULES_VERSION = "2026-10-27 min stop"  # bump when a change re-scores past setups (the runner then backfills them as read)


def mirror(bars):
    return [Bar(b.t, b.end, -b.o, -b.l, -b.h, -b.c) for b in bars]


@dataclass
class Series:
    """Everything the detector needs for one symbol in one direction (mirrored for sells)."""
    bars: dict
    exec_tf: str
    atr_tf: str
    levels: tuple
    fvg_mult: float
    ex: list = field(init=False)
    starts: list = field(init=False)

    def __post_init__(self):
        self.ex = self.bars[self.exec_tf]
        self.starts = [b.t for b in self.ex]
        self.sw_hi, self.sw_lo = find_swings(self.ex, 2)
        self.sw_hi_i = [h.i for h in self.sw_hi]
        fv = find_fvgs(self.ex, self.fvg_mult, 20)
        self.bull_fvg = sorted((f for f in fv if f.dir == "bullish"), key=lambda f: f.i)
        self.bear_fvg = sorted((f for f in fv if f.dir == "bearish"), key=lambda f: f.k)
        self.bull_i = [f.i for f in self.bull_fvg]
        self.bear_k = [f.k for f in self.bear_fvg]
        ab = self.bars[self.atr_tf]
        self.atr, self.atr_end = atr_series(ab), [b.end for b in ab]
        self.arrays = self._arrays()

    def _arrays(self):
        out, path = [], Path(self.bars)
        for tf in self.levels:
            b = self.bars[tf]
            fv = find_fvgs(b, self.fvg_mult, 20)
            for f in fv:
                if f.dir == "bullish":
                    out.append((f.low, f.high, b[f.k].end, b[f.closed_through].end if f.closed_through is not None else None,
                                f"{tf.upper()} FVG"))
            hi, lo = find_swings(b, 2)
            for ob in order_blocks(b, structure(b, hi, lo), fv, tf, path):
                if ob.dir == "bullish":
                    out.append((ob.low, ob.high, ob.formed_t, ob.invalid_t, f"{tf.upper()} OB"))
        return out

    def idx(self, t):
        return bisect.bisect_left(self.starts, t)

    def atr_at(self, t):
        k = bisect.bisect_right(self.atr_end, t) - 1
        return self.atr[max(k, 0)]

    def low_between(self, t0, t1):
        seg = self.ex[self.idx(t0):self.idx(t1)]
        return min(b.l for b in seg) if seg else None

    def array_at(self, price, t):
        """A still-valid bullish array that the reversal low traded into (largest first)."""
        hits = [a for a in self.arrays if a[2] <= t and (a[3] is None or a[3] > t) and price <= a[1]]
        return max(hits, key=lambda a: a[1] - a[0]) if hits else None


def smt_check(series_by_sym, t_ref0, t_ref1, t_rev1):
    took = {}
    for s, ser in series_by_sym.items():
        ref, rev = ser.low_between(t_ref0, t_ref1), ser.low_between(t_ref1, t_rev1)
        if ref is None or rev is None:
            return None
        took[s] = rev < ref
    return took


def stop_for(entry, structural, min_dist):
    """The structural stop, pushed further away when it is closer than `min_dist` to the entry (buy-side space)."""
    return min(structural, entry - min_dist)


def detect(series_by_sym, symbol, mode, cfg, buffer, sign, min_stop=0.0):
    """All MMXM buy setups for `symbol` on (possibly mirrored) series. Returns (setups, funnel)."""
    p = cfg[mode]
    S = series_by_sym[symbol]
    ex = S.ex
    dt = timedelta(seconds=TF_SECONDS[S.exec_tf])
    win, mss_w = timedelta(hours=p["window_h"]), timedelta(hours=p["mss_h"])
    bear_w = timedelta(hours=p["bear_fvg_h"])
    funnel = {"intermediate highs": 0, "retraced into bearish FVG": 0, "MSS with FVG": 0, "at reversal array": 0,
              "curve from terminus": 0, "SMT": 0}
    out, used_L = [], set()
    for H in S.sw_hi:
        funnel["intermediate highs"] += 1
        # 4. H was made by a retracement into a bearish FVG formed shortly before it
        k0 = bisect.bisect_left(S.bear_k, S.idx(ex[H.i].t - bear_w))
        k1 = bisect.bisect_left(S.bear_k, H.i)
        bfv = [f for f in S.bear_fvg[k0:k1] if H.price >= f.low and (f.closed_through is None or f.closed_through >= H.i)]
        if not bfv:
            continue
        funnel["retraced into bearish FVG"] += 1
        # 5. scan forward for the break of H
        end_t = ex[H.i].t + mss_w
        Li, j = None, None
        for m in range(H.i + 1, len(ex)):
            b = ex[m]
            if b.t > end_t:
                break
            if Li is None or b.l < ex[Li].l:
                Li = m
            if b.c > H.price and m >= H.conf:
                j = m
                break
        if j is None or Li is None or Li >= j:
            continue
        g0, g1 = bisect.bisect_left(S.bull_i, Li), bisect.bisect_right(S.bull_i, j)
        mss = [g for g in S.bull_fvg[g0:g1] if g.high > H.price]
        if not mss:
            continue
        g = mss[-1]
        c = max(j, g.k)
        conf_t = ex[c].end
        funnel["MSS with FVG"] += 1
        L = ex[Li]
        arr = S.array_at(L.l, L.t)
        if arr is None:
            continue
        funnel["at reversal array"] += 1
        # 1–2. terminus = highest high in the window before L, before H, far enough above L
        i0 = S.idx(L.t - win)
        Ti = max(range(i0, Li + 1), key=lambda m: ex[m].h)
        T = ex[Ti].h
        if not (Ti < H.i and T > H.price and T - L.l >= p["curve_atr"] * S.atr_at(L.t)):
            continue
        if not any(f.k > Ti for f in bfv):
            continue
        funnel["curve from terminus"] += 1
        # 6. SMT across the indices (curve before H vs H → MSS)
        took = smt_check(series_by_sym, ex[Ti].t, ex[H.i].t, conf_t)
        smt = took is not None and len(set(took.values())) == 2
        if cfg["smt"] == "required" and not smt:
            continue
        if smt:
            funnel["SMT"] += 1
        if Li in used_L or ("g", g.i) in used_L:
            continue
        used_L.update((Li, ("g", g.i)))
        entry = g.high
        stop = stop_for(entry, (g.low if cfg["stop"] == "fvg" else L.l) - buffer, min_stop)
        if not (stop < entry < T):
            continue
        out.append({"symbol": symbol, "mode": mode, "sign": sign, "H": H.price, "H_t": ex[H.i].t, "L": L.l, "L_t": L.t,
                    "T": T, "T_t": ex[Ti].t, "array": arr[4], "array_lo": arr[0], "array_hi": arr[1], "fvg": (g.low, g.high),
                    "entry": entry, "stop": stop, "conf_i": c, "conf_t": conf_t, "smt": smt, "took": took,
                    "rr": (T - entry) / (entry - stop)})
    return out, funnel


def simulate(S, st, cfg, mode, cost, buffer, min_stop=0.0):
    """Fill the low-risk entry, add on re-accumulation, exit at the terminus/stop/time. Result in R of the first unit.
    `events` records the timeline (prices in this series' space): fill, adds, exit. Live states: outcome "pending" (limit
    still waiting) or "open" (in the trade)."""
    p = cfg[mode]
    ex = S.ex
    fill_end = st["conf_t"] + timedelta(hours=p["fill_h"])
    fi, events = None, []
    for m in range(st["conf_i"] + 1, len(ex)):
        b = ex[m]
        if b.t >= fill_end:
            return {**st, "outcome": "expired", "exit_t": fill_end, "events": events}
        if b.l <= st["entry"]:
            fi = m
            break
        if b.h >= st["T"]:
            return {**st, "outcome": "missed", "exit_t": ex[m].end, "events": events}
        if b.c < st["L"]:
            return {**st, "outcome": "invalidated", "exit_t": ex[m].end, "events": events}
    if fi is None:
        return {**st, "outcome": "pending", "events": events}
    events.append(("filled", ex[fi].t, st["entry"], st["stop"]))
    units = [(st["entry"], st["entry"] - st["stop"], 1.0)]
    cur = st["stop"]
    hold_end = ex[fi].t + timedelta(hours=p["hold_h"])
    adds, pending, last_hi = 0, None, None
    hi_ptr = bisect.bisect_right(S.sw_hi_i, fi)

    def close(price, how, m):
        r = sum(w * (price - e) / risk - w * cost / risk for e, risk, w in units)
        return {**st, "outcome": how, "R": r, "adds": adds, "fill_t": ex[fi].t, "exit_t": ex[m].t, "exit": price,
                "events": events}

    for m in range(fi, len(ex)):
        b = ex[m]
        if b.t >= hold_end:
            return close(b.o, "time", m)
        if b.l <= cur:
            return close(cur, "stop" if cur < st["entry"] else "trail", m)
        if b.h >= st["T"]:
            return close(st["T"], "target", m)
        if pending and b.t < pending[3] and b.l <= pending[0]:
            e2, s2 = pending[0], pending[1]
            units.append((e2, e2 - s2, cfg["add_risk"]))
            adds += 1
            cur = max(cur, s2)
            events.append(("add", b.t, e2, cur))
            pending = None
            if b.l <= cur:
                return close(cur, "stop" if cur < st["entry"] else "trail", m)
        if pending and b.t >= pending[3]:
            pending = None
        # re-accumulation: a close above the latest swing high confirmed after the fill, with a bullish FVG in that leg
        while hi_ptr < len(S.sw_hi) and S.sw_hi[hi_ptr].conf <= m:
            last_hi = S.sw_hi[hi_ptr]
            hi_ptr += 1
        if adds < cfg["max_adds"] and pending is None and last_hi is not None and last_hi.i >= fi and b.c > last_hi.price:
            g0 = bisect.bisect_left(S.bull_i, last_hi.i)
            leg = [g for g in S.bull_fvg[g0:bisect.bisect_right(S.bull_i, m)] if g.k <= m + 1]
            s_add = stop_for(leg[-1].high, leg[-1].low - buffer, min_stop) if leg else None
            if leg and s_add > cur and leg[-1].high < st["T"]:
                g = leg[-1]
                pending = (g.high, s_add, m, ex[m].end + timedelta(hours=p["fill_h"]))
                events.append(("add_limit", ex[m].end, g.high, s_add))
            last_hi = None
    return {**st, "outcome": "open", "adds": adds, "fill_t": ex[fi].t, "stop_now": cur, "events": events,
            "add_pending": (pending[0], pending[1]) if pending else None}


def run(bars_by_sym, cfg=None, modes=("intraday", "swing"), buffers=None, costs=None, pips=None):
    """Detect and simulate MMXM buy and sell setups for every index in `bars_by_sym` (symbol → {tf: [Bar]}).
    `pips` (symbol → pip size, default 1.0 = index points) converts cfg["min_stop_pips"] into price."""
    cfg = {**MMXM_DEFAULTS, **(cfg or {})}
    min_stop = {s: (cfg["min_stop_pips"].get(s, 0) if isinstance(cfg["min_stop_pips"], dict) else cfg["min_stop_pips"])
                * (pips or {}).get(s, 1.0) for s in bars_by_sym}
    results, funnels = [], {}
    for mode in modes:
        p = cfg[mode]
        for sign in (1, -1):
            ser = {}
            for s, bars in bars_by_sym.items():
                use = bars if sign == 1 else {tf: mirror(b) for tf, b in bars.items()}
                ser[s] = Series(use, p["exec"], p["atr_tf"], p["levels"], cfg["fvg_mult"])
            for s in bars_by_sym:
                setups, fun = detect(ser, s, mode, cfg, buffers[s], sign, min_stop[s])
                funnels[(mode, "buy" if sign == 1 else "sell", s)] = fun
                for st in setups:
                    r = simulate(ser[s], st, cfg, mode, costs[s], buffers[s], min_stop[s])
                    r["L_stop"] = st["L"] - buffers[s]  # the wider stop the backtest favoured (shown as information)
                    if sign == -1:
                        for k in ("H", "L", "T", "entry", "stop", "array_lo", "array_hi", "exit", "stop_now", "L_stop"):
                            if k in r:
                                r[k] = -r[k]
                        r["fvg"] = (-r["fvg"][1], -r["fvg"][0])
                        r["array_lo"], r["array_hi"] = r["array_hi"], r["array_lo"]
                        r["events"] = [(e[0], e[1], -e[2], -e[3]) for e in r["events"]]
                        if r.get("add_pending"):
                            r["add_pending"] = (-r["add_pending"][0], -r["add_pending"][1])
                    r["dir"] = "buy" if sign == 1 else "sell"
                    results.append(r)
    return results, funnels
