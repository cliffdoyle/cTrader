"""ICT structure analysis per prompt.md definitions. Usage:
    python ict_analysis.py <bars.json> <out_dir>
bars.json: {"symbols": {SYM: {"details": {...}, "d1": [bars], "h4": [bars]}}} with UTC ISO bar-open timestamps.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
N_SWING = 2
DISPLACEMENT_MULT = 1.5
BODY_LOOKBACK = 20
ATR_LEN = 14
RECENT = 20
EQUAL_TOL_ATR = 0.1


def pip_size(symbol, details):
    if symbol in ("NAS100", "US30"):
        return 1.0, "points"
    if symbol.endswith("JPY") or symbol.startswith("XAU"):
        return 0.01, "pips"
    if details.get("category", "").startswith("FX"):
        return 0.0001, "pips"
    raise ValueError(f"No pip rule for {symbol}")


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def ny(ts):
    return parse(ts).astimezone(NY).strftime("%Y-%m-%d %H:%M %Z")


def trading_date(ts):
    # Broker D1 opens 17:00 NY; the trading day is the following calendar day.
    return (parse(ts).astimezone(NY) + timedelta(hours=7)).date()


def swings(bars):
    highs, lows = [], []
    for i in range(N_SWING, len(bars) - N_SWING):
        window = range(i - N_SWING, i + N_SWING + 1)
        if all(bars[i]["high"] > bars[k]["high"] for k in window if k != i):
            highs.append({"i": i, "price": bars[i]["high"], "confirmed_at": i + N_SWING})
        if all(bars[i]["low"] < bars[k]["low"] for k in window if k != i):
            lows.append({"i": i, "price": bars[i]["low"], "confirmed_at": i + N_SWING})
    return highs, lows


def trend_from(highs, lows):
    if len(highs) < 2 or len(lows) < 2:
        return "range"
    hh = highs[-1]["price"] > highs[-2]["price"]
    hl = lows[-1]["price"] > lows[-2]["price"]
    lh = highs[-1]["price"] < highs[-2]["price"]
    ll = lows[-1]["price"] < lows[-2]["price"]
    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "range"


def structure_breaks(bars, highs, lows):
    """Close-based breaks of the most recent confirmed swing. State flips on CHoCH."""
    events, state = [], None
    broken = set()
    for j in range(len(bars)):
        known_h = [s for s in highs if s["confirmed_at"] < j]
        known_l = [s for s in lows if s["confirmed_at"] < j]
        for side, known in (("up", known_h), ("down", known_l)):
            if not known:
                continue
            sw = known[-1]
            key = (side, sw["i"])
            if key in broken:
                continue
            c = bars[j]["close"]
            if (side == "up" and c > sw["price"]) or (side == "down" and c < sw["price"]):
                broken.add(key)
                new_dir = "bullish" if side == "up" else "bearish"
                ref = state or trend_from(known_h, known_l)
                if ref == new_dir:
                    kind = "BOS"
                elif ref == "range":
                    kind = "break (no prior trend)"
                else:
                    kind = "CHoCH"
                state = new_dir
                events.append({"j": j, "kind": kind, "dir": new_dir, "swing_i": sw["i"],
                               "level": sw["price"], "close": c})
    return events, state


def body(b):
    return abs(b["close"] - b["open"])


def fvgs(bars, pip):
    out = []
    for k in range(BODY_LOOKBACK + 1, len(bars)):
        c1, c2, c3 = bars[k - 2], bars[k - 1], bars[k]
        prior = bars[k - 1 - BODY_LOOKBACK:k - 1]
        avg = sum(body(b) for b in prior) / BODY_LOOKBACK
        if avg == 0 or body(c2) < DISPLACEMENT_MULT * avg:
            continue
        if c1["high"] < c3["low"]:
            d, lo, hi = "bullish", c1["high"], c3["low"]
        elif c1["low"] > c3["high"]:
            d, lo, hi = "bearish", c3["high"], c1["low"]
        else:
            continue
        status = "unfilled"
        for b in bars[k + 1:]:
            if d == "bullish":
                if b["low"] <= lo:
                    status = "filled"
                    break
                if b["low"] < hi:
                    status = "partly filled"
            else:
                if b["high"] >= hi:
                    status = "filled"
                    break
                if b["high"] > lo:
                    status = "partly filled"
        out.append({"i": k - 1, "dir": d, "low": lo, "high": hi, "size_pips": (hi - lo) / pip,
                    "body_ratio": body(c2) / avg, "status": status})
    return out


def atr(bars):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["high"], bars[i]["low"], bars[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    a = sum(trs[:ATR_LEN]) / ATR_LEN
    for tr in trs[ATR_LEN:]:
        a = (a * (ATR_LEN - 1) + tr) / ATR_LEN
    return a


def daily_levels(d1):
    days = [{"date": trading_date(b["timestamp"]), **b} for b in d1]
    weeks = {}
    for d in days:
        key = d["date"].isocalendar()[:2]
        w = weeks.setdefault(key, {"high": d["high"], "low": d["low"]})
        w["high"], w["low"] = max(w["high"], d["high"]), min(w["low"], d["low"])
    return days, weeks


def liquidity_levels_for(bar_ts, days, weeks):
    td = trading_date(bar_ts)
    prev_days = [d for d in days if d["date"] < td]
    prev_week_key = max((k for k in weeks if k < td.isocalendar()[:2]), default=None)
    lv = []
    if prev_days:
        lv += [("PDH", "high", prev_days[-1]["high"]), ("PDL", "low", prev_days[-1]["low"])]
    if prev_week_key:
        lv += [("PWH", "high", weeks[prev_week_key]["high"]), ("PWL", "low", weeks[prev_week_key]["low"])]
    return lv


def sweeps(h4, highs, lows, days, weeks, atr_v):
    out = []
    n = len(h4)
    for j in range(n - RECENT, n):
        b = h4[j]
        cands = []
        for s in (x for x in highs if x["confirmed_at"] < j):
            eq = any(abs(o["price"] - s["price"]) <= EQUAL_TOL_ATR * atr_v for o in highs if o is not s and o["confirmed_at"] < j)
            cands.append(("equal highs" if eq else "swing high", "high", s["price"], s["i"]))
        for s in (x for x in lows if x["confirmed_at"] < j):
            eq = any(abs(o["price"] - s["price"]) <= EQUAL_TOL_ATR * atr_v for o in lows if o is not s and o["confirmed_at"] < j)
            cands.append(("equal lows" if eq else "swing low", "low", s["price"], s["i"]))
        td = trading_date(b["timestamp"])
        day_start = next((k for k in range(n) if trading_date(h4[k]["timestamp"]) == td), j)
        for name, side, price in liquidity_levels_for(b["timestamp"], days, weeks):
            cands.append((name, side, price, day_start - 1))
        for name, side, price, since in cands:
            intact = all((h4[k]["high"] <= price) if side == "high" else (h4[k]["low"] >= price)
                         for k in range(max(since + 1, 0), j))
            if not intact:
                continue
            if side == "high" and b["high"] > price and b["close"] < price:
                out.append({"j": j, "type": "buy-side", "level_name": name, "level": price, "extreme": b["high"], "close": b["close"]})
            if side == "low" and b["low"] < price and b["close"] > price:
                out.append({"j": j, "type": "sell-side", "level_name": name, "level": price, "extreme": b["low"], "close": b["close"]})
    return out


def dealing_range(h4, highs, lows):
    sh, sl = highs[-1], lows[-1]
    hi = max([sh["price"]] + [b["high"] for b in h4[sh["i"]:]])
    lo = min([sl["price"]] + [b["low"] for b in h4[sl["i"]:]])
    return {"high": hi, "low": lo, "mid": (hi + lo) / 2, "swing_high_i": sh["i"], "swing_low_i": sl["i"]}


def analyse(sym, data):
    pip, unit = pip_size(sym, data["details"])
    d1, h4 = data["d1"], data["h4"]
    res = {"symbol": sym, "pip": pip, "unit": unit, "price": h4[-1]["close"], "price_time": h4[-1]["timestamp"]}
    for tf, bars in (("D1", d1), ("H4", h4)):
        hs, ls = swings(bars)
        ev, state = structure_breaks(bars, hs, ls)
        res[tf] = {"bars": bars, "highs": hs, "lows": ls, "trend": trend_from(hs, ls),
                   "structure_state": state, "events": ev, "fvgs": fvgs(bars, pip)}
    a = atr(h4)
    res["atr_h4"] = a
    days, weeks = daily_levels(d1)
    res["sweeps"] = sweeps(h4, res["H4"]["highs"], res["H4"]["lows"], days, weeks, a)
    res["range"] = dealing_range(h4, res["H4"]["highs"], res["H4"]["lows"])
    return res


def score(r):
    d1t, h4t = r["D1"]["trend"], r["H4"]["trend"]
    bias = h4t if h4t != "range" else d1t
    price, a, n = r["price"], r["atr_h4"], len(r["H4"]["bars"])
    pts, reasons = 0, []

    ok = d1t == h4t and d1t != "range"
    pts += ok
    reasons.append(("1 Bias agree", ok, f"D1 {d1t}, H4 {h4t}"))

    if bias == "range":
        for name in ("2 Premium/discount", "3 FVG within 1 ATR", "4 Liquidity sweep", "5 Recent BOS/CHoCH"):
            reasons.append((name, False, "no directional bias (D1 and H4 both range)"))
        r["bias"] = bias
        return pts, reasons

    rg = r["range"]
    pct = (price - rg["low"]) / (rg["high"] - rg["low"]) * 100
    ok = (bias == "bullish" and price < rg["mid"]) or (bias == "bearish" and price > rg["mid"])
    pts += ok
    zone = "discount" if price < rg["mid"] else "premium"
    reasons.append(("2 Premium/discount", ok, f"price {price} at {pct:.0f}% of H4 range {rg['low']}-{rg['high']} ({zone}); bias {bias}"))

    want = bias
    best = None
    for tf in ("H4", "D1"):
        for f in r[tf]["fvgs"]:
            if f["dir"] != want or f["status"] == "filled":
                continue
            dist = 0.0 if f["low"] <= price <= f["high"] else min(abs(price - f["low"]), abs(price - f["high"]))
            cand = {**f, "tf": tf, "dist": dist}
            if best is None or dist < best["dist"]:
                best = cand
            if f["status"] == "unfilled" and (r.get("nearest_unfilled") is None or dist < r["nearest_unfilled"]["dist"]):
                r["nearest_unfilled"] = cand
    r["nearest_open_fvg"] = best
    nu = r.get("nearest_unfilled")
    ok = nu is not None and nu["dist"] <= a
    pts += ok
    if nu:
        txt = f"nearest unfilled {want} FVG {nu['tf']} {nu['low']}-{nu['high']} is {nu['dist'] / r['pip']:.1f} {r['unit']} away; ATR {a / r['pip']:.1f}"
    else:
        txt = f"no unfilled {want} FVG on H4/D1"
    if best and best is not nu and best["dist"] <= a and not ok:
        txt += f" (a PARTLY filled {best['tf']} FVG is {best['dist'] / r['pip']:.1f} away)"
    reasons.append(("3 FVG within 1 ATR", ok, txt))

    need = "sell-side" if bias == "bullish" else "buy-side"
    sw = [s for s in r["sweeps"] if s["type"] == need]
    moved = [s for s in sw if (price > s["close"] if bias == "bullish" else price < s["close"])]
    ok = bool(moved)
    pts += ok
    if moved:
        s = moved[-1]
        txt = f"{need} sweep of {s['level_name']} {s['level']} at H4 #{s['j']}, price then moved {bias}"
    elif sw:
        txt = f"{need} sweep at H4 #{sw[-1]['j']} but no follow-through yet"
    else:
        txt = f"no {need} sweep in last {RECENT} H4 candles"
    reasons.append(("4 Liquidity sweep", ok, txt))

    ev = [e for e in r["H4"]["events"] if e["j"] >= n - RECENT and e["dir"] == bias and e["kind"] in ("BOS", "CHoCH")]
    ok = bool(ev)
    pts += ok
    txt = f"{ev[-1]['kind']} {bias} at H4 #{ev[-1]['j']}" if ev else f"no {bias} BOS/CHoCH in last {RECENT} H4 candles"
    reasons.append(("5 Recent BOS/CHoCH", ok, txt))
    r["bias"] = bias
    return pts, reasons


def fmt_bar_time(tf, bars, i):
    t = bars[i]["timestamp"]
    return f"{trading_date(t)} (D1)" if tf == "D1" else ny(t)


def report(r, pts, reasons):
    L = []
    s, p = r["symbol"], r["pip"]
    L.append(f"=== {s} | price {r['price']} (last H4 close, bar {ny(r['price_time'])}) | ATR14 H4 {r['atr_h4'] / p:.1f} {r['unit']} ===")
    for tf in ("D1", "H4"):
        t = r[tf]
        b = t["bars"]
        L.append(f"[{tf}] trend={t['trend']} structure_state={t['structure_state']}")
        for lab, arr in (("SH", t["highs"][-3:]), ("SL", t["lows"][-3:])):
            L.append(f"  last {lab}: " + "; ".join(f"#{x['i']} {fmt_bar_time(tf, b, x['i'])} {x['price']}" for x in arr))
        for e in t["events"][-3:]:
            L.append(f"  {e['kind']} {e['dir']}: #{e['j']} {fmt_bar_time(tf, b, e['j'])} close {e['close']} beyond swing #{e['swing_i']} {e['level']}")
        open_f = [f for f in t["fvgs"] if f["status"] != "filled"]
        L.append(f"  FVGs: {len(t['fvgs'])} total, {sum(f['status'] == 'unfilled' for f in t['fvgs'])} unfilled, {sum(f['status'] == 'partly filled' for f in t['fvgs'])} partly")
        for f in open_f[-4:]:
            L.append(f"    {f['dir']} {f['status']}: c2 #{f['i']} {fmt_bar_time(tf, b, f['i'])} zone {f['low']}-{f['high']} size {f['size_pips']:.1f} {r['unit']} body x{f['body_ratio']:.1f}")
    rg = r["range"]
    L.append(f"[H4 dealing range] {rg['low']} - {rg['high']} mid {rg['mid']:.5g} (swing high #{rg['swing_high_i']}, swing low #{rg['swing_low_i']})")
    for sw in r["sweeps"][-4:]:
        L.append(f"  sweep {sw['type']} {sw['level_name']} {sw['level']}: H4 #{sw['j']} {ny(r['H4']['bars'][sw['j']]['timestamp'])} extreme {sw['extreme']} close {sw['close']}")
    L.append(f"SCORE {pts}/5 (bias used: {r['bias']})")
    for name, ok, txt in reasons:
        L.append(f"  {'+' if ok else '-'} {name}: {txt}")
    return "\n".join(L)


def main():
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    data = json.loads(src.read_text(encoding="utf-8-sig"))["symbols"]
    rows, text = [], []
    for sym in ("NAS100", "US30", "EURUSD", "GBPUSD", "XAUUSD", "GBPJPY"):
        r = analyse(sym, data[sym])
        pts, reasons = score(r)
        text.append(report(r, pts, reasons))
        rows.append({"symbol": sym, "d1": r["D1"]["trend"], "h4": r["H4"]["trend"], "bias": r["bias"], "score": pts,
                     "reasons": [[n, ok, t] for n, ok, t in reasons]})
    (out / "stage1_report.txt").write_text("\n\n".join(text), encoding="utf-8")
    (out / "stage1_scores.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    for row in sorted(rows, key=lambda x: -x["score"]):
        print(f"{row['symbol']:7} D1={row['d1']:8} H4={row['h4']:8} bias={row['bias']:8} score={row['score']}")


if __name__ == "__main__":
    main()
