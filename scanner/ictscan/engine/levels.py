"""Weekly/daily key levels of market structure (strategy-spec.md §2)."""
from .core import atr_series, find_swings


def key_zones(bars, tf, params):
    if len(bars) < 30:
        return []
    highs, lows = find_swings(bars, params["swing_n"])
    atr = atr_series(bars)
    a = atr[-1]
    tol = params["zone_tol_atr"] * a
    n = len(bars)
    span = params["zone_move_bars"]
    cands = sorted(highs + lows, key=lambda s: s.body)
    clusters = []
    for s in cands:
        # neighbours within tol join, but a zone never spans more than 2 × tol (prevents chaining into huge zones)
        if clusters and s.body - clusters[-1][-1].body <= tol and s.body - clusters[-1][0].body <= 2 * tol:
            clusters[-1].append(s)
        else:
            clusters.append([s])

    zones = []
    for cl in clusters:
        lo = min(s.body for s in cl)
        hi = max(s.body for s in cl)
        turning = drastic = False
        for s in cl:
            before = bars[max(0, s.i - span):s.i]
            after = bars[s.i + 1:s.i + 1 + span]
            if not after:
                continue
            if s.side == "high":
                move_in = s.price - min(b.l for b in before) if before else 0
                move_out = s.price - min(b.l for b in after)
            else:
                move_in = max(b.h for b in before) - s.price if before else 0
                move_out = max(b.h for b in after) - s.price
            if move_in >= params["zone_turn_atr"] * a and move_out >= params["zone_turn_atr"] * a:
                turning = True
            if move_out >= params["zone_move_atr"] * a:
                drastic = True
        crit = {
            "turning point": turning,
            "multiple rejections": len(cl) >= 3,
            "support and resistance": len({s.side for s in cl}) == 2,
            "drastic move away": drastic,
            "recent": max(s.i for s in cl) >= n - params["zone_recent"][tf],
        }
        last = max(cl, key=lambda s: s.i)
        zones.append({
            "tf": tf, "low": lo, "high": hi, "mid": (lo + hi) / 2,
            "strength": sum(crit.values()), "criteria": crit, "touches": len(cl),
            "last_touch": bars[last.i].t.isoformat(),
        })
    return zones


def zones_near(zones_w, zones_d, price, atr_d1, params):
    out = []
    for z in zones_w + zones_d:
        if z["strength"] < params["zone_min_strength"]:
            continue
        dist = 0.0 if z["low"] <= price <= z["high"] else min(abs(price - z["low"]), abs(price - z["high"]))
        if dist <= params["zone_near_atr_d1"] * atr_d1:
            out.append({**z, "distance": dist, "position": "at price" if dist == 0 else ("above" if z["low"] > price else "below")})
    return sorted(out, key=lambda z: z["distance"])
