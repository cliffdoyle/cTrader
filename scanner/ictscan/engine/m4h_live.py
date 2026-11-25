"""Turns 4H manipulation candles (engine/m4h.py) into scanner setup records for the /m4h page and pop-ups."""
from __future__ import annotations

from datetime import timedelta

from ..config import M4H_ALERT, M4H_SYMBOLS
from . import m4h
from .core import index_at
from .setups import ny, stage

OPEN = ("pending", "open")


def iso(t):
    return t.isoformat() if t is not None else None


def alert_ok(s):
    """Pop-up filter (config.M4H_ALERT); every candle is still stored and listed."""
    return ((not M4H_ALERT.get("bias") or s["bias"]) and (not M4H_ALERT.get("momentum") or s["momentum"])
            and (not M4H_ALERT.get("sessions") or s["session"] in M4H_ALERT["sessions"]))


def to_record(sym, s):
    dg = M4H_SYMBOLS[sym]["digits"]
    f = lambda v: None if v is None else round(v, dg)  # noqa: E731
    buy = s["dir"] == "buy"
    d = "bullish" if buy else "bearish"
    o, h, l, c = s["C"]
    plans = []
    for pl in s["plans"]:
        item = {"id": pl["id"], "name": m4h.ENTRY_NAMES[pl["id"]], "kind": pl["kind"], "entry": f(pl["entry"]),
                "stop": f(pl["stop"]), "t0": iso(pl["t0"]), "risk": f(abs(pl["entry"] - pl["stop"])), "trades": []}
        if "fvg" in pl:
            item["fvg"] = {"low": f(pl["fvg"]["low"]), "high": f(pl["fvg"]["high"]), "tf": pl["fvg"]["tf"]}
        if "dup" in pl:
            item["dup"] = {k: (iso(v) if k == "t" else f(v)) for k, v in pl["dup"].items()}
        for tr in pl["trades"]:
            item["trades"].append({"target": tr["target"], "tp": f(tr["tp"]), "rr": tr["rr"], "outcome": tr["outcome"],
                                   "R": tr.get("R"), "fill_t": iso(tr.get("fill_t")), "exit_t": iso(tr.get("exit_t")),
                                   "tp_name": tr.get("tp_name")})
        plans.append(item)
    e1 = next(p for p in plans if p["id"] == "E1")
    t2r = next(t for t in e1["trades"] if t["target"] == "T2R")
    word, side = ("buy", "low") if buy else ("sell", "high")
    rec = {"id": f"{sym}-M4H-{d[:4]}-{int(s['t'].timestamp())}", "symbol": sym, "setup": "M4H",
           "name": f"4H manipulation {word}", "dir": d, "poi_tf": "h4", "confirm_tf": "h4", "key_t": s["t"].isoformat(),
           "poi": {"kind": "4H manipulation candle", "tf": "h4", "low": f(l), "high": f(h), "formed": s["t"].isoformat(),
                   "candle": s["t"].isoformat()},
           "timeline": [], "stage": None, "stage_t": None, "entry": e1["entry"], "stop": e1["stop"], "tp1": t2r["tp"],
           "tp1_name": "2R", "tp2": None, "tp3": None, "rr": 2.0, "result_r": t2r.get("R"), "grade": "A",
           "grade_note": "Unproven: no version of this setup made money in the 13-month backtest on 46 markets"}
    rec["checklist"] = [
        {"name": "Bias (D1 structure)", "ok": s["bias"], "detail": s["d1_state"] or "undefined"},
        {"name": "Momentum (H4 structure)", "ok": s["momentum"], "detail": s["h4_state"] or "undefined"},
        {"name": f"Manipulation: took the previous 4H {side}", "ok": True,
         "detail": f"{f(s['P'][1] if not buy else s['P'][2])} → {f(h if not buy else l)}"},
        {"name": "Closed beyond the previous candle's body", "ok": True,
         "detail": f"close {f(c)} vs body {f(min(s['P'][0], s['P'][3]) if not buy else max(s['P'][0], s['P'][3]))}"},
        {"name": f"Session of the {side}", "ok": True, "detail": f"{s['session']} ({ny(s['ext_t'])} NY)"},
        {"name": f"{'Low' if buy else 'High'} of the day so far", "ok": bool(s["hod_so_far"]),
         "detail": "held to 17:00 NY" if s["hod_held"] else ("taken later that day" if s["hod_held"] is False else "day not over")},
    ]
    rec["score"] = sum(1 for x in rec["checklist"] if x["ok"])
    rec["m4h"] = {"C": [f(v) for v in s["C"]], "P": [f(v) for v in s["P"]], "conf_t": iso(s["conf_t"]),
                  "session": s["session"], "ext_t": iso(s["ext_t"]), "bias": s["bias"], "momentum": s["momentum"],
                  "d1_state": s["d1_state"], "h4_state": s["h4_state"], "hod_so_far": s["hod_so_far"],
                  "hod_held": s["hod_held"], "range_atr": s["range_atr"], "class": M4H_SYMBOLS[sym]["class"],
                  "digits": dg, "alert": alert_ok(s), "rules": m4h.RULES_VERSION, "plans": plans}
    levels = " · ".join(f"{p['id']} {p['entry']}" for p in plans)
    stage(rec, "armed", s["conf_t"], f"4H candle took the previous {side} and closed beyond its body ({s['session']}). "
          f"{word.capitalize()} entries: {levels}; stop {e1['stop']}", e1["entry"])
    ends = [t["exit_t"] for p in plans for t in p["trades"] if t["exit_t"]]
    if not any(t["outcome"] in OPEN for p in plans for t in p["trades"]):
        rec["stage"], rec["stage_t"] = "done", max(ends) if ends else iso(s["conf_t"])
    return rec


def board_row(sym, bars):
    """One market's line on the /m4h board: price, D1/H4 structure and the last closed 4H candle."""
    cfg = M4H_SYMBOLS[sym]
    F = m4h.frame(bars["d1"], bars["h4"], bars["m30"][-48:], m4h.DEFAULTS["swing_n"])
    h4 = bars["h4"]
    last = h4[-1]
    jd = index_at(bars["d1"], last.end)
    return {"symbol": sym, "class": cfg["class"], "digits": cfg["digits"], "price": bars["m30"][-1].c,
            "price_t": iso(bars["m30"][-1].end), "d1": F.d1_state[jd] if jd >= 0 else None, "h4": F.h4_state[-1],
            "last_h4": iso(last.t), "next_close": iso(last.end + timedelta(hours=4))}


def live(bars_by_sym, now, lookback_days):
    """Setup records of the last `lookback_days` and the board, for every M4H market with enough candles."""
    recs, board = [], []
    for sym, bars in bars_by_sym.items():
        recs += [to_record(sym, s) for s in m4h.run(bars, M4H_SYMBOLS[sym], since=now - timedelta(days=lookback_days))]
        board.append(board_row(sym, bars))
    return recs, board
