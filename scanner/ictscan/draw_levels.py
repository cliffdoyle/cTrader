"""Draw today's session key levels on a cTrader chart of the symbol (desktop app, MCP). Usage from the scanner folder:
    python -m ictscan.draw_levels NAS100 [--untouched-only] [--dry-run]
Draws PWH/PWL, PDH/PDL, Asia, London and NY AM highs/lows plus the midnight open, each with a short label. Untouched
levels are solid/dashed lines extending to the right; levels already swept are thin dotted segments that stop at the
candle that took them, labelled "swept HH:MM" (NY) — omit them with --untouched-only. Drawing goes through
ictscan.drawing: the active chart if it shows the symbol, else that symbol's open chart, else a new one; redrawing removes
only the objects drawn last time (ids in DATA_DIR/drawn-levels-<SYMBOL>.json, all MCP_ objects). Also a dashboard button.
"""
from __future__ import annotations

import sys

from .engine.session import NY, key_levels, ny_time, session_summary, trading_day

COLORS = {"PW": "#B388FF", "PD": "#FFFFFF", "Asia": "#FFD600", "London": "#00E5FF", "NY AM": "#FF9100", "open": "#9E9E9E"}


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def plan_levels(ctx, untouched_only=False):
    """(objects, lines): objects are (add_chart_object args, thickness, style)."""
    kl = key_levels(ctx.bars["m5"], ctx.bars["d1"], ctx.bars["w1"], ctx.now)
    pad = ctx.atr["h1"][-1] * 0.12
    objs, drawn = [], []
    for lv in kl["levels"]:
        if not lv["untouched"] and untouched_only:
            continue
        tag = next(k for k in COLORS if lv["name"].startswith(k))
        color = COLORS[tag]
        label = f"{lv['name']} {ctx.fmt(lv['price'])}"
        if lv["untouched"]:
            objs.append(({"object_type": "trend_line", "price1": lv["price"], "time1": iso(lv["formed_t"]), "price2": lv["price"],
                          "time2": iso(ctx.now), "color": color, "extend": True}, 2, "solid" if tag in ("PW", "PD") else "lines"))
        else:  # swept: a thin dotted segment that stops at the candle that took it
            objs.append(({"object_type": "trend_line", "price1": lv["price"], "time1": iso(lv["formed_t"]), "price2": lv["price"],
                          "time2": iso(lv["taken_t"]), "color": color, "extend": False}, 1, "dots"))
            label += f" swept {lv['taken_t'].astimezone(NY):%H:%M}"
        objs.append(({"object_type": "text", "price1": ctx.fmt(lv["price"] + (pad if lv["side"] == "high" else -pad)),
                      "time1": iso(lv["formed_t"]), "color": color, "text": label}, None, None))
        drawn.append(lv["name"] + ("" if lv["untouched"] else " (swept)"))
    mo = kl["opens"].get("midnight")
    if mo is not None:
        t0 = ny_time(trading_day(ctx.now), 0, 0, 0)
        objs.append(({"object_type": "trend_line", "price1": mo, "time1": iso(t0), "price2": mo, "time2": iso(ctx.now),
                      "color": COLORS["open"], "extend": True}, 1, "dots"))
        objs.append(({"object_type": "text", "price1": ctx.fmt(mo - pad), "time1": iso(t0), "color": COLORS["open"],
                      "text": f"Midnight open {ctx.fmt(mo)}"}, None, None))
        drawn.append("midnight open")
    summary = session_summary(ctx)
    c = summary["context"]
    lines = [f"  trading day {summary['day']} — lean {c['lean']} ({c['bulls']} bull / {c['bears']} bear)"]
    lines += [f"   {comp['name']:30s} {comp['reading']:8s} {comp['note']}" for comp in c["components"]]
    if c["draw"]:
        d = c["draw"]
        lines.append(f"   nearest liquidity: above {d['above']} {d['above_price']} (+{d['above_dist']}), below {d['below']} "
                     f"{d['below_price']} (-{d['below_dist']}); random-walk odds upper first {d['p_above_first']}%")
    lines.append("  levels: " + ", ".join(drawn))
    return objs, lines


def main(symbol, untouched_only=False, dry_run=False):
    from .drawing import DrawError, draw
    from .mcp import MCPError
    try:
        res = draw(symbol, "levels", untouched_only=untouched_only, dry_run=dry_run)
    except (DrawError, MCPError) as e:
        raise SystemExit(str(e)) from e
    print("\n".join(res["lines"]))
    if dry_run:
        print(f"Dry run: {res['count']} objects planned (nothing drawn).")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "NAS100", untouched_only="--untouched-only" in sys.argv, dry_run="--dry-run" in sys.argv)