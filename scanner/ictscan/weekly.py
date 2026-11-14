"""Weekly MMXM report — the live (forward) record of the market maker setups, one trading week at a time.

A setup belongs to the trading week (Sunday 18:00 → Friday 17:00 NY) in which it was confirmed, i.e. when its alert went
out; the confirmation time is the epoch at the end of the setup id. After the Friday close the runner posts one
notification per week (`maybe_notify`); the dashboard page /weekly shows any week (`report`).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from .engine.session import NY, trading_day

FINAL = ("target", "stopped", "expired", "missed", "invalidated")
OPEN = ("limit", "triggered")


def conf_time(rec):
    return datetime.fromtimestamp(int(rec["id"].split("-")[-1]), timezone.utc)


def mode_of(rec):
    return rec["id"].split("-")[2]


def friday_of(t):
    td = trading_day(t)
    return td + timedelta(days=4 - td.weekday())


def week_close(friday):
    return datetime.combine(friday, time(17, 0), NY).astimezone(timezone.utc)


def last_closed_week(now):
    fri = friday_of(now)
    return fri if now >= week_close(fri) else fri - timedelta(days=7)


def _tally(recs):
    filled = [r for r in recs if r.get("result_r") is not None]
    wins = [r for r in filled if r["result_r"] > 0]
    return {"setups": len(recs), "closed": len(filled), "wins": len(wins), "losses": len(filled) - len(wins),
            "open": sum(1 for r in recs if r["stage"] == "triggered"),
            "waiting": sum(1 for r in recs if r["stage"] == "limit"),
            "not_filled": sum(1 for r in recs if r["stage"] in ("expired", "missed", "invalidated") and r.get("result_r") is None),
            "net_r": round(sum(r["result_r"] for r in filled), 2),
            "best_r": round(max((r["result_r"] for r in filled), default=0), 2)}


def _by_mode(recs):
    return {m: _tally([r for r in recs if mode_of(r) == m]) for m in ("swing", "intraday")}


def _row(r):
    t = conf_time(r)
    return {"id": r["id"], "symbol": r["symbol"], "mode": mode_of(r), "dir": "buy" if r["dir"] == "bullish" else "sell",
            "confirmed": t.isoformat(), "confirmed_ny": t.astimezone(NY).strftime("%a %d %b %H:%M"),
            "entry": r.get("entry"), "stop": r.get("stop"), "target": r.get("tp1"), "rr": r.get("rr"), "stage": r["stage"],
            "stage_ny": datetime.fromisoformat(r["stage_t"]).astimezone(NY).strftime("%a %d %b %H:%M") if r.get("stage_t") else "",
            "result_r": r.get("result_r"), "superseded": bool(r.get("superseded"))}


def report(store, friday=None, now=None):
    """The week ending Friday `friday` (date; default the last closed week): every MMXM setup confirmed in it with its
    outcome, totals per mode, and the running forward record of every week the scanner has recorded."""
    now = now or datetime.now(timezone.utc)
    friday = friday or last_closed_week(now)
    recs = store.setups(kinds=["MMXM"], limit=100000)
    weeks = {}
    for r in recs:
        weeks.setdefault(friday_of(conf_time(r)), []).append(r)
    this = sorted(weeks.get(friday, []), key=conf_time)
    history = [{"week": f.isoformat(), **{m: t for m, t in _by_mode(rs).items()}} for f, rs in sorted(weeks.items())]
    return {"week": friday.isoformat(), "from": (week_close(friday) - timedelta(days=5)).isoformat(),
            "to": week_close(friday).isoformat(), "closed": now >= week_close(friday),
            "totals": _by_mode(this), "setups": [_row(r) for r in this],
            "forward": _by_mode(recs), "since": min((conf_time(r) for r in recs), default=now).isoformat(),
            "history": history, "weeks": sorted((f.isoformat() for f in weeks), reverse=True)}


def summary_text(rep):
    def part(name, t):
        if not t["setups"]:
            return f"{name}: no setups"
        n = f"{t['setups']} setup" + ("s" if t["setups"] != 1 else "")
        still = f", {t['open']} open" if t["open"] else ""
        return f"{name}: {n}, {t['wins']} won / {t['losses']} lost{still}, {t['net_r']:+.1f}R"
    w, f = rep["totals"], rep["forward"]
    return (f"{part('Swing', w['swing'])} · {part('Intraday', w['intraday'])}. Forward record since "
            f"{datetime.fromisoformat(rep['since']).astimezone(NY):%d %b}: swing {f['swing']['net_r']:+.1f}R "
            f"({f['swing']['closed']} closed), intraday {f['intraday']['net_r']:+.1f}R ({f['intraday']['closed']} closed).")


def maybe_notify(store, now):
    """After the Friday close, post the week's report once (also catches up if the scanner was off at the close)."""
    friday = last_closed_week(now)
    if store.get_meta("mmxm_weekly_sent") == friday.isoformat():
        return 0
    rep = report(store, friday, now)
    title = f"📊 MMXM weekly report — week to Fri {friday:%d %b}"
    n = store.add_notification(f"WEEKLY-MMXM-{friday.isoformat()}", "ALL", "report", "result", now.isoformat(), title,
                               summary_text(rep))
    store.set_meta("mmxm_weekly_sent", friday.isoformat())
    return n


def parse_week(s):
    """'YYYY-MM-DD' of any day → the Friday that ends its week (None → default)."""
    if not s:
        return None
    d = date.fromisoformat(s)
    return d + timedelta(days=(4 - d.weekday()) % 7)
