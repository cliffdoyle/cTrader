# Chart add-ons: ICT Scanner Levels (cTrader + MT5 + TradingView)

Draw the scanner's levels on your own charts without the cTrader MCP:
- **Session boxes** (Asia, London and optionally New York 09:30–11:30, for the last 1–5 trading days): light shading with
  a bold "ASIA H …" above and "L … · O … → C …" below; on the latest day short thick ticks on the left
  and right edges mark the session's opening and closing price. A dotted line runs right from each high/low until price
  takes it ("swept 07:50"). *Session times*: **KillZones** (default) = ICT kill zones, Asia 20:00–00:00 and London
  02:00–05:00 NY, with 00:00–02:00 left empty on purpose; **Continuous** = Asia 20:00–02:00 runs straight into London.
- **90-minute boxes** (07:00 → 14:30 NY), one colour per window, with high, low, open and close printed (and open/close
  ticks). Every other window's labels sit one text row further out, so neighbouring boxes' labels never overlap at any zoom.
- **PXH / PXL / EQ** of the previous month, week, day and 90-minute cycle (each on/off); labels sit in the empty space
  right of the candles.
- **SMT card** (dark box, top left by default; *SMT card position* moves it): the verdict, the bigger SMT (direction) and the 5m/15m SMT (timing) with each index's
  HH/LH (or ✓/✗ took the level), and which index to trade. On the chart, one line joins this index's two swing points.

Analysis stays on the website; the chart only shows levels and the SMT read. Both add-ons download the scanner's
read-only feed (`https://scanner.zuristay.com/api/feed/<symbol>`, built on the server from the cTrader Open API data)
every 60 seconds and redraw. They run on your PC inside cTrader / MT5 and are separate from the scanner (which runs 24/7
on the server). They work with **any broker** that offers these symbols (Pepperstone not required): the data
comes from the scanner, symbol names are mapped and prices are aligned to your broker. They **only draw**: there is no trading code, and they only touch their own `ICTS…` objects. Use **one
copy per chart**.

