"""Draw today's session key levels on the ACTIVE cTrader chart (desktop app, MCP). Usage from the scanner folder:
    python -m ictscan.draw_levels NAS100 [--untouched-only]
Draws PWH/PWL, PDH/PDL, Asia, London and NY AM highs/lows plus the midnight open, each with a short label. Untouched
levels are solid/dashed lines extending to the right; levels already swept are thin dotted segments that stop at the
candle that took them, labelled "swept HH:MM" (NY) — omit them with --untouched-only. Before drawing it removes only the
objects it drew last time (ids kept in DATA_DIR/drawn-levels-<SYMBOL>.json, all MCP_ objects); other drawings are never
touched.
"""
from __future__ import annotations

import json
import sys
import time

from .config import DATA_DIR, MCP_ENDPOINT, PARAMS, SYMBOLS, TIMEFRAMES, WINDOW
from .data import parse, sync
from .engine.context import Context
from .engine.core import make_bars
from .engine.session import key_levels, session_summary
from .mcp import MCPClient
from .store import Store
from .config import DB_PATH

COLORS = {"PW": "#B388FF", "PD": "#FFFFFF", "Asia": "#FFD600", "London": "#00E5FF", "NY AM": "#FF9100", "open": "#9E9E9E"}


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def main(symbol, untouched_only=False):
    if symbol not in SYMBOLS:
        raise SystemExit(f"Unknown symbol {symbol}; known: {', '.join(SYMBOLS)}")
    client, store = MCPClient(MCP_ENDPOINT), Store(DB_PATH)
    server_now = parse(client.call("get_server_time")["utcTime"])
    for tf in TIMEFRAMES:
        sync(client, store, symbol, tf, server_now)
    bars = {tf: make_bars(store.load_bars(symbol, tf, WINDOW[tf]), tf) for tf in TIMEFRAMES}
    ctx = Context(symbol, bars, SYMBOLS[symbol], PARAMS)
    kl = key_levels(bars["m5"], bars["d1"], bars["w1"], ctx.now)
    summary = session_summary(ctx)

    def active_ok():
        a = client.call("get_active_chart")
        if a.get("symbolName") != symbol:
            raise SystemExit(f"Active chart is {a.get('symbolName')} {a.get('timeframe')}; click inside a {symbol} chart and run again.")
        return a["chartId"]

    chart_id = active_ok()
    rec_file = DATA_DIR / f"drawn-levels-{symbol}.json"
    old = json.loads(rec_file.read_text(encoding="utf-8")) if rec_file.exists() else {}
    if old.get("chartId") == chart_id:
        present = {o["objectId"] for o in client.call("get_chart_objects").get("objects", [])}
        for oid in old.get("ids", []):
            if oid.startswith("MCP_") and oid in present:
                active_ok()
                client.call("delete_chart_object", {"objectId": oid})
                time.sleep(0.15)
    ids = []

    def add(obj, thick=None, style=None):
        active_ok()
        res = client.call("add_chart_object", obj)
        oid = res.get("objectId", "")
        if not oid.startswith("MCP_"):
            raise SystemExit(f"Unexpected response: {res}")
        ids.append(oid)
        if thick or style:
            client.call("update_chart_object", {"objectName": oid, **({"lineThickness": thick} if thick else {}),
                                                **({"lineStyle": style} if style else {})})
        rec_file.write_text(json.dumps({"chartId": chart_id, "symbol": symbol, "ids": ids}), encoding="utf-8")
        time.sleep(0.15)

    pad = ctx.atr["h1"][-1] * 0.12
    drawn = []
    from .engine.session import NY as _NY
    for lv in kl["levels"]:
        if not lv["untouched"] and untouched_only:
            continue
        tag = next(k for k in COLORS if lv["name"].startswith(k))
        color = COLORS[tag]
        label = f"{lv['name']} {ctx.fmt(lv['price'])}"
        if lv["untouched"]:
            add({"object_type": "trend_line", "price1": lv["price"], "time1": iso(lv["formed_t"]), "price2": lv["price"],
                 "time2": iso(ctx.now), "color": color, "extend": True}, thick=2, style="solid" if tag in ("PW", "PD") else "lines")
        else:  # swept: a thin dotted segment that stops at the candle that took it
            add({"object_type": "trend_line", "price1": lv["price"], "time1": iso(lv["formed_t"]), "price2": lv["price"],
                 "time2": iso(lv["taken_t"]), "color": color, "extend": False}, thick=1, style="dots")
            label += f" swept {lv['taken_t'].astimezone(_NY):%H:%M}"
        add({"object_type": "text", "price1": ctx.fmt(lv["price"] + (pad if lv["side"] == "high" else -pad)),
             "time1": iso(lv["formed_t"]), "color": color, "text": label})
        drawn.append(lv["name"] + ("" if lv["untouched"] else " (swept)"))
    mo = kl["opens"].get("midnight")
    if mo is not None:
        from .engine.session import ny_time, trading_day
        t0 = ny_time(trading_day(ctx.now), 0, 0, 0)
        add({"object_type": "trend_line", "price1": mo, "time1": iso(t0), "price2": mo, "time2": iso(ctx.now),
             "color": COLORS["open"], "extend": True}, thick=1, style="dots")
        add({"object_type": "text", "price1": mo - pad, "time1": iso(t0), "color": COLORS["open"], "text": f"Midnight open {ctx.fmt(mo)}"})
    c = summary["context"]
    print(f"{symbol} {ctx.fmt(ctx.price)} — trading day {summary['day']} — lean {c['lean']} ({c['bulls']} bull / {c['bears']} bear)")
    for comp in c["components"]:
        print(f"   {comp['name']:30s} {comp['reading']:8s} {comp['note']}")
    if c["draw"]:
        d = c["draw"]
        print(f"   nearest liquidity: above {d['above']} {d['above_price']} (+{d['above_dist']}), below {d['below']} "
              f"{d['below_price']} (-{d['below_dist']}); random-walk odds upper first {d['p_above_first']}%")
    print(f"Drew {len(ids)} objects on chart {chart_id}: {', '.join(drawn)}" + (", midnight open" if mo is not None else ""))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    main(args[0] if args else "NAS100", untouched_only="--untouched-only" in sys.argv)
