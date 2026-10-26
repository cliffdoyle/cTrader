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
# One colour per 90-minute window: pre-open violet, 08:30 amber (news/open), 10:00 teal, lunch grey, 13:00 blue, 14:30 pink
WINDOW_COLORS = ("9B7BFF", "F5B84A", "2BD4A4", "8A97A8", "5AA9FF", "FF7A9A")
FILL_ALPHA, LINE_ALPHA = "26", "B3"   # ≈15 % fill so candles stay visible, ≈70 % outline
SHORT = {"month": "M", "week": "W", "day": "D", "90m": "90m"}


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def plan(ctx, kinds):
    """Objects to draw: per kind PXH, PXL (solid) and EQ (dotted) across the current cycle; one label per price level,
    placed just right of the last candle (empty space) and merged when two cycles share a price."""
    summ = cycle_summary(ctx.bars, ctx.now, ctx.price, ctx.fmt)
    atr = ctx.atr["h1"][-1]
    objs, lines, labels = [], [], {}
    for kind in kinds:
        c = summ.get(kind)
        if not c:
            continue
        p, color = c["prev"], COLORS[kind]
        span = c["current"] or {"start": p["start"], "end": c.get("next_window") or p["end"]}
        t0, t1 = parse(span["start"]), parse(span["end"])
        if c["current"] is None and c.get("next_window"):  # between 90m windows: show the last window's levels over it
            t0, t1 = parse(p["start"]), parse(p["end"])
        for name, price, thick, style in (("PXH", p["high"], 2, "solid"), ("PXL", p["low"], 2, "solid"), ("EQ", p["eq"], 1, "dots")):
            objs.append(({"object_type": "trend_line", "price1": price, "time1": iso(t0), "price2": price, "time2": iso(t1),
                          "color": color, "extend": False}, thick, style))
        for side, price in (("PXH", p["high"]), ("PXL", p["low"])):
            lab = labels.setdefault((side, price), {"tags": [], "color": color, "side": side, "price": price})
            lab["tags"].append(SHORT[kind])
        lines.append(f"  {c['label']:<12} PXH {p['high']:<10} EQ {p['eq']:<10} PXL {p['low']:<10} → {c['read']['note']}")
    # stack labels so they never overlap: at least 0.3 × H1 ATR apart, highs nudged up, lows nudged down
    gap, placed = atr * 0.3, []
    for lab in sorted(labels.values(), key=lambda x: -x["price"]):
        y = lab["price"] + (atr * 0.08 if lab["side"] == "PXH" else -atr * 0.08)
        if placed and placed[-1] - y < gap:
            y = placed[-1] - gap
        placed.append(y)
        objs.append(({"object_type": "text", "price1": ctx.fmt(y), "time1": iso(ctx.now), "color": lab["color"], "right": True,
                      "anchor": "middle", "text": f"{' · '.join(lab['tags'])} {lab['side']} {lab['price']}"}, None, None))
    return objs, lines


def plan_boxes(ctx, style="filled"):
    """The 90-minute windows of the latest trading day as boxes (window high→low, start→end) with a label.
    style "filled" = TradingView-like session boxes: soft translucent fill (#AARRGGBB), crisp outline, one colour per
    window, label "08:30 · 163 pts"; style "outline" = outline only."""
    cyc = build_cycles(ctx.bars["m5"], "90m", ctx.now)
    if not cyc:
        return [], []
    day = cyc[-1]["key"][0]
    pad = ctx.atr["h1"][-1] * 0.10
    objs, lines = [], []
    for c in (c for c in cyc if c["key"][0] == day):
        i = c["key"][1]
        rgb = WINDOW_COLORS[i]
        end = c["end"]  # full window width, also for the forming window (its height grows on each redraw)
        start_ny = c["start"].astimezone(NY).strftime("%H:%M")
        forming = c["end"] > ctx.now
        box = {"object_type": "rectangle", "price1": c["high"], "time1": iso(c["start"]), "price2": c["low"], "time2": iso(end)}
        if style == "filled":
            objs.append(({**box, "color": f"#{FILL_ALPHA}{rgb}", "fill": True}, None, None))
        objs.append(({**box, "color": f"#{LINE_ALPHA}{rgb}", "fill": False}, 1, "lines" if forming else "solid"))
        pad2 = ctx.atr["h1"][-1] * 0.03
        objs.append(({"object_type": "text", "price1": ctx.fmt(c["high"] + pad2), "time1": iso(c["start"]), "color": f"#{rgb}",
                      "anchor": "above", "text": f"{start_ny}  H {ctx.fmt(c['high'])}{' …' if forming else ''}"}, None, None))
        objs.append(({"object_type": "text", "price1": ctx.fmt(c["low"] - pad2), "time1": iso(c["start"]), "color": f"#{rgb}",
                      "anchor": "below", "text": f"L {ctx.fmt(c['low'])}"}, None, None))
        lines.append(f"  {start_ny}–{c['end'].astimezone(NY):%H:%M} NY  high {ctx.fmt(c['high'])}  low {ctx.fmt(c['low'])}"
                     + ("  (forming)" if forming else ""))
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