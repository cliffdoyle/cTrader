# Scanner strategy spec — v0.2 (approved 2026-10-17; see §8 for build clarifications)

Top-down market structure from the Smart Risk videos ([strategy-top-down.md](./strategy-top-down.md), [strategy-3-setups.md](./strategy-3-setups.md)). No SMT, no session filter. All times America/New_York. All "close beyond" rules use candle **closes**; wicks never break structure.
Parameters marked ⚙ are starting values to be tuned by visual checks and the backtest, never by eye on live charts.

## 0. Scope
- Symbols: NAS100, US30, EURUSD, GBPUSD, XAUUSD, GBPJPY (cTrader demo account, Open API, view-only `accounts` scope).
- Timeframes: W1, D1 (levels only) · H4, H1 (structure + POIs) · M15, M5 (confirmation).
- Only **closed** candles are analysed. The engine runs after each M5 close.
- Units: pips = 0.0001 (EURUSD, GBPUSD), 0.01 (GBPJPY, XAUUSD per prompt.md); points for NAS100/US30.

## 1. Primitives (every timeframe)
**Swing point.** Swing high = high strictly above the highs of N candles each side; swing low mirrored. N = 2 ⚙. Confirmed only after the N right-hand candles close (no look-ahead).

**ATR.** Wilder ATR(14) of that timeframe; used for all tolerances.

**Structure state machine (BOS / CHoCH, protected levels).**
- Start state: `undefined` until the first close beyond a confirmed swing.
- Bullish break: a candle closes above the most recent confirmed, unbroken swing high.
  - In `bullish` state → **BOS** (continuation). In `bearish` state → **CHoCH** (reversal). From `undefined` → state becomes bullish, labelled "initial break".
  - **Protected low** = the lowest low between the broken swing high and the break candle (the low that launched the breakout).
- Bearish break is mirrored (protected high).
- While `bullish`, a close below the protected low is a **bearish CHoCH** even if it is not the latest swing low; this is the video's "stay bullish while price holds the low that caused the breakout".
- **Bias of a timeframe = its current state.** HH/HL swing counting is reported for information only.

**Dealing range.** Bullish: protected low → highest high since the last bullish break. Bearish mirrored. 50% = equilibrium; below 50% = discount, above = premium.

**Fair value gap.** Three candles: bullish if candle-1 high < candle-3 low; bearish if candle-1 low > candle-3 high. Valid only if candle 2 is a displacement candle: body ≥ 1.5 ⚙ × mean body of the 20 candles before it. Fill status from later candles: unfilled · partly filled (traded into) · filled (traded through the far edge). Near edge = the edge price reaches first on a retrace ("start of the gap").

**Order block (user choice: video-3 literal).** The **displacement candle (FVG candle 2)** whose move produced a BOS or CHoCH in the same direction within the same leg (between the leg origin and the break candle inclusive).
- Zone = that candle's full range (low → high) ⚙ (body-only variant tested in the backtest).
- Mitigated when price trades back into the zone; **invalidated** when a candle closes beyond the far edge (below the low of a bullish OB / above the high of a bearish OB).

**Liquidity levels.**
- Swing highs/lows that are still intact (no later candle has traded through them).
- Equal highs/lows: ≥ 2 intact swing highs (lows) within 0.1 ⚙ × ATR of each other; level = the most extreme of the group.
- Previous day high/low (PDH/PDL) and previous week high/low (PWH/PWL). Trading day starts 17:00 NY.
- Buy-side = above highs; sell-side = below lows.

**Liquidity sweep (user choice: wick OR two-candle).** For a buy-side level L:
- (a) wick sweep: a candle's high > L and its close < L; or
- (b) two-candle sweep: a candle closes > L and the **next** candle closes < L.
- Sell-side mirrored. The sweep extreme = the highest high (lowest low) of the sweeping candle(s).

## 2. Weekly/daily key levels (targets and "room" checks only)
1. Candidates: confirmed swing highs/lows on W1 and D1, using **candle bodies** (max(open,close) at swing highs, min(open,close) at swing lows).
2. Cluster candidates within 0.25 ⚙ × ATR of that timeframe. Zone = min → max of the cluster's body prices; treat it as an area.
3. Strength score 0–5 (one point each): **turning point** (a cluster swing began a move ≥ 3 ⚙ ATR); **multiple rejections** (≥ 3 touches); **flip** (cluster has both swing highs and swing lows); **drastic move away** (move of ≥ 3 ⚙ ATR within 10 ⚙ candles after a touch); **recent** (touched within the last 26 weeks / 60 days ⚙).
4. Keep zones with strength ≥ 2 ⚙ within 5 ⚙ × ATR(D1) of price. Weekly zones outrank daily zones.

## 3. Bias and quality filters
- **Directional bias = H4 state.** No trades when H4 is `undefined`.
- **A-grade alignment:** H1 state equals H4 state.
- **Premium/discount:** longs from discount, shorts from premium, of the POI timeframe's dealing range.
- **Room:** distance from entry to the nearest opposing W/D key level (or opposing intact H4 liquidity) ≥ 2 ⚙ × risk; otherwise flagged "limited room".
- Risk buffer on every stop = 1 ⚙ × typical spread of the symbol.

## 4. Setups
Each setup has stages: **Watch → Armed → Trigger → (Open) → Target / Stopped / Invalidated / Expired.** Only Armed and Trigger send push alerts; Watch goes into the morning digest.

