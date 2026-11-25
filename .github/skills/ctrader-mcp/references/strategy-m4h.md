# 4H manipulation candle (M4H) — spec v0.1 (draft, 2026-11-25)

Source: Tom Camp, "Trading Made Simple — The Complete Course" Part 1 (QCSLSsLcncQ) and Part 2 (rypuR8wd7mU).
Transcripts in the session `files/video/`. Where Tom leaves judgement, the exact rule chosen here is marked ⚙
(configurable, measured in the backtest). Tom says the entry choice is discretionary ("there is no mechanical
edge"), so every entry is recorded and scored separately.

## User decisions (2026-11-25)
- Markets: ~45 liquid Pepperstone symbols (28 FX, XAU/XAG, 9 indices, oil, BTC/ETH); names verified on the Open API.
- Candles: Pepperstone's native H4 (open 17/21/01/05/09/13 NY), not Tom's CME 18:00 anchor.
- Entries: E1–E5 below. E6 (swing failure / three-candle swing point) is NOT built — Tom gives no exact rule.
- Targets: both 2R and nearest swing liquidity, measured separately.
- Rollout: backtest and live together; pop-ups marked "unproven" until a version passes the pass bar.

## Checklist ("four things")
Sell side shown; buy side is the mirror image.
1. **Bias** — D1 structure (`core.structure`, N=2 swings) bearish. ⚙ off = record only.
2. **Momentum** — H4 structure bearish. ⚙ off = record only.
3. **Manipulation** — the closed H4 candle C has `C.high > P.high` (P = the previous H4 candle).
4. **Engulf + close** — `C.close < min(P.open, P.close)` (beyond P's BODY, not its wick).

Context recorded on every setup (filters in the backtest, tags on the dashboard):
- Session in which C made its high (from H1 inside C): Asia 18:00–02:29, London 02:30–07:29, NY AM 07:30–11:29,
  Lunch 11:30–12:59, PM 13:00–16:45 NY. Tom prefers London, then NY.
- Whether C's high is the high of the trading day so far, and whether it held to the 17:00 NY close (Tom's core
  claim: "probably the highest point of the day").
- C range in ATR(H4), stop size in price and in $ at the broker minimum volume.

## Stop
C.high + spread buffer (`SYMBOLS[..]["buffer"]`, refreshed from live quotes). E4 uses its own stop (below).

## Entries
| Id | Entry | Order |
|---|---|---|
| E1 | open of the next H4 candle (= C.close) | market |
| E2 | 50 % of C (high → low, wicks) | limit |
| E3 | proximal edge of the unfilled H1 or M30 bearish FVG inside C nearest to 50 % ⚙ | limit |
| E4 | "duplicate": inside C's range, after C closes, an M30 (⚙ or H1) candle d with `d.high > prev.high` and `d.close < min(prev.open, prev.close)`; entry = d.close, stop = d.high + buffer | market at d close |
| E5 | OTE 70.5 % ⚙ of C (from C.low back up toward C.high) | limit |

Pending limits (E2, E3, E5) and the E4 search are cancelled when: the stop level trades first (invalidated), the
target trades before a fill (missed), or 3 ⚙ more H4 candles close without a fill (expired).

## Targets
- **T2R** — entry − 2 × risk.
- **TLIQ** — nearest untaken H4/D1 swing low (N=2) below entry; trade only if ≥ 2R, else "no target".
- E5 also scores **TLOW** = C.low (Tom: "target the 4-hour low").
- No management after entry (Tom: set and forget). No break-even.

## Results
Replayed on M30 bars; stop and target in the same bar = stop. Costs = typical spread per symbol. R per trade, win %,
PF, max losing run, per symbol / asset class / session / entry / target, split into design and validation halves.

## Pass bar (set before testing, same as 2026-10-17)
≥ +0.15 R/trade net, PF ≥ 1.3, max losing run < 15 R, profitable on most markets, 200+ trades, holds in both halves.

## Risk display
Tom: 1 % per trade, 2 % once the account is +4 %, back to 1 % below +4 %. The dashboard shows $ risk at the broker
minimum volume and the volume for 1 % of a configurable balance; it flags setups whose minimum risk exceeds 2 %.

## Status
- 2026-11-25: **Backtest — no edge.** Engine `scanner/ictscan/engine/m4h.py` (tests `tests/test_m4h.py`, 11), runner
  `python -m ictscan.m4h_backtest [--anchor broker|tom]` on `DATA_DIR/hist-m4h.db` (Open API, 46 markets: D1 ~4 y,
  H4 ~16 months, M30 21 Oct 2025 → 25 Nov 2026). 12,911 manipulation candles (broker H4). Every entry × target × filter
  (none, bias+momentum, +London/NY AM, London/NY AM) is negative in both halves; none passes. E1 × 2R: 12,847 trades,
  32.6 % wins, −0.104 R net (−0.022 R before costs), profitable on 6/46 markets. Bias+momentum makes it worse
  (E1 × 2R −0.143 R). Tom's own 18:00 NY candles (rebuilt from M30): same picture (E1 × 2R −0.095 R net, −0.013 R gross).
  Before costs every version is −0.08…+0.03 R/trade = a coin flip; costs add 0.08–0.25 R/trade.
  Tom's "high of the day" claim: a candle that took the previous H4 high, is the day's high so far AND closed below the
  previous body held to 17:00 NY 59.8 % (vs 35.5 % without the engulf; London 66.5 % vs 30.3 %) — real, but it does not
  turn into profit at 2R, the swing target or the candle's far end. Results kept in `DATA_DIR/m4h-backtest*.json`,
  reports in session `files/m4h-bt-v1.txt` / `m4h-bt-tom.txt`. Hand check NAS100 Tue 24 Nov 01:00 NY candle matched.
  Live scanner deployed 2026-11-25 (user: build it; a manipulation candle in ANY session is valid): `/m4h` on
  scanner.zuristay.com, runner step `Scanner.scan_m4h` once per M30 close (first run 86 s incl. 46-market backfill, then
  ~9 s; container 111 MiB of 384), ~33 candles/day of which ~14/day pop up (D1 bias + H4 momentum, any session,
  `M4H_ALERT`). Unrestricted by session the backtest is still negative in every session (E1 × 2R: Asia −0.07, London −0.10,
  NY AM −0.13, PM −0.25 R). Backup `/root/ictscan-code-backup-20261125-104343.tgz`; summary published (meta `backtest:m4h`).
- 2026-11-25: spec drafted.
