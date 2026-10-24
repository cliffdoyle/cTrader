"""Top-down summary per symbol: what the dashboard shows as the analysis narrative."""
from __future__ import annotations

from .core import dealing_range
from .session import session_summary
from .setups import ny

TF_LABEL = {"w1": "Weekly", "d1": "Daily", "h4": "4H", "h1": "1H", "m15": "15m", "m5": "5m"}


def tf_summary(ctx, tf):
    bars, st = ctx.bars[tf], ctx.st[tf]
    j = len(bars) - 1
    rng = dealing_range(st, j)
    events = [{"kind": e.kind, "dir": e.dir, "t": bars[e.j].end.isoformat(), "ny": ny(bars[e.j].t) + " candle",
               "level": ctx.fmt(e.level), "close": ctx.fmt(e.close), "prot": ctx.fmt(e.prot),
               "ref_t": bars[e.ref_i].t.isoformat(), "break_t": bars[e.j].t.isoformat()} for e in st.events[-4:]]
    out = {"tf": tf, "label": TF_LABEL[tf], "state": st.state[j], "events": events,
           "protected": ctx.fmt(st.prot[j][0]) if st.prot[j] else None, "range": None, "position": None}
    if rng:
        out["range"] = {k: ctx.fmt(v) for k, v in rng.items() if k != "state"}
        span = rng["high"] - rng["low"]
        if span > 0:
            pct = (ctx.price - rng["low"]) / span * 100
            out["position"] = {"pct": round(pct), "zone": "discount" if pct < 50 else "premium"}
    if tf in ("h4", "h1", "m15", "m5"):
        fv = [f for f in ctx.fvg[tf] if f.status(j) != "filled" and f.closed_through is None]
        fv = sorted(fv, key=lambda f: min(abs(ctx.price - f.low), abs(ctx.price - f.high)))[:4]
        out["fvgs"] = [{"dir": f.dir, "low": ctx.fmt(f.low), "high": ctx.fmt(f.high), "status": f.status(j),
                        "t": bars[f.i].t.isoformat(), "ny": ny(bars[f.i].t)} for f in fv]
        above = sorted([lv for lv in ctx.levels_at([tf], "high", ctx.now) if lv.price > ctx.price], key=lambda x: x.price)[:3]
        below = sorted([lv for lv in ctx.levels_at([tf], "low", ctx.now) if lv.price < ctx.price], key=lambda x: -x.price)[:3]
        out["liquidity"] = {"above": [{"name": x.name, "price": ctx.fmt(x.price)} for x in above],
                            "below": [{"name": x.name, "price": ctx.fmt(x.price)} for x in below]}
    if tf in ("h4", "h1"):
        obs = [o for o in ctx.obs[tf] if o.invalid_t is None][-3:]
        out["order_blocks"] = [{"dir": o.dir, "low": ctx.fmt(o.low), "high": ctx.fmt(o.high), "t": bars[o.i].t.isoformat(),
                                "ny": ny(bars[o.i].t), "tapped": o.tap_t is not None} for o in obs]
    return out


def narrative(ctx, tfs):
    h4, h1 = tfs["h4"], tfs["h1"]
    lines = []
    near = ctx.zones[:4]
    if near:
        lines.append("Key levels: " + "; ".join(
            f"{z['tf'].upper()} {ctx.fmt(z['low'])}–{ctx.fmt(z['high'])} ({z['position']}, strength {z['strength']}/5)" for z in near) + ".")
    else:
        lines.append("No strong weekly/daily zone within reach of price.")
    if h4["state"] in ("bullish", "bearish"):
        last = h4["events"][-1] if h4["events"] else None
        txt = f"4H bias is {h4['state']}"
        if last:
            txt += f" since the {last['kind']} on the {last['ny']} (close {last['close']} beyond {last['level']})"
        txt += f"; protected level {h4['protected']}."
        if h4["position"]:
            txt += f" Price sits at {h4['position']['pct']}% of the 4H range ({h4['position']['zone']})."
        lines.append(txt)
    else:
        lines.append("4H structure is undefined — no directional trades.")
    if h1["state"] == h4["state"] and h4["state"] in ("bullish", "bearish"):
        lines.append(f"1H agrees ({h1['state']}): A-grade alignment.")
    else:
        lines.append(f"1H is {h1['state']} — a pullback against the 4H; wait for 1H to realign.")
    wants = "discount" if h4["state"] == "bullish" else "premium"
    if h4["position"] and h4["state"] in ("bullish", "bearish"):
        if h4["position"]["zone"] == wants:
            lines.append(f"Price is already in {wants}: look for confirmation entries now.")
        else:
            lines.append(f"Price is in the wrong half for {h4['state']} entries; wait for a retrace into {wants}.")
    return lines


def topdown(ctx):
    tfs = {tf: tf_summary(ctx, tf) for tf in ("w1", "d1", "h4", "h1", "m15", "m5")}
    try:
        sess = session_summary(ctx)
    except Exception as e:  # noqa: BLE001 — the session panel must never break the scan
        sess = {"error": str(e)}
    return {
        "symbol": ctx.symbol, "price": ctx.fmt(ctx.price), "as_of": ctx.now.isoformat(), "as_of_ny": ny(ctx.now),
        "bias": tfs["h4"]["state"], "aligned": tfs["h1"]["state"] == tfs["h4"]["state"],
        "zones": [{**z, "low": ctx.fmt(z["low"]), "high": ctx.fmt(z["high"]), "mid": ctx.fmt(z["mid"]),
                   "distance": ctx.fmt(z["distance"])} for z in ctx.zones[:8]],
        "timeframes": tfs, "narrative": narrative(ctx, tfs), "session": sess,
        "unit": ctx.cfg["unit"], "pip": ctx.cfg["pip"],
    }
