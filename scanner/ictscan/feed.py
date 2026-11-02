"""Drawing feed for chart add-ons (cTrader indicator, MT5 expert) — the scanner's levels as plain drawing instructions.

GET /api/feed/<symbol>?groups=sessions,boxes,cycles,levels,smt&kinds=week,day,90m&smt=all&tier=1&sn=asia,london&days=3
    &token=<ICTSCAN_FEED_TOKEN>
`symbol` may be a broker name (USTEC, US30.cash, SPX500…); it is mapped to a scanner symbol. `smt` picks the indices the
SMT compares: all (NAS100/US30/US500, default) | nq-es (NAS100 vs US500) | nq-ym (NAS100 vs US30) | es-ym (US500 vs US30).
`sn` = session boxes (asia, london, ny), `days` = how many trading days of session boxes (1–5), `tier` = the chart's
timeframe class for fitting (see FIT_*; 0 = draw everything asked for). Defaults match the first add-on versions.
Response is text/plain:

    #ICTSCAN|1|NAS100|<price>|<last M5 bar open, unix UTC>|<last M5 close>|<generated unix>
    <kind>|<group>|<id>|<t1>|<p1>|<t2>|<p2>|<AARRGGBB>|<fill 0/1>|<width>|<style>|<extend 0/1>|<text>

kind R = rectangle, L = line, T = text (t2/p2 empty), P = corner panel line (top-right, in order; no time/price).
Times are unix seconds UTC. style = solid | dash | dot for R/L; for T/P it is the text anchor (above | below | middle)
and extend is a flag set: 1 = "place right of the last candle" (the add-on picks the time for its own timeframe),
2 = "one text row further out" (the add-on converts a row to price at the current zoom so neighbouring labels never collide),
4 = bold.
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

GROUPS = ("sessions", "boxes", "cycles", "levels", "smt")
SESSION_KEYS = {"asia": "Asia", "london": "London", "ny": "NY AM"}
# Fit to the chart's timeframe (add-on setting): tier 1 ≤ M5 · 2 ≤ M30 · 3 = H1 · 4 ≤ H4 · 5 ≥ D1 (0 = no fitting).
# Each item is drawn up to the highest tier where it is still several candles wide; everything else would collapse into
# one or two candles and only clutter the chart.
FIT_GROUPS = {"boxes": 2, "sessions": 3, "levels": 5, "cycles": 5, "smt": 5}
FIT_KINDS = {"90m": 2, "day": 4, "week": 5, "month": 5}
FIT_LEVEL_TAGS = {4: ("PW", "PD"), 5: ("PW",)}  # higher timeframes: only the previous week/day highs and lows
DEFAULT_KINDS = ["week", "day", "90m"]
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
        kind = {"rectangle": "R", "trend_line": "L", "text": "T", "panel": "P"}.get(o["object_type"])
        if not kind:
            continue
        t1 = str(_unix(o["time1"])) if o.get("time1") else "0"
        p1 = str(fmt(o["price1"])) if o.get("price1") is not None else "0"
        t2 = str(_unix(o["time2"])) if o.get("time2") else ""
        p2 = str(fmt(o["price2"])) if o.get("price2") is not None else ""
        if kind in ("T", "P"):  # text: style = anchor (above | below | middle); extend = flags 1 = right of the last
            st = o.get("anchor", "above")  # candle, 2 = one text row further out (neighbouring labels), 4 = bold
            ext = str((1 if o.get("right") else 0) | (2 if o.get("row") else 0) | (4 if o.get("bold") else 0))
        else:
            st, ext = STYLE.get(style, "solid"), "1" if o.get("extend") else "0"
        out.append("|".join([kind, group, f"{group}{i}", t1, p1, t2, p2, _argb(o.get("color")), "1" if o.get("fill") else "0",
                             str(thick or 1), st, ext, _clean(o.get("text"))]))
    return out


def fit(tier, groups, kinds):
    """(groups, cycle kinds) that read well on a chart of this timeframe tier; tier 0 keeps everything asked for."""
    kinds = list(kinds or DEFAULT_KINDS)
    if not tier:
        return list(groups), kinds
    return [g for g in groups if tier <= FIT_GROUPS.get(g, 5)], [k for k in kinds if tier <= FIT_KINDS.get(k, 5)]


def build_feed(store, symbol, groups=GROUPS, kinds=None, sess="kz", smt="all", tier=0, sessions=("Asia", "London"), days=1):
    from .drawing import PANEL_DIM, plan_for, plan_sessions, plan_smt
    from .draw_levels import plan_levels
    from .engine.smt import PAIRS, meta_key
    smt = smt if smt in PAIRS else "all"
    groups, kinds = fit(tier, groups, kinds)
    key = (symbol, tuple(groups), tuple(kinds), sess, smt, tier, tuple(sessions), days)
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
                summ = store.get_meta(meta_key(smt)) or {}
                if not summ.get("verdict"):
                    continue
                if symbol not in (summ.get("symbols") or []):
                    pair = " vs ".join(PAIRS[smt])
                    objs = [({"object_type": "panel", "color": PANEL_DIM,
                              "text": f"SMT ({pair}) · {symbol} is not in this comparison"}, None, None)]
                else:
                    objs, _ = plan_smt(ctx, summ)
            elif g == "cycles":
                if not kinds:
                    continue
                _, objs, _ = plan_for(g, ctx, None, store, kinds)
            elif g == "sessions":
                if not sessions:
                    continue
                objs, _ = plan_sessions(ctx, sess, sessions, days, compact=tier == 3)
            elif g == "levels":
                objs, _ = plan_levels(ctx, tags=FIT_LEVEL_TAGS.get(tier))
            else:
                _, objs, _ = plan_for(g, ctx, None, store, kinds, sess=sess)
        except Exception as e:  # noqa: BLE001 — one group failing must not blank the others
            lines.append(f"#ERR|{g}|{_clean(e)}")
            continue
        lines += to_lines(g, objs, ctx.fmt)
    text = "\n".join(lines) + "\n"
    with _lock:
        _cache[key] = (time.time(), text)
    return text
