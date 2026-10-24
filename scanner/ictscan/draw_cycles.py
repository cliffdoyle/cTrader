"""Draw previous-cycle highs/lows (PXH/PXL) and EQ on the ACTIVE cTrader chart (desktop app, MCP).
Usage from the scanner folder:
    python -m ictscan.draw_cycles NAS100                    # week, day and 90-minute cycles (15 objects)
    python -m ictscan.draw_cycles US30 --kinds month,week   # choose cycles
    python -m ictscan.draw_cycles NAS100 --dry-run          # print the plan only (no cTrader needed)
    python -m ictscan.draw_cycles NAS100 --boxes            # the latest day's six 90-minute windows as boxes (12 objects)
Each cycle kind gets one colour. PXH/PXL are solid lines and EQ is dotted, drawn only across the CURRENT cycle (from its
start to its end), with a short label at the left end, so the chart stays clean on every timeframe. Before drawing it
removes only the objects it drew last time (ids in DATA_DIR/drawn-cycles-<SYMBOL>.json, all MCP_ objects).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import DATA_DIR, DB_PATH, MCP_ENDPOINT, PARAMS, SYMBOLS, TIMEFRAMES, WINDOW
from .data import parse, sync
from .engine.context import Context
from .engine.core import make_bars
from .engine.cycles import KINDS, build_cycles, cycle_summary
from .mcp import MCPClient, MCPError
from .store import Store

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
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    symbol, kinds = a.symbol, [k for k in a.kinds.split(",") if k in KINDS]
    if symbol not in SYMBOLS:
        raise SystemExit(f"Unknown symbol {symbol}; known: {', '.join(SYMBOLS)}")
    store = Store(DB_PATH)
    client = None if a.dry_run else MCPClient(MCP_ENDPOINT)
    if client:
        server_now = parse(client.call("get_server_time")["utcTime"])
        for tf in TIMEFRAMES:
            sync(client, store, symbol, tf, server_now)
    bars = {tf: make_bars(store.load_bars(symbol, tf, WINDOW[tf]), tf) for tf in TIMEFRAMES}
    ctx = Context(symbol, bars, SYMBOLS[symbol], PARAMS)
    if a.boxes:
        objs, lines = plan_boxes(ctx)
        print(f"{symbol} {ctx.fmt(ctx.price)} at {ctx.now:%Y-%m-%d %H:%M} UTC — 90-minute windows (AM amber, PM blue):")
    else:
        objs, lines = plan(ctx, kinds)
        print(f"{symbol} {ctx.fmt(ctx.price)} at {ctx.now:%Y-%m-%d %H:%M} UTC — previous-cycle levels:")
    print("\n".join(lines))
    if a.dry_run:
        for o, thick, style in objs:
            print("   ", json.dumps(o), f"thickness={thick} style={style}" if thick else "")
        print(f"Dry run: {len(objs)} objects planned (nothing drawn).")
        return

    def active_ok():
        act = client.call("get_active_chart")
        if act.get("symbolName") != symbol:
            raise SystemExit(f"Active chart is {act.get('symbolName')} {act.get('timeframe')}; click inside a {symbol} chart and run again.")
        return act["chartId"]

    chart_id = active_ok()
    rec_file = DATA_DIR / f"drawn-{'cycleboxes' if a.boxes else 'cycles'}-{symbol}.json"
    old = json.loads(rec_file.read_text(encoding="utf-8")) if rec_file.exists() else {}
    if old.get("chartId") == chart_id:
        present = {o["objectId"] for o in client.call("get_chart_objects").get("objects", [])}
        for oid in old.get("ids", []):
            if oid.startswith("MCP_") and oid in present:
                active_ok()
                client.call("delete_chart_object", {"objectId": oid})
                time.sleep(0.15)
    ids = []
    for obj, thick, style in objs:
        active_ok()
        res = client.call("add_chart_object", obj)
        oid = res.get("objectId", "")
        if not oid.startswith("MCP_"):
            raise SystemExit(f"Unexpected response: {res}")
        ids.append(oid)
        if thick or style:
            try:
                client.call("update_chart_object", {"objectName": oid, "lineThickness": thick, "lineStyle": style})
            except MCPError as e:
                print(f"   (style not applied to {oid}: {e})")
        rec_file.write_text(json.dumps({"chartId": chart_id, "symbol": symbol, "ids": ids,
                                        "drawn": datetime.now().isoformat(timespec="seconds")}), encoding="utf-8")
        time.sleep(0.15)
    print(f"Drew {len(ids)} objects on chart {chart_id} ({'90-minute boxes' if a.boxes else ', '.join(kinds)}).")


if __name__ == "__main__":
    main()
