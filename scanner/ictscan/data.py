"""Candle sync from cTrader into the local cache. Fills any gap since the last stored candle (e.g. after a restart)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .config import WINDOW
from .engine.core import TF_SECONDS

CALENDAR = {"m5": 1.7, "m15": 1.7, "h1": 1.6, "h4": 1.6, "d1": 1.5, "w1": 1.05}
PAGE = 1000


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fetch_range(client, symbol, tf, start, end, max_pages=30):
    """Page backwards: the server keeps the newest bars when a request is truncated."""
    got, to = {}, end
    for _ in range(max_pages):
        r = client.call("get_trendbars", {"symbolName": symbol, "timeframe": tf, "from": iso(start), "to": iso(to), "limit": PAGE})
        bars = r.get("bars") or []
        for b in bars:
            got[b["timestamp"]] = b
        if not bars or not r.get("truncated") or len(bars) < PAGE:
            break
        first = parse(bars[0]["timestamp"])
        if first <= start:
            break
        to = first - timedelta(seconds=1)
    return [got[k] for k in sorted(got)]


def sync(client, store, symbol, tf, server_now):
    dur = TF_SECONDS[tf]
    last = store.last_bar_t(symbol, tf)
    filled = store.get_meta(f"filled:{symbol}:{tf}", False)
    if last is None or not filled:
        start = server_now - timedelta(seconds=dur * WINDOW[tf] * CALENDAR[tf])
    else:
        start = datetime.fromtimestamp(last, timezone.utc)
    raw = fetch_range(client, symbol, tf, start, server_now)
    rows = []
    for b in raw:
        t = parse(b["timestamp"])
        if t + timedelta(seconds=dur) > server_now:
            continue  # still forming
        rows.append({"t": int(t.timestamp()), "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"], "v": b.get("volume", 0)})
    if rows:
        store.upsert_bars(symbol, tf, rows)
    if not filled:
        store.set_meta(f"filled:{symbol}:{tf}", True)
    return len(rows)
