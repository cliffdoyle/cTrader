"""Scan loop: sync candles, run the analysis, persist setups and notifications. Survives restarts and cTrader outages."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .config import PARAMS, REFRESH, SCAN_DELAY_SECONDS, SYMBOLS, TIMEFRAMES, WINDOW
from .data import parse, sync
from .engine import mmxm
from .engine.context import Context
from .engine.core import make_bars
from .engine.mmxm_live import live_setups
from .engine.setups import detect_all
from .engine.smt import smt_summary
from .engine.topdown import topdown
from .mcp import MCPUnavailable
from .source import make_client, offline_hint

log = logging.getLogger("ictscan.runner")


class Scanner:
    def __init__(self, store):
        self.store = store
        self.client = make_client()
        self.last_fetch = {}
        self.stop = threading.Event()
        self.scan_now = threading.Event()

    def health(self, status, message, **extra):
        h = {"status": status, "message": message, "at": datetime.now(timezone.utc).isoformat(), **extra}
        self.store.set_meta("health", h)
        return h

    def run_live(self, symbols, every=2.0):
        """Keep the Open API spot stream for `symbols` alive (live prices for the dashboard, updated every second).
        Only for clients that support streaming; the desktop MCP source has no push quotes."""
        if not hasattr(self.client, "ensure_live"):
            return
        backoff = every
        while not self.stop.is_set():
            try:
                self.client.ensure_live(symbols)
                backoff = every
            except Exception as e:  # noqa: BLE001 — retry with backoff; the 5-minute scan keeps working regardless
                log.warning("live quotes: %s", e)
                backoff = min(backoff * 2, 60)
            self.stop.wait(backoff)

    def scan_once(self):
        t0 = time.time()
        server_now = parse(self.client.call("get_server_time")["utcTime"])
        new_symbols = {sym for sym in SYMBOLS if self.store.last_bar_t(sym, "m5") is None}
        fetched = 0
        for sym in SYMBOLS:
            for tf in TIMEFRAMES:
                key = f"{sym}:{tf}"
                if REFRESH[tf] == 0 or server_now.timestamp() - self.last_fetch.get(key, 0) >= REFRESH[tf]:
                    fetched += sync(self.client, self.store, sym, tf, server_now)
                    self.last_fetch[key] = server_now.timestamp()

        first_run = not self.store.get_meta("initialized", False)
        last_scan = self.store.get_meta("last_scan")
        quiet_before = server_now.isoformat() if first_run else None
        offline_since = None
        if last_scan and server_now - parse(last_scan) > timedelta(minutes=15):
            offline_since = last_scan
        new_notes, errors = 0, []
        index_bars = {}
        for sym, cfg in SYMBOLS.items():
            try:
                bars = {tf: make_bars(self.store.load_bars(sym, tf, WINDOW[tf]), tf) for tf in TIMEFRAMES}
                if any(len(b) < 50 for b in bars.values()):
                    errors.append(f"{sym}: not enough history yet")
                    continue
                if sym in mmxm.INDICES:
                    index_bars[sym] = bars
                ctx = Context(sym, bars, cfg, PARAMS)
                self.store.save_snapshot(sym, topdown(ctx))
                # A newly added symbol's backfilled history is pre-marked as read, like the first ever scan.
                quiet = server_now.isoformat() if sym in new_symbols else quiet_before
                new_notes += self.store.save_setups(detect_all(ctx), quiet, offline_since)
            except Exception as e:  # one bad symbol must not stop the others
                log.exception("analysis failed for %s", sym)
                errors.append(f"{sym}: {e}")
        if len(index_bars) == len(mmxm.INDICES):  # MMXM needs all three indices for SMT
            try:
                # The first MMXM scan backfills the last 7 days, and a rules change re-scores them; mark that history as
                # read so it does not pop up.
                fresh = self.store.get_meta("mmxm_initialized", False) and \
                    self.store.get_meta("mmxm_rules") == mmxm.RULES_VERSION
                quiet = quiet_before if fresh else server_now.isoformat()
                new_notes += self.store.save_setups(live_setups(index_bars, server_now), quiet, offline_since)
                self.store.set_meta("mmxm_initialized", True)
                self.store.set_meta("mmxm_rules", mmxm.RULES_VERSION)
            except Exception as e:
                log.exception("MMXM analysis failed")
                errors.append(f"MMXM: {e}")
            try:
                self.store.set_meta("smt", smt_summary(index_bars, server_now))
            except Exception as e:
                log.exception("SMT analysis failed")
                errors.append(f"SMT: {e}")
        self.store.set_meta("initialized", True)
        self.store.set_meta("last_scan", server_now.isoformat())
        dur = round(time.time() - t0, 1)
        msg = f"Scanned {len(SYMBOLS)} symbols in {dur}s; {fetched} new candles; {new_notes} new notifications"
        if offline_since:
            msg += f"; caught up after downtime since {offline_since}"
        self.health("ok" if not errors else "degraded", msg, errors=errors, last_scan=server_now.isoformat(), duration_s=dur)
        log.info(msg)

    def run_forever(self):
        backoff = 30
        while not self.stop.is_set():
            try:
                self.scan_once()
                backoff = 30
                now = time.time()
                wait = 300 - (now % 300) + SCAN_DELAY_SECONDS
            except MCPUnavailable as e:
                self.health("offline", f"{offline_hint()}: {e}")
                log.warning("data source offline: %s", e)
                wait = backoff
                backoff = min(backoff * 2, 300)
            except Exception as e:
                log.exception("scan failed")
                self.health("error", f"Scan failed: {e}")
                wait = backoff
                backoff = min(backoff * 2, 300)
            self.scan_now.wait(wait)
            self.scan_now.clear()
