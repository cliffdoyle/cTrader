"""Per-symbol analysis context shared by the top-down summary and the setup detectors."""
from __future__ import annotations

from .core import (Path, atr_series, current_period_levels, dealing_range, equal_levels, find_fvgs, find_swings,
                   index_at, order_blocks, period_levels, structure, swing_levels)
from .levels import key_zones, zones_near


class Context:
    def __init__(self, symbol, bars, sym_cfg, params):
        self.symbol = symbol
        self.cfg = sym_cfg
        self.p = params
        self.bars = bars
        self.path = Path(bars)
        self.now = bars["m5"][-1].end
        self.price = bars["m5"][-1].c
        self.sw, self.atr, self.st, self.fvg, self.liq, self.obs = {}, {}, {}, {}, {}, {}
        for tf, b in bars.items():
            if not b:
                continue
            h, l = find_swings(b, params["swing_n"])
            self.sw[tf] = (h, l)
            self.atr[tf] = atr_series(b)
            self.st[tf] = structure(b, h, l)
            self.fvg[tf] = find_fvgs(b, params["fvg_mult"], params["fvg_lookback"])
        for tf in ("h4", "h1", "m15", "m5"):
            h, l = self.sw[tf]
            self.liq[tf] = swing_levels(bars[tf], h, l, tf, self.path) + \
                equal_levels(bars[tf], h, l, tf, self.path, self.atr[tf], params["equal_tol_atr"])
        for tf in ("h4", "h1"):
            self.obs[tf] = order_blocks(bars[tf], self.st[tf], self.fvg[tf], tf, self.path)
        start = bars["h1"][0].t
        self.period = [lv for lv in period_levels(bars["d1"], bars["w1"], self.path) if lv.until is None or lv.until >= start]
        self.zones_w = key_zones(bars["w1"], "w1", params)
        self.zones_d = key_zones(bars["d1"], "d1", params)
        self.zones = zones_near(self.zones_w, self.zones_d, self.price, self.atr["d1"][-1], params)

    # --- state lookups at a moment in time (no look-ahead) ---
    def idx(self, tf, t):
        return index_at(self.bars[tf], t)

    def state_at(self, tf, t):
        j = self.idx(tf, t)
        return self.st[tf].state[j] if j >= 0 else "undefined"

    def range_at(self, tf, t):
        return dealing_range(self.st[tf], self.idx(tf, t))

    def atr_at(self, tf, t):
        j = self.idx(tf, t)
        return self.atr[tf][max(j, 0)]

    def levels_at(self, tfs, side, t):
        out = [lv for tf in tfs for lv in self.liq[tf] if lv.side == side and lv.intact_at(t)]
        out += [lv for lv in current_period_levels(self.period, t) if lv.side == side and lv.intact_at(t)]
        return out

    def nearest_target(self, tfs, direction, entry, t):
        side = "high" if direction == "bullish" else "low"
        cands = [lv for lv in self.levels_at(tfs, side, t) if (lv.price > entry if side == "high" else lv.price < entry)]
        if not cands:
            return None
        return min(cands, key=lambda lv: abs(lv.price - entry))

    def zone_target(self, tf, direction, beyond):
        zones = [z for z in (self.zones_w if tf == "w1" else self.zones_d) if z["strength"] >= self.p["zone_min_strength"]]
        if direction == "bullish":
            c = [z for z in zones if z["low"] > beyond]
            return min(c, key=lambda z: z["low"]) if c else None
        c = [z for z in zones if z["high"] < beyond]
        return max(c, key=lambda z: z["high"]) if c else None

    def fmt(self, v):
        return None if v is None else round(v, self.cfg["digits"])
