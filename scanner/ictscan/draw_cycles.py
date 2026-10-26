"""Draw previous-cycle highs/lows (PXH/PXL) and EQ on a cTrader chart of the symbol (desktop app, MCP).
Usage from the scanner folder:
    python -m ictscan.draw_cycles NAS100                    # week, day and 90-minute cycles (15 objects)
    python -m ictscan.draw_cycles US30 --kinds month,week   # choose cycles
    python -m ictscan.draw_cycles NAS100 --dry-run          # print the plan only (no cTrader needed)
    python -m ictscan.draw_cycles NAS100 --boxes            # the latest day's six 90-minute windows as boxes (12 objects)
Each cycle kind gets one colour. PXH/PXL are solid lines and EQ is dotted, drawn only across the CURRENT cycle (from its
start to its end), with a short label at the left end, so the chart stays clean on every timeframe. Drawing goes through
ictscan.drawing (the active chart if it shows the symbol, else that symbol's open chart, else a new one); redrawing
replaces only the objects drawn last time (ids in DATA_DIR/drawn-cycles-<SYMBOL>.json, all MCP_ objects).
The same drawings are available as buttons on the dashboard (Time cycles and symbol pages) while the PC scanner runs.
"""
from __future__ import annotations

import argparse
import json
from zoneinfo import ZoneInfo

from .data import parse
from .engine.cycles import KINDS, build_cycles, cycle_summary
from .mcp import MCPError

NY = ZoneInfo("America/New_York")
COLORS = {"month": "#E3E9F1", "week": "#B388FF", "day": "#5AA9FF", "90m": "#F5B84A"}
BOX_COLORS = ("#F5B84A", "#5AA9FF")  # AM windows, PM windows
SHORT = {"month": "M", "week": "W", "day": "D", "90m": "90m"}


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def plan(ctx, kinds):
    """Objects to draw: per kind PXH, PXL, EQ lines across the current cycle and two labels."""
    summ = cycle_summary(ctx.bars, ctx.now, ctx.price, ctx.fmt)
    pad = ctx.atr["h1"][-1] * 0.12
    objs, lines = [], []
    for kind in kinds:
        c = summ.get(kind)
        if not c:
            continue
        p, color = c["prev"], COLORS[kind]
        span = c["current"] or {"start": p["start"], "end": c.get("next_window") or p["end"]}
        t0, t1 = parse(span["start"]), parse(span["end"])
        if c["current"] is None and c.get("next_window"):  # between 90m windows: show the last window's levels over it
            t0, t1 = parse(p["start"]), parse(p["end"])
        tag = SHORT[kind]
        for name, price, thick, style in (("PXH", p["high"], 2, "solid"), ("PXL", p["low"], 2, "solid"), ("EQ", p["eq"], 1, "dots")):
            objs.append(({"object_type": "trend_line", "price1": price, "time1": iso(t0), "price2": price, "time2": iso(t1),
                          "color": color, "extend": False}, thick, style))
        objs.append(({"object_type": "text", "price1": ctx.fmt(p["high"] + pad), "time1": iso(t0), "color": color,
                      "text": f"{tag} PXH {p['high']}"}, None, None))
        objs.append(({"object_type": "text", "price1": ctx.fmt(p["low"] - pad), "time1": iso(t0), "color": color,
                      "text": f"{tag} PXL {p['low']}"}, None, None))
        lines.append(f"  {c['label']:<12} PXH {p['high']:<10} EQ {p['eq']:<10} PXL {p['low']:<10} → {c['read']['note']}")
    return objs, lines


def plan_boxes(ctx):
    """The 90-minute windows of the latest trading day as boxes (window high→low, start→end) with a start-time label."""
    cyc = build_cycles(ctx.bars["m5"], "90m", ctx.now)
    if not cyc:
        return [], []
    day = cyc[-1]["key"][0]
    pad = ctx.atr["h1"][-1] * 0.10
    objs, lines = [], []
    for c in (c for c in cyc if c["key"][0] == day):
        i = c["key"][1]
        color = BOX_COLORS[0] if i < 3 else BOX_COLORS[1]
        end = min(c["end"], ctx.now)
        start_ny = c["start"].astimezone(NY).strftime("%H:%M")
        objs.append(({"object_type": "rectangle", "price1": c["high"], "time1": iso(c["start"]), "price2": c["low"],
                      "time2": iso(end), "color": color, "fill": False}, 1, "solid"))
        objs.append(({"object_type": "text", "price1": ctx.fmt(c["high"] + pad), "time1": iso(c["start"]), "color": color,
                      "text": f"{start_ny}"}, None, None))
        lines.append(f"  {start_ny}–{c['end'].astimezone(NY):%H:%M} NY  high {ctx.fmt(c['high'])}  low {ctx.fmt(c['low'])}"
                     + ("  (forming)" if c["end"] > ctx.now else ""))
    return objs, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="NAS100")
    ap.add_argument("--kinds", default="week,day,90m", help=f"comma list of {', '.join(KINDS)}")
    ap.add_argument("--boxes", action="store_true", help="draw today's six 90-minute windows as boxes instead of levels")
    ap.add_argument("--timeframe", default=None, help="timeframe for a new chart if none of this symbol is open (default m15)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    from .drawing import DrawError, draw
    try:
        res = draw(a.symbol, "boxes" if a.boxes else "cycles", kinds=[k for k in a.kinds.split(",") if k in KINDS],
                   timeframe=a.timeframe, dry_run=a.dry_run)
    except (DrawError, MCPError) as e:
        raise SystemExit(str(e)) from e
    print("\n".join(res["lines"]))
    if a.dry_run:
        for o in res["objects"]:
            print("   ", json.dumps(o))
        print(f"Dry run: {res['count']} objects planned (nothing drawn).")


if __name__ == "__main__":
    main()