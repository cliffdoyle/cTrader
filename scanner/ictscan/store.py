"""SQLite persistence (outside OneDrive): candles, setups, notifications, snapshots, health."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS bars (symbol TEXT, tf TEXT, t INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL,
    PRIMARY KEY (symbol, tf, t));
CREATE TABLE IF NOT EXISTS setups (id TEXT PRIMARY KEY, symbol TEXT, setup TEXT, dir TEXT, stage TEXT, stage_t TEXT,
    key_t TEXT, score INTEGER, data TEXT, first_seen TEXT, updated TEXT);
CREATE INDEX IF NOT EXISTS setups_stage ON setups(stage, stage_t);
CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, setup_id TEXT, symbol TEXT, stage TEXT,
    level TEXT, t TEXT, created TEXT, title TEXT, body TEXT, read INTEGER DEFAULT 0, UNIQUE (setup_id, stage));
CREATE TABLE IF NOT EXISTS snapshots (symbol TEXT PRIMARY KEY, t TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def utcnow():
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # --- candles ---
    def upsert_bars(self, symbol, tf, bars):
        rows = [(symbol, tf, b["t"], b["o"], b["h"], b["l"], b["c"], b.get("v", 0)) for b in bars]
        with self.lock, self._conn() as c:
            c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)

    def last_bar_t(self, symbol, tf):
        with self._conn() as c:
            r = c.execute("SELECT MAX(t) FROM bars WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()
        return r[0]

    def count_bars(self, symbol, tf):
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM bars WHERE symbol=? AND tf=?", (symbol, tf)).fetchone()[0]

    def load_bars(self, symbol, tf, limit):
        with self._conn() as c:
            rows = c.execute("SELECT t,o,h,l,c FROM bars WHERE symbol=? AND tf=? ORDER BY t DESC LIMIT ?",
                             (symbol, tf, limit)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- setups and notifications ---
    def save_setups(self, setups, quiet_before=None, offline_since=None):
        now = utcnow().isoformat()
        new_notes = 0
        with self.lock, self._conn() as c:
            for s in setups:
                c.execute("""INSERT INTO setups (id, symbol, setup, dir, stage, stage_t, key_t, score, data, first_seen, updated)
                             VALUES (?,?,?,?,?,?,?,?,?,?,?)
                             ON CONFLICT(id) DO UPDATE SET stage=excluded.stage, stage_t=excluded.stage_t,
                             score=excluded.score, data=excluded.data, updated=excluded.updated""",
                          (s["id"], s["symbol"], s["setup"], s["dir"], s["stage"], s["stage_t"], s["key_t"], s["score"],
                           json.dumps(s), now, now))
                for ev in s["timeline"]:
                    title, body, level = describe(s, ev)
                    read = 1 if quiet_before and ev["t"] < quiet_before else 0
                    if offline_since and ev["t"] < now and ev["t"] >= offline_since and not read:
                        body = "(while the scanner was offline) " + body
                    cur = c.execute("""INSERT OR IGNORE INTO notifications (setup_id, symbol, stage, level, t, created, title, body, read)
                                       VALUES (?,?,?,?,?,?,?,?,?)""",
                                    (s["id"], s["symbol"], ev["stage"], level, ev["t"], now, title, body, read))
                    new_notes += cur.rowcount
        return new_notes

    def setups(self, symbol=None, active_only=False, limit=200, grades=None):
        q, args = "SELECT data FROM setups", []
        cond = []
        if symbol:
            cond.append("symbol=?")
            args.append(symbol)
        if active_only:
            cond.append("stage IN ('watch','armed','limit','triggered')")
        if grades:
            cond.append(f"json_extract(data, '$.grade') IN ({','.join('?' * len(grades))})")
            args.extend(grades)
        if cond:
            q += " WHERE " + " AND ".join(cond)
        q += " ORDER BY stage_t DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [json.loads(r[0]) for r in c.execute(q, args).fetchall()]

    def setup(self, sid):
        with self._conn() as c:
            r = c.execute("SELECT data FROM setups WHERE id=?", (sid,)).fetchone()
        return json.loads(r[0]) if r else None

    @staticmethod
    def _grade_cond(grades):
        if not grades:
            return "", []
        marks = ",".join("?" * len(grades))
        return f" AND setup_id IN (SELECT id FROM setups WHERE json_extract(data, '$.grade') IN ({marks}))", list(grades)

    def notifications(self, after_id=0, limit=100, unread_only=False, grades=None):
        gc, gargs = self._grade_cond(grades)
        q = "SELECT * FROM notifications WHERE id>?" + (" AND read=0" if unread_only else "") + gc + " ORDER BY t DESC, id DESC LIMIT ?"
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, (after_id, *gargs, limit)).fetchall()]

    def unread_count(self, grades=None):
        gc, gargs = self._grade_cond(grades)
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM notifications WHERE read=0" + gc, gargs).fetchone()[0]

    def mark_read(self, ids=None):
        with self.lock, self._conn() as c:
            if ids:
                c.executemany("UPDATE notifications SET read=1 WHERE id=?", [(i,) for i in ids])
            else:
                c.execute("UPDATE notifications SET read=1")

    # --- snapshots and meta ---
    def save_snapshot(self, symbol, data):
        with self.lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?)", (symbol, utcnow().isoformat(), json.dumps(data)))

    def snapshot(self, symbol):
        with self._conn() as c:
            r = c.execute("SELECT data FROM snapshots WHERE symbol=?", (symbol,)).fetchone()
        return json.loads(r[0]) if r else None

    def set_meta(self, key, value):
        with self.lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def get_meta(self, key, default=None):
        with self._conn() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default


LEVEL = {"watch": "info", "armed": "alert", "limit": "alert", "triggered": "alert", "breakeven_set": "alert",
         "add_limit": "alert", "add": "alert",
         "target": "result", "stopped": "result", "breakeven": "result", "invalidated": "info", "expired": "info", "missed": "info"}


def describe(s, ev):
    arrow = "▲" if s["dir"] == "bullish" else "▼"
    title = f"{arrow} {s['symbol']} {s['setup']} {ev['stage'].upper()} — {s['name']}"
    parts = [ev["note"]]
    if ev["stage"] in ("limit", "triggered", "watch") and s.get("entry") is not None:
        parts.append(f"Entry {s['entry']} · Stop {s['stop']} · TP1 {s['tp1']} ({s['rr']}R)")
    if ev["stage"] in ("target", "stopped", "breakeven", "expired") and s.get("result_r") is not None:
        parts.append(f"Result {s['result_r']:+}R")
    parts.append(f"{ev['ny']} NY · score {s['score']}/7" + (f" · grade {s['grade']}" if s.get("grade") else ""))
    return title, " · ".join(parts), LEVEL.get(ev["stage"], "info")
