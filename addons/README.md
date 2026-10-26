# Chart add-ons: ICT Scanner Levels (cTrader + MT5)

Draw the scanner's levels on your own charts without the cTrader MCP: 90-minute session boxes (translucent fill, one
colour per window), previous-cycle PXH / PXL / EQ, session levels and the SMT read. Both add-ons download the scanner's
read-only feed (`https://scanner.zuristay.com/api/feed/<symbol>`, built from the cTrader Open API data on the server)
every 60 seconds and redraw. They **only draw** — no trading code — and only touch objects named `ICTS_…`.

You need the **feed key** (read-only, not the dashboard password): it is in `%LOCALAPPDATA%\ictscan\feed-key.txt` on the
PC and in the server's `deploy/.env` as `ICTSCAN_FEED_TOKEN`.

## cTrader — indicator
1. Double-click `ctrader/ICT Scanner Levels.algo` (or copy it to `Documents\cAlgo\Sources\Indicators`) and restart cTrader.
   To build from source instead: Automate → Indicators → New, paste `ctrader/ICTScannerLevels/ICTScannerLevels.cs`, Build.
2. On a NAS100 / US30 / US500 chart (M5 or M15 shows the boxes best): Indicators → **ICT Scanner Levels**.
3. Paste the feed key into **Feed key** → OK. The bottom-left status shows "ICT Scanner · NAS100 · 50 objects · 14:20 UTC".

## MT5 — expert (draws only)
MT5 allows web requests only in Expert Advisors, so this is an EA — it contains no order functions.
1. File → Open Data Folder → `MQL5\Experts` → copy `mt5/ICT Scanner Levels.ex5` (or the `.mq5` and compile it in MetaEditor).
2. Tools → Options → Expert Advisors → tick **Allow WebRequest for listed URL** → add `https://scanner.zuristay.com`.
3. Drag **ICT Scanner Levels** onto the chart → Inputs → paste the feed key → OK. (One EA per chart in MT5.)

## Settings (both)
| Setting | Default | Meaning |
|---|---|---|
| Scanner symbol | blank | blank = the chart's symbol; broker names (USTEC, US30.cash, SPX500m…) are mapped automatically |
| Refresh every | 60 s | how often the feed is downloaded |
| 90-minute boxes / PXH-PXL / Session levels / SMT | on / on / off / on | what to draw |
| Cycles | week,day,90m | which PXH/PXL cycles (month,week,day,90m) |
| Align prices to this broker | on | shifts levels by the difference between your broker's and Pepperstone's price on the last 5-min bar (0 on Pepperstone) |

## Build
- cTrader: `dotnet build ICTScannerLevels.csproj -c Release` (needs the .NET 8 SDK; the `cTrader.Automate` package produces the
  `.algo` and copies it into `Documents\cAlgo\Sources\Indicators`, named after the build folder's parent — rename it).
- MT5: `MetaEditor64.exe /compile:"ICT Scanner Levels.mq5" /log:build.log`.

Feed format: see `scanner/ictscan/feed.py`.