### S1 — HTF order block + LTF CHoCH (top-down video)
- **POI:** an unmitigated OB in the H4-bias direction on **H4 (confirm on M15)** or **H1 (confirm on M5)**, lying in discount (longs) / premium (shorts) of that timeframe's dealing range.
- **Watch:** price within 1 ⚙ × ATR(POI TF) of the zone. **Armed:** price trades into the zone.
- **Trigger:** after the tap, the confirmation timeframe prints a **CHoCH** in the bias direction while the OB is not invalidated. No CHoCH → no trade.
- **Entry:** close of the CHoCH candle ⚙ (variant tested: limit at the near edge of the confirmation-TF FVG formed in the CHoCH leg).
- **Stop:** beyond the confirmation-TF leg extreme made inside/at the OB (the protected level of the new LTF structure) + buffer.
- **Targets:** TP1 nearest intact opposing liquidity on the POI timeframe; TP2 next daily key level; TP3 next weekly key level.
- **Invalidated:** POI-TF close beyond the OB far edge before trigger.

### S2 — Trend pullback into discount FVG + LTF sweep (3-setups #1)
- **Context:** H1 last break was a BOS/CHoCH in the H4-bias direction. Range = protected level → leg extreme.
- **POI:** an unfilled or partly filled H1 FVG in the bias direction whose midpoint is in discount (longs) / premium (shorts) of that range. Premium-side FVGs are ignored. If several qualify, the one price reaches first.
- **Armed:** price enters the FVG.
- **Trigger:** an M5 liquidity sweep in the bias direction (sell-side for longs) where the swept level lies **inside the H1 FVG**. Sweeps outside the FVG are ignored.
- **Entry:** close of the sweep's confirming candle. **Stop:** beyond the sweep extreme + buffer (variant tested: the H1 protected level, "safest").
- **Target:** TP1 = the H1 range extreme (recent swing high/low); TP2/TP3 as S1.
- **Invalidated:** H1 close beyond the protected level, or the FVG becomes filled, before trigger. A stopped trade may re-arm if the context still holds.

### S3 — H1 liquidity sweep + M5 BOS/FVG (3-setups #2)
- **Levels:** H1 intact swing highs/lows, equal highs/lows, PDH/PDL, PWH/PWL.
- **Armed:** an H1 sweep (wick or two-candle) of a level. Direction = against the sweep (buy-side sweep → short).
- **Trigger:** within 12 ⚙ H1 candles after the sweep, M5 prints a BOS/CHoCH in the reversal direction **and** an M5 FVG forms in that breaking leg (after the sweep).
- **Entry:** limit at the M5 FVG near edge ("start of the gap"). Expires if not filled within 24 ⚙ M5 candles or if TP1 trades first.
- **Stop:** beyond the H1 sweep extreme + buffer. **Target:** TP1 = the opposite H1 liquidity (other side of the range); TP2/TP3 as S1.
- Bias: allowed against H4 (the video does not filter), but "with H4 bias" is a checklist point and is reported separately in the backtest.

### S4 — Equal highs/lows + FVG (3-setups #3, single timeframe H1)
- **Watch (pending idea):** H4 bias bearish, H1 equal highs intact, and an unfilled bearish H1 FVG whose low sits above the equal-highs level and within 1 ⚙ × ATR(H1) of it. Bullish mirrored (FVG below equal lows, H4 bullish).
- **Trigger:** price reaches the FVG near edge (the limit level) — the run on equal highs delivers price into the gap.
- **Entry:** limit at the FVG near edge. **Stop:** beyond the FVG far edge + buffer. **Target:** nearest intact opposing H1 liquidity (the video does not state one); TP2/TP3 as S1.
- **Invalidated:** H1 close beyond the FVG far edge before the limit fills.

## 5. Checklist score (shown on every alert)
1. With H4 bias · 2. H1 aligned · 3. POI in correct premium/discount · 4. Liquidity swept before entry · 5. POI overlaps a W/D key zone · 6. Room ≥ 2R to the opposing key level · 7. R:R to TP1 ≥ 2.
Alerts show "x/7" plus the setup's backtest record (trades, win rate, average R). Nothing is called "high probability" without that record.

## 6. Alerts
- Push (Telegram): Armed, Trigger, Invalidated-after-trigger, data-feed down > 10 min.
- Morning digest 07:00 UTC+3 (00:00 NY in EDT, 23:00 NY in EST): overnight triggers and outcomes, active Watch/Armed list, bias grid.
- Every alert: symbol, setup, timeframes, NY time, entry, stop, TP1–TP3, R:R, checklist, chart link on the dashboard.
- De-duplication: one alert per setup instance per stage.

## 7. Backtest assumptions
- Same code path as live (bar-by-bar replay; nothing uses future candles).
- Costs: measured typical spread per symbol + commission (stated per run). Limit orders fill only if price trades through the limit by ≥ 1 tick.
- If stop and target fall in the same candle, the stop is assumed (conservative); drill down to M1 when available.
- 12 months of data; the most recent 20% kept as a one-time out-of-sample test. Report trades, win rate, average R, max drawdown, results by month, by setup and by symbol, and example trades with NY times for chart checking. Warn on small samples.

## 8. Implementation notes (v0.2, as built in `scanner/`, 2026-10-17)
- Approved by the user as v0.1; these clarifications were made while coding.
- CHoCH happens **only** on a close beyond the protected level (video 2). Breaking a minor swing inside the trend is not a CHoCH.
- Order block: when a breaking leg has several displacement candles, the **earliest** is used.
- S2 "FVG filled" invalidation = an H1 **close** through the FVG's far edge (a wick sweep through it is the trigger, not an invalidation).
- W/D zones: a cluster never spans more than 2 × tolerance (stops zones chaining into very wide areas).
- Event descriptions use the candle **open** time (as on cTrader charts); stage times are the deciding candle's close.
- Stop buffer per symbol is a fixed estimate in `config.py` until live spreads are measured.
