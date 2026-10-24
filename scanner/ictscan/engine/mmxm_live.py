"""Turns MMXM detections (engine/mmxm.py) into scanner setup records (same shape as engine/setups.py) for the dashboard.
Built exactly as specified by the user (both timeframes, stop below the MSS FVG, adds shown); the backtest's wider stop
(below the reversal low) is reported as information only."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from ..config import SYMBOLS
from . import mmxm
from .setups import ny, stage

NY = ZoneInfo("America/New_York")
KILLZONES = ((2, 0, 5, 0), (8, 30, 11, 0))
NAMES = {"buy": "Market maker buy model", "sell": "Market maker sell model"}
FINAL = {"target": ("target", "Terminus (original consolidation) reached"),
         "stop": ("stopped", "Stop hit"),
         "trail": ("stopped", "Trailing stop hit after the adds"),
         "time": ("expired", "Time stop: closed at market"),
         "expired": ("expired", "Limit not filled in time"),
         "missed": ("missed", "Terminus reached before the limit filled"),
         "invalidated": ("invalidated", "A candle closed beyond the reversal extreme before the fill")}


def in_killzone(t):
    n = t.astimezone(NY)
    m = n.hour * 60 + n.minute
    return any(h0 * 60 + m0 <= m < h1 * 60 + m1 for h0, m0, h1, m1 in KILLZONES)


def to_record(r):
    sym, buy = r["symbol"], r["dir"] == "buy"
    dg = SYMBOLS[sym]["digits"]
    f = lambda v: None if v is None else round(v, dg)  # noqa: E731
    word, d = ("buy", "bullish") if buy else ("sell", "bearish")
    hi_lo, opp = ("high", "low") if buy else ("low", "high")
    mode, p = r["mode"], mmxm.MMXM_DEFAULTS[r["mode"]]
    arr_tf = r["array"].split()[0].lower()
    took = r.get("took") or {}
    smt_txt = ", ".join(f"{s} {'took' if v else 'held'} its curve {opp}" for s, v in took.items()) or "not available"
    s = {"id": f"{sym}-MMXM-{mode}-{d[:4]}-{int(r['conf_t'].timestamp())}", "symbol": sym, "setup": "MMXM",
         "name": f"{NAMES[r['dir']]} ({mode})", "dir": d, "poi_tf": arr_tf, "confirm_tf": p["exec"],
         "key_t": r["L_t"].isoformat(),
         "poi": {"kind": f"{r['array']} reversal level", "tf": arr_tf, "low": f(r["array_lo"]), "high": f(r["array_hi"]),
                 "formed": r["L_t"].isoformat(), "candle": r["L_t"].isoformat()},
         "timeline": [], "stage": None, "stage_t": None, "entry": f(r["entry"]), "stop": f(r["stop"]), "tp1": f(r["T"]),
         "tp1_name": "original consolidation (terminus)", "tp2": None, "tp3": None, "rr": round(r["rr"], 2),
         "result_r": None, "grade": "A", "grade_note": "MMXM model complete (as specified)", "d1_state": None,
         "reasons": [
             f"{'Sell' if buy else 'Buy'} side of the curve from the original consolidation {f(r['T'])} "
             f"({ny(r['T_t'])}) into the {r['array']} {f(r['array_lo'])}–{f(r['array_hi'])}.",
             f"Intermediate-term {hi_lo} {f(r['H'])} ({ny(r['H_t'])}) was made by a retracement into a "
             f"{'bearish' if buy else 'bullish'} FVG.",
             f"Reversal {opp} {f(r['L'])} ({ny(r['L_t'])}) traded into the {r['array']}; the {p['exec'].upper()} market "
             f"structure shift broke {f(r['H'])} with a {d} FVG {f(r['fvg'][0])}–{f(r['fvg'][1])}.",
             f"SMT: {smt_txt}.",
             f"Plan (as specified): {word} limit {f(r['entry'])} (MSS FVG {'upper' if buy else 'lower'} edge), stop "
             f"{f(r['stop'])}, target {f(r['T'])} ({r['rr']:.1f}R). Adds on re-accumulation (max {mmxm.MMXM_DEFAULTS['max_adds']}, "
             f"{mmxm.MMXM_DEFAULTS['add_risk']:g}R each); all stops trail to each add's stop.",
             f"Backtest note: a stop beyond the reversal {opp} ({f(r['L_stop'])}) tested better than the specified stop "
             f"(swing version) — information only."]}
    s["checklist"] = [
        {"name": "Reversal level (higher-timeframe array)", "ok": True,
         "detail": f"{r['array']} {f(r['array_lo'])}–{f(r['array_hi'])}"},
        {"name": "Curve from the original consolidation", "ok": True, "detail": f"terminus {f(r['T'])}"},
        {"name": f"Intermediate {hi_lo} made in an FVG", "ok": True, "detail": f"{f(r['H'])}"},
        {"name": "Market structure shift with FVG", "ok": True, "detail": f"FVG {f(r['fvg'][0])}–{f(r['fvg'][1])}"},
        {"name": "SMT (NAS100 / US30 / US500)", "ok": bool(r["smt"]), "detail": smt_txt},
        {"name": "In a killzone (London 02–05, NY 08:30–11)", "ok": in_killzone(r["conf_t"]), "detail": ny(r["conf_t"]) + " NY"},
        {"name": "R:R to terminus ≥ 2", "ok": r["rr"] >= 2, "detail": f"{r['rr']:.1f}R"},
    ]
    s["score"] = sum(1 for c in s["checklist"] if c["ok"])
    stage(s, "limit", r["conf_t"], f"MSS confirmed: {word} limit at {f(r['entry'])}, stop {f(r['stop'])}, target {f(r['T'])}",
          f(r["entry"]))
    for kind, t, price, stp in r.get("events", []):
        if kind == "filled":
            stage(s, "triggered", t, f"Limit filled at {f(price)}", f(price))
        else:  # adds are notes: the setup stays 'triggered' until it closes
            note = (f"Re-accumulation: add {word} limit at {f(price)}, its stop {f(stp)}" if kind == "add_limit"
                    else f"Add filled at {f(price)} — move all stops to {f(stp)}")
            s["timeline"].append({"stage": kind, "t": t.isoformat(), "ny": ny(t), "note": note, "price": f(price)})
    if r["outcome"] in FINAL:
        name, note = FINAL[r["outcome"]]
        if "R" in r:
            s["result_r"] = round(r["R"], 2)
            note += f" ({r['R']:+.2f}R, {r.get('adds', 0)} add{'s' if r.get('adds', 0) != 1 else ''})"
        stage(s, name, r["exit_t"], note, f(r.get("exit")))
    return s


def live_setups(bars_by_sym, now, lookback_days=7):
    buffers = {s: SYMBOLS[s]["buffer"] for s in mmxm.INDICES}
    res, _ = mmxm.run(bars_by_sym, buffers=buffers, costs=buffers)
    since = now - timedelta(days=lookback_days)
    return [to_record(r) for r in res if r["conf_t"] >= since]