The feed is protected by a **feed key** (read-only, not the dashboard password) so strangers can't read it. It is in
`%LOCALAPPDATA%\ictscan\feed-key.txt` on the PC and in the server's `deploy/.env` as `ICTSCAN_FEED_TOKEN`. On this PC
you never have to paste it: leave **Feed key** blank.
- cTrader: the indicator built on this PC has the key built in (the build reads `feed-key.txt` into `obj\`, never into
  the source). The `ctrader/ICT Scanner Levels.algo` in this repo is a keyless build: with it, paste the key once.
- MT5: when **Feed key** is blank the EA reads `ictscan-feed-key.txt` from MetaTrader's shared folder
  `%APPDATA%\MetaQuotes\Terminal\Common\Files` (one line, the key). On another PC, put that file there or paste the key.

## cTrader — indicator
1. Build it on this PC (see Build) or double-click `ctrader/ICT Scanner Levels.algo`, then restart cTrader.
   To build from source instead: Automate → Indicators → New, paste `ctrader/ICTScannerLevels/ICTScannerLevels.cs`, Build.
2. On a NAS100 / US30 / US500 chart (M5 or M15 shows the boxes best): Indicators → **ICT Scanner Levels** → Add to Chart.
   The bottom-left status shows "ICT Scanner · NAS100 · 50 objects · 14:20 UTC".

## MT5 — expert (draws only)
MT5 allows web requests only in Expert Advisors, so this is an EA — it contains no order functions. It works with the
**Algo Trading** button off (that button only blocks trading), so you can leave it off.
1. Open MT5 and log in to any broker account (demo is fine). Open a NAS100/US30/US500 chart (names differ per broker,
   e.g. USTEC, US30, US500/SPX500).
2. File → Open Data Folder → `MQL5\Experts` → copy `mt5/ICT Scanner Levels.ex5` there → in the Navigator, right-click
   Expert Advisors → Refresh.
3. Tools → Options → Expert Advisors → tick **Allow WebRequest for listed URL** → add `https://scanner.zuristay.com` → OK.
4. Drag **ICT Scanner Levels** from the Navigator onto the chart → OK (Feed key blank uses the shared key file).
   (One EA per chart in MT5; use another chart for another EA.)

## TradingView — Pine Script indicator
Pine Script cannot download data from a server, so the TradingView version does not read the scanner feed: it rebuilds
the same drawings from TradingView's own candles with the scanner's rules (sessions, 90-minute boxes, PXH/EQ/PXL, key
levels and the SMT panel). No feed key and no scanner connection are needed; it works on the web, desktop and phone apps.
1. Open a chart of **PEPPERSTONE:NAS100** (or PEPPERSTONE:US500 / PEPPERSTONE:US30 — the Pepperstone feed gives the same
   prices as the scanner; other feeds work but differ by a few points).
2. Pine Editor (bottom panel) → open the menu next to the script name → **Create new → Indicator** → select all → paste the
   contents of `tradingview/ICT Scanner Levels.pine` → **Save** (name it "ICT Scanner Levels") → **Add to chart**.
   A free TradingView account is enough (saving needs to be logged in).
3. Settings (gear on the indicator's name): Sessions, Levels and cycles, SMT and Look groups mirror the cTrader settings;
   **SMT compare** picks the indices and the three symbol fields choose the feed the SMT compares (default Pepperstone).

Differences from the cTrader / MT5 add-ons:
- Everything is computed by TradingView, so it keeps working when the scanner is down, but the values come from
  TradingView's candles (PEPPERSTONE matches; another feed shifts the prices slightly).
- Session boxes and the session high/low lines use the chart's candles: exact up to M30, on H1 the New York box covers
  09:00–12:00. Day / week / month levels always come from H1 data, the SMT from 5m / 15m / 1H data of the three indices.
- The SMT follows `engine/smt.py` (cycle SMT on the 90-min / day / week cycles + swing SMT on 5m / 15m / 1H with the same
  ±2-bar alignment, 0.1 × ATR equal-high tolerance and watch / confirmed / cancelled status). The "sell the weaker / buy
  the stronger" line names the index from the 5m/15m trigger when there is one, and adds the higher-timeframe SMT's index
  in brackets when they differ.
- **Cycle SMT markers** ("Mark cycle SMT on the chart", on by default): the moment one index trades through the previous
  90-min / day / week high (low) while another has not, the level is highlighted (dashed, red for highs / green for lows)
  and a filled label appears — on the candle that took it ("SMT ▼ Day PXH taken · US500 did not") when this chart's index
  took it, or on this index's own high/low ("SMT ▼ Day PXH not taken · NAS100 took it") when it did not. It is live from
  the first tick (the forming candle counts, unlike the scanner which waits for closed candles), so it disappears again
  if the lagging index also takes the level. 90-min markers show up to M30, day up to H4, week everywhere.
- **Session SMT** ("SMT at Asia / London / NY AM highs and lows", on by default): once a session has closed, its high and
  low are watched on every index for the rest of that trading day; when one index trades through, e.g., its Asia high and
  another has not, the same live marker appears ("SMT ▼ Asia high taken · US500 did not"), the panel can use it as the
  direction SMT (weight of a 90-min cycle) and an alert fires. Computed inside the existing 5m request per index, so it
  costs no extra data request. Markers show up to H1.
- **Alerts**: right-click the indicator → Add alert → condition "ICT Scanner Levels" → **Any alert() function call**. You
  get one alert per new SMT (cycle SMTs and 5m / 15m / 1H swing SMTs), with the indices that took / did not take the level.
- A text "row" is a fixed 0.3 × H1 ATR (Pine cannot read the zoom), so at extreme zoom levels neighbouring labels can touch.

Sharing: the script has no key or scanner address in it, so it can be given to anyone. Either send the `.pine` file (they
paste it as in step 2), or publish it from the Pine Editor (**Publish script** → **Private**, so it is not listed or
reviewed) and send the link; they open it and click **Add to favorites**, and later "Update" publications reach them
automatically.

## Settings (both)
| Setting | Default | Meaning |
|---|---|---|
| Scanner symbol | blank | blank = the chart's symbol; broker names (USTEC, US30.cash, SPX500m…) are mapped automatically |
| Refresh every | 60 s | how often the feed is downloaded |
| **Levels:** Previous month / week / day / 90-min cycle | off / on / on / on | each PXH / PXL / EQ set on its own — e.g. only month + week + day for a clean higher-timeframe map |
| Session levels (PDH, PWH, Asia H…) | off | today's key levels as lines (on H4 only PW/PD, on D1 only PW) |
| **Sessions:** Asia / London / New York box | on / on / off | each session box on its own (New York = 09:30–11:30 NY kill zone, green) |
| Session times | KillZones | KillZones (gap 00:00–02:00) or Continuous (Asia until 02:00) |
| Days of sessions | 3 | how many trading days of session boxes (1–5); the latest day is full colour with open/close ticks and "swept" lines, older days are quieter |
| 90-minute boxes (NY day) | on | the six 90-minute windows 07:00 → 16:00 NY |
| **SMT:** SMT card / SMT compare / position | on / AllThree / TopLeft | which indices the SMT compares: all three, or one pair — **Nasdaq_SP500** (NAS100 vs US500, no Dow), Nasdaq_Dow, SP500_Dow. A chart whose index is not in the pair shows "not in this comparison" |
| **Fit to timeframe** | on | draws only what reads well on the chart's timeframe (table below). Off = everything you switched on, on every timeframe |
| Align prices to this broker | on | shifts levels by the difference between your broker's and Pepperstone's price on the last 5-min bar (0 on Pepperstone) |

**Fit to timeframe** (what each timeframe shows, from the items you switched on):

| Chart timeframe | Session boxes | 90-min boxes / 90-min levels | Day levels | Week / month levels |
|---|---|---|---|---|
| M1 – M5, M10 – M30 | ✓ (full labels, ticks) | ✓ | ✓ | ✓ |
| H1 | ✓ (short labels, no ticks) | — | ✓ | ✓ |
| H2 – H4 | — | — | ✓ | ✓ |
| D1 and above | — | — | — | ✓ |

**Switching timeframe is instant:** each timeframe's drawing is stored (cTrader LocalStorage; in MT5 in the EA's memory) and
the other timeframes are downloaded in the background after the first refresh, so a switch redraws from storage at once
and refreshes a moment later. The 5-minute bars used for broker price alignment load in the background too.

## Build
- cTrader: `dotnet build ICTScannerLevels.csproj -c Release` (needs the .NET 8 SDK; the `cTrader.Automate` package produces the
  `.algo` and copies it into `Documents\cAlgo\Sources\Indicators`, named after the build folder's parent — rename it).
  That build embeds the feed key from `%LOCALAPPDATA%\ictscan\feed-key.txt`; add `-p:EmbedFeedKey=false` for the keyless
  copy that goes into this repo. Never commit a keyed `.algo`.
- MT5: `MetaEditor64.exe /compile:"ICT Scanner Levels.mq5" /log:build.log`.

Feed format: see `scanner/ictscan/feed.py`.
