"""Dashboard web app (FastAPI). Read-only views over the scanner store plus notification state."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ..config import SYMBOLS, TIMEFRAMES
from ..engine.core import TF_SECONDS

STATIC = Path(__file__).parent / "static"
ACTIVE = ("watch", "armed", "limit", "triggered")


def create_app(store, scanner=None):
    app = FastAPI(title="ICT Scanner", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def page(name):
        return FileResponse(STATIC / name, headers={"Cache-Control": "no-store"})

    @app.get("/")
    def index():
        return page("index.html")

    @app.get("/symbol/{sym}")
    def symbol_page(sym: str):
        return page("symbol.html")

    @app.get("/setup/{sid}")
    def setup_page(sid: str):
        return page("setup.html")

    @app.get("/notifications")
    def notifications_page():
        return page("notifications.html")

    @app.get("/api/overview")
    def overview():
        rows = []
        for sym in SYMBOLS:
            snap = store.snapshot(sym)
            if not snap:
                rows.append({"symbol": sym, "missing": True})
                continue
            tfs = snap["timeframes"]
            rows.append({
                "symbol": sym, "price": snap["price"], "as_of_ny": snap["as_of_ny"], "bias": snap["bias"],
                "aligned": snap["aligned"], "states": {tf: tfs[tf]["state"] for tf in TIMEFRAMES},
                "h4_position": tfs["h4"]["position"], "narrative": snap["narrative"],
            })
        active = [s for s in store.setups(active_only=True, limit=200)]
        recent = [s for s in store.setups(limit=60) if s["stage"] not in ACTIVE][:30]
        return {"health": store.get_meta("health"), "unread": store.unread_count(), "symbols": rows,
                "muted": store.get_meta("alerts_muted", False),
                "active": [brief(s) for s in active], "recent": [brief(s) for s in recent]}

    @app.get("/api/symbol/{sym}")
    def symbol(sym: str):
        if sym not in SYMBOLS:
            raise HTTPException(404, "unknown symbol")
        return {"snapshot": store.snapshot(sym), "setups": [brief(s) for s in store.setups(symbol=sym, limit=80)]}

    @app.get("/api/bars/{sym}/{tf}")
    def bars(sym: str, tf: str, n: int = 300, end: int | None = None):
        if sym not in SYMBOLS or tf not in TF_SECONDS:
            raise HTTPException(404, "unknown symbol/timeframe")
        rows = store.load_bars(sym, tf, 5000 if end else min(n, 3000))
        if end:
            rows = [r for r in rows if r["t"] <= end][-n:]
        return [{"time": r["t"], "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"]} for r in rows]

    @app.get("/backtest")
    def backtest_page():
        return page("backtest.html")

    @app.get("/api/backtest")
    def backtest(full: bool = False):
        out = {}
        for part in ("in-sample", "out-of-sample"):
            rep = store.get_meta(f"backtest:{part}")
            if rep and not full:
                rep = {k: v for k, v in rep.items() if k != "trades"}
            out[part] = rep
        out["variants"] = store.get_meta("backtest_variants:in-sample", {})
        return out

    @app.get("/api/setup/{sid}")
    def setup(sid: str):
        s = store.setup(sid)
        if not s:
            raise HTTPException(404, "unknown setup")
        rep = store.get_meta("backtest:in-sample")
        if rep:
            s["backtest"] = {"setup": rep["by_setup"].get(s["setup"]),
                             "setup_symbol": rep["by_setup_symbol"].get(f"{s['setup']} {s['symbol']}"),
                             "period": f"{rep['from'][:10]} → {rep['to'][:10]} (in-sample)"}
        return s

    @app.get("/api/notifications")
    def notifications(after_id: int = 0, limit: int = 100, unread: bool = False):
        return {"items": store.notifications(after_id, limit, unread), "unread": store.unread_count(),
                "muted": store.get_meta("alerts_muted", False)}

    @app.post("/api/notifications/read")
    def mark_read(ids: list[int] | None = Body(default=None, embed=True)):
        store.mark_read(ids)
        return {"unread": store.unread_count()}

    @app.get("/api/health")
    def health():
        return {"health": store.get_meta("health"), "server_time": datetime.now(timezone.utc).isoformat()}

    @app.post("/api/scan")
    def scan_now():
        if scanner is None:
            raise HTTPException(503, "scanner not running")
        scanner.scan_now.set()
        return {"ok": True}

    return app


def brief(s):
    keys = ("id", "symbol", "setup", "name", "dir", "poi_tf", "confirm_tf", "stage", "stage_t", "key_t", "entry", "stop",
            "tp1", "tp1_name", "rr", "result_r", "score")
    out = {k: s.get(k) for k in keys}
    out["poi"] = {k: s["poi"].get(k) for k in ("kind", "low", "high")}
    out["last_note"] = s["timeline"][-1]["note"] if s["timeline"] else ""
    out["last_ny"] = s["timeline"][-1]["ny"] if s["timeline"] else ""
    return out
