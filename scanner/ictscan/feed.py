"""Drawing feed for chart add-ons (cTrader indicator, MT5 expert) — the scanner's levels as plain drawing instructions.

GET /api/feed/<symbol>?groups=boxes,cycles,levels,smt&kinds=week,day,90m&token=<ICTSCAN_FEED_TOKEN>
`symbol` may be a broker name (USTEC, US30.cash, SPX500…); it is mapped to a scanner symbol. Response is text/plain:

    #ICTSCAN|1|NAS100|<price>|<last M5 bar open, unix UTC>|<last M5 close>|<generated unix>
    <kind>|<group>|<id>|<t1>|<p1>|<t2>|<p2>|<AARRGGBB>|<fill 0/1>|<width>|<style>|<extend 0/1>|<text>

kind R = rectangle, L = line, T = text (t2/p2 empty). Times are unix seconds UTC. style = solid | dash | dot.
Plain text so MQL5 can parse it without a JSON library. The objects are exactly what the dashboard draw buttons make
(`drawing.plan_for`), built from the scanner's stored candles — no cTrader desktop / MCP involved.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timezone

from .config import PARAMS, SYMBOLS, TIMEFRAMES, WINDOW
from .engine.context import Context
from .engine.core import make_bars

GROUPS = ("boxes", "cycles", "levels", "smt")
ALIASES = {
    "NAS100": ("NAS100", "USTEC", "US100", "NDX", "NAS", "USTECH", "NASDAQ"),
    "US30": ("US30", "DJ30", "WS30", "DOW", "DJI", "USA30"),
    "US500": ("US500", "SPX500", "SP500", "SPX", "USA500", "S&P500"),
    "XAUUSD": ("XAUUSD", "GOLD"),
}
STYLE = {None: "solid", "solid": "solid", "lines": "dash", "dots": "dot"}
_cache, _lock = {}, threading.Lock()
CACHE_S = 20


def resolve(name):
    """Broker symbol → scanner symbol (strips suffixes like .cash, m, _i, -ECN)."""
    raw = (name or "").upper().strip()
    if raw in SYMBOLS:
        return raw
    core = re.sub(r"[^A-Z0-9&]", "", raw)
    for sym, names in ALIASES.items():
        if any(core.startswith(n) for n in names):
            return sym
    for sym in SYMBOLS:
        if core.startswith(sym):
            return sym
    return None


def _argb(color):
    c = (color or "#FFFFFF").lstrip("#").upper()
    return c if len(c) == 8 else "FF" + c


def _unix(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def _clean(text):
    return str(text or "").replace("|", "/").replace("\n", " ").replace("\r", " ")


def to_lines(group, objs, fmt):
    out = []
    for i, (o, thick, style) in enumerate(objs):
        kind = {"rectangle": "R", "trend_line": "L", "text": "T"}.get(o["object_type"])
        if not kind:
            continue
        t2 = str(_unix(o["time2"])) if o.get("time2") else ""
        p2 = str(fmt(o["price2"])) if o.get("price2") is not None else ""
        out.append("|".join([kind, group, f"{group}{i}", str(_unix(o["time1"])), str(fmt(o["price1"])), t2, p2,
                             _argb(o.get("color")), "1" if o.get("fill") else "0", str(thick or 1),
                             STYLE.get(style, "solid"), "1" if o.get("extend") else "0", _clean(o.get("text"))]))
    return out


def build_feed(store, symbol, groups=GROUPS, kinds=None):
    from .drawing import plan_for, plan_smt
    key = (symbol, tuple(groups), tuple(kinds or ()))
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_S:
            return hit[1]
    bars = {tf: make_bars(store.load_bars(symbol, tf, WINDOW[tf]), tf) for tf in TIMEFRAMES}
    if any(len(b) < 50 for b in bars.values()):
        raise ValueError(f"not enough history for {symbol} yet")
    ctx = Context(symbol, bars, SYMBOLS[symbol], PARAMS)
    last = bars["m5"][-1]
    lines = [f"#ICTSCAN|1|{symbol}|{ctx.fmt(ctx.price)}|{int(last.t.timestamp())}|{ctx.fmt(last.c)}|"
             f"{int(datetime.now(timezone.utc).timestamp())}"]
    for g in groups:
        try:
            if g == "smt":
                summ = store.get_meta("smt") or {}
                if symbol not in (summ.get("symbols") or []) or not summ.get("verdict"):
                    continue
                objs, _ = plan_smt(ctx, summ)
            else:
                _, objs, _ = plan_for(g, ctx, None, store, kinds)
        except Exception as e:  # noqa: BLE001 — one group failing must not blank the others
            lines.append(f"#ERR|{g}|{_clean(e)}")
            continue
        lines += to_lines(g, objs, ctx.fmt)
    text = "\n".join(lines) + "\n"
    with _lock:
        _cache[key] = (time.time(), text)
    return text
