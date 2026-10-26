"""Draw the scanner's levels on the cTrader desktop app (MCP) — shared by the command-line tools and the dashboard buttons.

What can be drawn per symbol (`what`):
  cycles — previous-cycle PXH / PXL / EQ for the chosen kinds (week, day, 90m, month) across the current cycle
  boxes  — the latest day's six 90-minute windows as boxes
  levels — today's session key levels (PWH/PWL, PDH/PDL, Asia, London, NY AM, midnight open; swept ones dotted)
  smt    — recent SMT divergences on this index (line between its two swing points + label) and live cycle SMT labels
  clear  — remove everything the scanner drew for this symbol (its own MCP_ objects only)
The target chart is the active chart if it shows the symbol, otherwise the first open chart of that symbol (brought to
the front), otherwise a new chart is opened. Before each object the active chart is checked again. Each kind keeps its
own record (DATA_DIR/drawn-<kind>-<SYMBOL>.json, ids per chart) and redrawing replaces only those objects.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime

from .config import DATA_DIR, DB_PATH, MCP_ENDPOINT, PARAMS, SYMBOLS, TIMEFRAMES, WINDOW
from .data import parse, sync
from .engine.context import Context
from .engine.core import make_bars
from .mcp import MCPClient, MCPError

KINDS = {"cycles": "cycles", "boxes": "cycleboxes", "levels": "levels", "smt": "smt"}
LOCK = threading.Lock()  # one drawing at a time (the MCP acts on the single active chart)


class DrawError(RuntimeError):
    pass


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _record_path(kind, symbol):
    return DATA_DIR / f"drawn-{KINDS[kind]}-{symbol}.json"


def _load_record(kind, symbol):
    p = _record_path(kind, symbol)
    if not p.exists():
        return {}
    rec = json.loads(p.read_text(encoding="utf-8"))
    if "charts" not in rec:  # old format: one chart
        rec = {"charts": {rec["chartId"]: rec.get("ids", [])}} if rec.get("chartId") else {"charts": {}}
    return rec


def _save_record(kind, symbol, rec):
    rec["drawn"] = datetime.now().isoformat(timespec="seconds")
    _record_path(kind, symbol).write_text(json.dumps(rec), encoding="utf-8")


class Chart:
    """The chart we draw on; re-focuses it before every change so a click elsewhere cannot redirect the drawing."""

    def __init__(self, client, symbol, timeframe=None):
        self.client, self.symbol = client, symbol
        charts = client.call("list_charts").get("charts", [])
        active = next((c for c in charts if c.get("isActive")), None)
        mine = [c for c in charts if c.get("symbolName") == symbol]
        if active and active.get("symbolName") == symbol:
            chart, self.how = active, "active chart"
        elif mine:
            chart = next((c for c in mine if timeframe and str(c.get("timeframe", "")).lower() == timeframe.lower()), mine[0])
            self.how = "brought to the front"
        else:
            chart = client.call("open_chart", {"symbolName": symbol, "timeframe": timeframe or "m15"})
            self.how = "opened"
            time.sleep(1.0)
        self.id, self.timeframe = chart["chartId"], chart.get("timeframe")
        self.ensure()

    @classmethod
    def by_id(cls, client, symbol, chart_id):
        ch = cls.__new__(cls)
        ch.client, ch.symbol, ch.id, ch.timeframe, ch.how = client, symbol, chart_id, None, "existing"
        ch.ensure()
        return ch

    def ensure(self):
        a = self.client.call("get_active_chart")
        if a.get("chartId") != self.id:
            self.client.call("focus_chart", {"chartId": self.id})
            time.sleep(0.3)
            a = self.client.call("get_active_chart")
        if a.get("chartId") != self.id or a.get("symbolName") != self.symbol:
            raise DrawError(f"Could not bring the {self.symbol} chart to the front (active: {a.get('symbolName')}).")

    def object_ids(self):
        return {o["objectId"] for o in self.client.call("get_chart_objects").get("objects", [])}


def _remove(client, chart, ids):
    present = chart.object_ids()
    n = 0
    for oid in ids:
        if oid.startswith("MCP_") and oid in present:
            chart.ensure()
            client.call("delete_chart_object", {"objectId": oid})
            n += 1
            time.sleep(0.12)
    return n


def load_ctx(client, store, symbol, sync_tfs=TIMEFRAMES):
    if client is not None:
        server_now = parse(client.call("get_server_time")["utcTime"])
        for tf in sync_tfs:
            sync(client, store, symbol, tf, server_now)
    bars = {tf: make_bars(store.load_bars(symbol, tf, WINDOW[tf]), tf) for tf in TIMEFRAMES}
    return Context(symbol, bars, SYMBOLS[symbol], PARAMS)


def plan_for(what, ctx, client, store, kinds=None, untouched_only=False):
    """(header, objects, lines) — objects are (add_chart_object args, thickness, style)."""
    if what == "cycles":
        from .draw_cycles import plan
        objs, lines = plan(ctx, kinds or ["week", "day", "90m"])
        return "previous-cycle levels (PXH / EQ / PXL)", objs, lines
    if what == "boxes":
        from .draw_cycles import plan_boxes
        objs, lines = plan_boxes(ctx)
        return "90-minute windows (filled session boxes, one colour per window)", objs, lines
    if what == "levels":
        from .draw_levels import plan_levels
        objs, lines = plan_levels(ctx, untouched_only)
        return "today's key levels", objs, lines
    if what == "smt":
        from .engine.smt import INDICES, smt_summary
        if ctx.symbol not in INDICES:
            raise DrawError(f"SMT is measured across {', '.join(INDICES)} only.")
        bars = {ctx.symbol: ctx.bars}
        for s in INDICES:
            if s != ctx.symbol:
                bars[s] = load_ctx(client, store, s, ("m5", "m15", "h1", "h4")).bars
        objs, lines = plan_smt(ctx, smt_summary(bars, ctx.now))
        return "SMT divergence", objs, lines
    raise DrawError(f"Unknown drawing '{what}'")


SMT_COL = {"bearish": "#FF5C6C", "bullish": "#2BD4A4"}


def plan_smt(ctx, summ):
    """Only what matters: the strongest SMT (direction) and the 5m/15m SMT that agrees with it (timing), short labels."""
    sym, atr = ctx.symbol, ctx.atr["h1"][-1]
    v = summ.get("verdict") or {}
    objs, lines = [], []
    for role, x in (("direction", v.get("bias")), ("timing", v.get("trigger"))):
        if not x:
            continue
        col, bear = SMT_COL[x["side"]], x["side"] == "bearish"
        arrow = "▼" if bear else "▲"
        if x["source"] == "cycle":
            c = next(c for c in summ["cycles"] if c["kind"] == x["tf"])
            q = c["per"][sym]
            px, t = (q["high"], q["high_t"]) if bear else (q["low"], q["low_t"])
            word = ("took PXH" if bear else "took PXL") if sym in x["leaders"] else ("held PXH" if bear else "held PXL")
            y = px + atr * 0.35 if bear else px - atr * 0.35
            objs.append(({"object_type": "text", "price1": ctx.fmt(y), "time1": iso(parse(t)), "color": col,
                          "text": f"{arrow} {x['name']} SMT ({word})"}, None, None))
        else:
            e = next(e for e in summ["swings"] if e["tf"] == x["tf"] and e["t2"] == x["t"])
            p = e["per"][sym]
            objs.append(({"object_type": "trend_line", "price1": p["p1"], "time1": iso(parse(p["t1"])),
                          "price2": p["p2"], "time2": iso(parse(p["t2"])), "color": col, "extend": False}, 2, "solid"))
            y = p["p2"] + atr * 0.25 if bear else p["p2"] - atr * 0.25
            objs.append(({"object_type": "text", "price1": ctx.fmt(y), "time1": iso(parse(p["t2"])), "color": col,
                          "text": f"{arrow} {x['name']} SMT {p['label']}"}, None, None))
        lines.append(f"  {role:9} {x['name']:6} {x['side']:8} {x['when']} NY  {x['text']}")
    lines.append(f"  → {v.get('summary', 'no SMT')}")
    return objs, lines


def draw(symbol, what, kinds=None, untouched_only=False, timeframe=None, dry_run=False, store=None, client=None):
    """Draw (or clear) one kind for one symbol. Returns a summary dict; raises DrawError / MCPError."""
    if symbol not in SYMBOLS:
        raise DrawError(f"Unknown symbol {symbol}")
    if what != "clear" and what not in KINDS:
        raise DrawError(f"Unknown drawing '{what}'")
    from .store import Store
    store = store or Store(DB_PATH)
    with LOCK:
        client = None if dry_run else (client or MCPClient(MCP_ENDPOINT))
        if what == "clear":
            removed = 0
            for kind in KINDS:
                rec = _load_record(kind, symbol)
                for cid, ids in list(rec.get("charts", {}).items()):
                    try:
                        removed += _remove(client, Chart.by_id(client, symbol, cid), ids)
                    except (MCPError, DrawError):
                        pass  # chart closed (its objects went with it) or now shows another symbol
                    rec["charts"].pop(cid, None)
                if rec:
                    _save_record(kind, symbol, rec)
            return {"ok": True, "symbol": symbol, "what": what, "count": removed,
                    "lines": [f"Removed {removed} scanner drawing(s) from {symbol} charts."]}
        ctx = load_ctx(client, store, symbol)
        header, objs, lines = plan_for(what, ctx, client, store, kinds, untouched_only)
        head = f"{symbol} {ctx.fmt(ctx.price)} — {header}"
        if dry_run:
            return {"ok": True, "symbol": symbol, "what": what, "count": len(objs), "lines": [head, *lines],
                    "objects": [o for o, _, _ in objs]}
        chart = Chart(client, symbol, timeframe)
        rec = _load_record(what, symbol)
        rec.setdefault("charts", {})
        removed = _remove(client, chart, rec["charts"].get(chart.id, []))
        ids = []
        for obj, thick, style in objs:
            chart.ensure()
            res = client.call("add_chart_object", obj)
            oid = res.get("objectId", "")
            if not oid.startswith("MCP_"):
                raise DrawError(f"Unexpected response: {res}")
            ids.append(oid)
            rec["charts"][chart.id] = ids
            _save_record(what, symbol, rec)
            if thick or style:
                try:
                    client.call("update_chart_object", {"objectName": oid, **({"lineThickness": thick} if thick else {}),
                                                        **({"lineStyle": style} if style else {})})
                except MCPError:
                    pass
            time.sleep(0.12)
        rec["charts"][chart.id] = ids
        _save_record(what, symbol, rec)
        return {"ok": True, "symbol": symbol, "what": what, "count": len(ids), "removed": removed,
                "chart": {"id": chart.id, "timeframe": chart.timeframe, "how": chart.how},
                "lines": [head, *lines, f"Drew {len(ids)} objects on the {symbol} {chart.timeframe or ''} chart ({chart.how})."]}


def status(client=None):
    client = client or MCPClient(MCP_ENDPOINT, timeout=8)
    charts = client.call("list_charts").get("charts", [])
    return {"ok": True, "charts": [{"symbol": c.get("symbolName"), "timeframe": c.get("timeframe"), "active": c.get("isActive")}
                                   for c in charts]}
