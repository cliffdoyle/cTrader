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
- 2026-11-25: spec drafted; nothing built.
