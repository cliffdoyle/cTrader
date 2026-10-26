# Chart add-ons: ICT Scanner Levels (cTrader + MT5)

Draw the scanner's levels on your own charts without the cTrader MCP:
- **Asia and London boxes**: light shading with "ASIA H …" above and "L … · O … → C …" below; short thick ticks on the left
  and right edges mark the session's opening and closing price. A dotted line runs right from each high/low until price
  takes it ("swept 07:50"). *Session times*: **KillZones** (default) = ICT kill zones, Asia 20:00–00:00 and London
  02:00–05:00 NY, with 00:00–02:00 left empty on purpose; **Continuous** = Asia 20:00–02:00 runs straight into London.
- **90-minute boxes** (07:00 → 14:30 NY), one colour per window, with high, low, open and close printed (and open/close
  ticks). Every other window's labels sit one text row further out, so neighbouring boxes' labels never overlap at any zoom.
- **PXH / PXL / EQ** of the previous week, day and 90-minute cycle; labels sit in the empty space right of the candles.
- **SMT card** (dark box, top left by default; *SMT card position* moves it): the verdict, the bigger SMT (direction) and the 5m/15m SMT (timing) with each index's
  HH/LH (or ✓/✗ took the level), and which index to trade. On the chart, one line joins this index's two swing points.

Analysis stays on the website; the chart only shows levels and the SMT read. Both add-ons download the scanner's
read-only feed (`https://scanner.zuristay.com/api/feed/<symbol>`, built on the server from the cTrader Open API data)
every 60 seconds and redraw. They run on your PC inside cTrader / MT5 and are separate from the scanner (which runs 24/7
on the server). They work with **any broker** that offers these symbols (Pepperstone not required): the data
comes from the scanner, symbol names are mapped and prices are aligned to your broker. They **only draw**: there is no trading code, and they only touch their own `ICTS…` objects. Use **one
copy per chart**.

You need the **feed key** (read-only, not the dashboard password): it is in `%LOCALAPPDATA%\ictscan\feed-key.txt` on the
PC and in the server's `deploy/.env` as `ICTSCAN_FEED_TOKEN`.

## cTrader — indicator
1. Double-click `ctrader/ICT Scanner Levels.algo` (or copy it to `Documents\cAlgo\Sources\Indicators`) and restart cTrader.
   To build from source instead: Automate → Indicators → New, paste `ctrader/ICTScannerLevels/ICTScannerLevels.cs`, Build.
2. On a NAS100 / US30 / US500 chart (M5 or M15 shows the boxes best): Indicators → **ICT Scanner Levels**.
3. Paste the feed key into **Feed key** → OK. The bottom-left status shows "ICT Scanner · NAS100 · 50 objects · 14:20 UTC".

## MT5 — expert (draws only)
MT5 allows web requests only in Expert Advisors, so this is an EA — it contains no order functions. It works with the
**Algo Trading** button off (that button only blocks trading), so you can leave it off.
1. Open MT5 and log in to any broker account (demo is fine). Open a NAS100/US30/US500 chart (names differ per broker,
   e.g. USTEC, US30, US500/SPX500).
2. File → Open Data Folder → `MQL5\Experts` → copy `mt5/ICT Scanner Levels.ex5` there → in the Navigator, right-click
   Expert Advisors → Refresh.
3. Tools → Options → Expert Advisors → tick **Allow WebRequest for listed URL** → add `https://scanner.zuristay.com` → OK.
4. Drag **ICT Scanner Levels** from the Navigator onto the chart → Inputs → paste the feed key into **Feed key** → OK.
   (One EA per chart in MT5; use another chart for another EA.)

## Settings (both)
| Setting | Default | Meaning |
|---|---|---|
| Scanner symbol | blank | blank = the chart's symbol; broker names (USTEC, US30.cash, SPX500m…) are mapped automatically |
| Refresh every | 60 s | how often the feed is downloaded |
| Asia + London boxes / 90-minute boxes / PXH-PXL / Session levels / SMT | on / on / on / off / on | what to draw |
| Session times | KillZones | KillZones (gap 00:00–02:00) or Continuous (Asia until 02:00) |
| Cycles | week,day,90m | which PXH/PXL cycles (month,week,day,90m) |
| SMT card position | TopLeft | corner for the SMT card (cTrader) |
| Align prices to this broker | on | shifts levels by the difference between your broker's and Pepperstone's price on the last 5-min bar (0 on Pepperstone) |

## Build
- cTrader: `dotnet build ICTScannerLevels.csproj -c Release` (needs the .NET 8 SDK; the `cTrader.Automate` package produces the
  `.algo` and copies it into `Documents\cAlgo\Sources\Indicators`, named after the build folder's parent — rename it).
- MT5: `MetaEditor64.exe /compile:"ICT Scanner Levels.mq5" /log:build.log`.

Feed format: see `scanner/ictscan/feed.py`.
