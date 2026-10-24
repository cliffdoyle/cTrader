# Strategy: ICT Market Maker Model (MMXM) — v1.0 (chosen by the user 2026-10-24)

Source: Frank369, "Advanced MMXM Lecture – ICT Concepts" (Feb 2023), summarised and made mechanical here. This replaces
S1–S4 as the user's main model. Where the lecture is discretionary, the mechanical choice is marked ⚙ (tunable in
`scanner/ictscan/engine/mmxm.py` → `MMXM_DEFAULTS`).

## User choices (2026-10-24)
- Markets: **NAS100, US30, US500** (the three US index CFDs, so SMT can be checked).
- Timeframes: **both** — intraday (reversal levels on H1/H4, execution on M5) and swing (levels on D1/H4, execution on M15).
- SMT: **required** (no setup without it). This reverses the 2026-10-17 decision to drop SMT, for this model only.
- Entries: **low-risk entry + re-accumulation adds**.  Target: **the original consolidation** (terminus).
- Order: **backtest first**, then build into the live scanner.

## The model (buy side; the sell model is the exact mirror)
1. **Original consolidation / terminus (T).** The highest high of the execution chart in the look-back window before the
   reversal ⚙ (intraday 24 h, swing 5 trading days). This is where price is expected to return to.
2. **Sell side of the curve.** Price falls from T into the reversal level; the fall must be at least ⚙ 2 × H1 ATR
   (swing: 2 × H4 ATR). Lower highs on the way down hold buy stops (liquidity for later).
3. **Reversal level.** A higher-timeframe bullish discount array that is still valid when price reaches it:
   a bullish FVG or bullish order block on the level timeframes (intraday H1/H4, swing D1/H4), not closed through.
   The reversal low L must trade into it.
4. **Intermediate-term high (H).** An execution-chart swing high (N=2) made on the sell side of the curve by a retracement
   that traded into a bearish FVG formed earlier on the curve (H ≥ that FVG's lower edge). H is below T.
5. **Market-structure-shift pattern (MSS).** After L, an execution candle closes above H **and** a bullish FVG forms in
   that up-leg whose upper edge is above H (the break is done by displacement, not a drift). It must happen within
   ⚙ 12 h intraday / 24 h swing of H. Confirmation = close of the later of the break candle and the FVG's third candle.
6. **SMT (required).** Between the curve before H and the reversal window (H → MSS), compare the three indices: each
   either takes out its own curve low or holds it. SMT exists when they disagree (at least one takes it, at least one
   holds). Sell model: the same with highs.
7. **Low-risk entry.** Buy limit at the upper edge of the MSS FVG (first retracement into it), valid for ⚙ 2 h intraday /
   12 h swing. Cancelled if a candle closes below L or price reaches T first (missed).
   Stop ⚙: below the MSS FVG's lower edge − spread buffer (lecture: "below the retracement low, which should hold").
8. **Re-accumulation adds** (max ⚙ 2). After entry, each new execution BOS up with a bullish FVG in its leg gives a buy
   limit at that FVG's upper edge; when filled, the add risks ⚙ 0.5 R to its own stop (FVG lower edge − buffer) and the
   stops of all open units move up to that level (aggressive trailing).
9. **Target.** All units exit at T. Stop and target in the same candle = stop. Time stop ⚙ 48 h intraday / 10 days swing.
10. **Costs.** Round-trip spread per unit: NAS100 1.5, US30 2.5, US500 0.6 points.

## What the lecture leaves to judgement (not modelled)
- "Time comes first": no session/killzone rule was given, so none is applied (killzones can be tested as a variant).
- Choice of the reversal array by eye; mitigation/breaker blocks; standard-deviation projections; trailing by feel.

## Status
- 2026-10-24: **Live as specified** (user's choice after the backtest): both timeframes, stop below the MSS FVG, SMT
  required, adds shown as alerts, target = terminus. Runs every scan on NAS100/US30/US500 (`engine/mmxm_live.py`,
  `runner.py`); setups are named "Market maker buy/sell model (intraday|swing)", id `SYM-MMXM-<mode>-<bull|bear>-<epoch>`,
  stages limit → triggered → (add_limit/add notes) → target/stopped/expired/missed/invalidated, grade A, 7-item checklist
  (killzone and R:R ≥ 2 are informational). MMXM pop-ups are on (`ALERT_SETUPS` in config.py) while S1–S4 stay muted.
  Each setup page shows the backtest record (meta `backtest:mmxm`, published with `python -m ictscan.mmxm_backtest
  --publish <summary.json>`) and the wider "beyond the reversal extreme" stop as information.
- 2026-10-24: spec written; data: Open API history for the three indices (droplet fetch, `%LOCALAPPDATA%\ictscan\hist-indices.db`,
  5 Oct 2025 → 23 Oct 2026); backtest `python -m ictscan.mmxm_backtest` (design A < 2026-05-01 ≤ validation B).
- **Backtest result (deduplicated, costs included):**
  | Version | Swing (D1/H4 → M15) | Intraday (H1/H4 → M5) |
  |---|---|---|
  | As specified (stop below MSS FVG) | 113 tr, +0.93R avg but only +0.20R without the top 3; wins capped at 5R −0.10R; B half negative | 428 tr, −0.25R |
  | **Stop below the reversal low** | 113 tr, +1.40R avg; without top 3 +0.19R; capped +0.23R (A +0.26, B +0.20); max DD −17R; 31% win | 428 tr, +0.07R; capped −0.25R |
  | Stop below reversal low, no adds | 113 tr, +0.38R; capped +0.27R (A +0.25, B +0.29); max DD −12.5R; 35% win | −0.15R |
  | Stop below reversal low, SMT off | 403 tr, +0.41R; capped −0.10R | 1420 tr, +0.12R; capped −0.19R |
  Findings: the lecture's stop (below the MSS FVG) is too tight; the swing version with the stop below the reversal low is
  the only one positive in both halves after capping outliers; SMT clearly helps the swing version; adds create the big
  trend-day wins (and most of the variance); intraday has no robust edge. Sells (+0.50R capped) beat buys (−0.13R) and
  US30 was negative — not used as filters (one year, would be curve-fitting). Sample: ~2 setups a week across the three
  indices, correlated (≈ 95 independent events), below the 200-trade research bar.
