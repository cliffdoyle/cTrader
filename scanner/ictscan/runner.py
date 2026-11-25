"""Scan loop: sync candles, run the analysis, persist setups and notifications. Survives restarts and cTrader outages."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import weekly
from .config import (M4H_ENABLED, M4H_LOOKBACK_DAYS, M4H_SYMBOLS, M4H_TIMEFRAMES, M4H_WINDOW, PARAMS, REFRESH,
                     SCAN_DELAY_SECONDS, SYMBOLS, TIMEFRAMES, WINDOW)
from .data import parse, sync
from .engine import m4h, m4h_live, mmxm
from .engine.context import Context
from .engine.core import make_bars
from .engine.mmxm_live import live_setups
from .engine.setups import detect_all
from .engine.smt import PAIRS as SMT_PAIRS, meta_key as smt_meta_key, smt_summary
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
        self.m4h_slot = None

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
                active = self.store.setups(active_only=True, kinds=["MMXM"], limit=500)
                new_notes += self.store.save_setups(live_setups(index_bars, server_now, active=active), quiet, offline_since)
                self.store.set_meta("mmxm_initialized", True)
                self.store.set_meta("mmxm_rules", mmxm.RULES_VERSION)
            except Exception as e:
                log.exception("MMXM analysis failed")
                errors.append(f"MMXM: {e}")
            for pair, syms in SMT_PAIRS.items():
                try:
                    self.store.set_meta(smt_meta_key(pair), smt_summary({s: index_bars[s] for s in syms}, server_now))
                except Exception as e:
                    log.exception("SMT analysis failed (%s)", pair)
                    errors.append(f"SMT {pair}: {e}")
        try:
            new_notes += self.scan_m4h(server_now, quiet_before, offline_since, errors)
        except MCPUnavailable:
            raise
        except Exception as e:
            log.exception("M4H scan failed")
            errors.append(f"M4H: {e}")
        try:
            new_notes += weekly.maybe_notify(self.store, server_now)
        except Exception as e:
            log.exception("weekly report failed")
            errors.append(f"weekly report: {e}")
        self.store.set_meta("initialized", True)
        self.store.set_meta("last_scan", server_now.isoformat())
        dur = round(time.time() - t0, 1)
        msg = f"Scanned {len(SYMBOLS)} symbols in {dur}s; {fetched} new candles; {new_notes} new notifications"
        if offline_since:
            msg += f"; caught up after downtime since {offline_since}"
        self.health("ok" if not errors else "degraded", msg, errors=errors, last_scan=server_now.isoformat(), duration_s=dur)
        log.info(msg)

    def scan_m4h(self, server_now, quiet_before, offline_since, errors):
        """4H manipulation candles on config.M4H_SYMBOLS, once per M30 close (every 30 minutes)."""
        if not M4H_ENABLED:
            return 0
        slot = int(server_now.timestamp()) // 1800
        if slot == self.m4h_slot:
            return 0
        bars_by_sym = {}
        for sym in M4H_SYMBOLS:
            try:
                for tf in M4H_TIMEFRAMES:
                    if tf != "m30" and sym in SYMBOLS:
                        continue  # the main scan keeps D1/H4 of its own symbols fresh
                    if tf == "d1" and self.m4h_slot is not None and slot % 2:
                        continue  # D1 hourly
                    sync(self.client, self.store, sym, tf, server_now)
                b = {tf: make_bars(self.store.load_bars(sym, tf, M4H_WINDOW[tf]), tf) for tf in M4H_TIMEFRAMES}
                if all(len(x) >= 50 for x in b.values()):
                    bars_by_sym[sym] = b
                else:
                    errors.append(f"M4H {sym}: not enough history yet")
            except MCPUnavailable:
                raise
            except Exception as e:  # one bad symbol must not stop the others
                log.exception("M4H data failed for %s", sym)
                errors.append(f"M4H {sym}: {e}")
        # Keep following stored setups that are still open (a swing-liquidity target can take weeks), within the candles
        # held in memory; older ones are closed as no longer tracked.
        horizon = server_now - timedelta(days=25)
        since = server_now - timedelta(days=M4H_LOOKBACK_DAYS)
        stale = []
        for a in self.store.setups(active_only=True, kinds=["M4H"], limit=2000):
            conf = parse(a["m4h"]["conf_t"])
            if conf < horizon:
                a["stage"], a["stage_t"] = "done", server_now.isoformat()
                a["m4h"]["untracked"] = True
                stale.append(a)
            else:
                since = min(since, conf)
        recs, board = m4h_live.live(bars_by_sym, server_now, (server_now - since).total_seconds() / 86400)
        fresh = self.store.get_meta("m4h_initialized", False) and self.store.get_meta("m4h_rules") == m4h.RULES_VERSION
        quiet = quiet_before if fresh else server_now.isoformat()
        new = self.store.save_setups([r for r in recs if r["m4h"]["alert"]], quiet, offline_since)
        self.store.save_setups([r for r in recs if not r["m4h"]["alert"]] + stale, "9999")  # listed, no pop-up
        self.store.set_meta("m4h_board", {"t": server_now.isoformat(), "rows": board})
        self.store.set_meta("m4h_initialized", True)
        self.store.set_meta("m4h_rules", m4h.RULES_VERSION)
        self.m4h_slot = slot
        log.info("M4H: %d markets, %d candles in the last %.0f days, %d new notifications", len(bars_by_sym), len(recs),
                 (server_now - since).total_seconds() / 86400, new)
        return new

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
