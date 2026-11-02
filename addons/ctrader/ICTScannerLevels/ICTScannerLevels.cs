// ICT Scanner Levels — draws the ICT scanner's levels on a cTrader chart, straight from the scanner's web feed.
//
// No cTrader MCP needed: the indicator downloads /api/feed/<symbol> from the scanner (data from the cTrader Open API) every
// minute and draws Asia / London / New York session boxes, 90-minute boxes, previous month / week / day / 90-min PXH / PXL /
// EQ and an SMT card — each switchable on its own. "Fit to timeframe" asks the scanner for what reads well on the chart's
// timeframe (sessions and 90-minute boxes vanish on H4 / D1, month and week levels stay); the drawing for every timeframe is
// kept in cTrader's LocalStorage, so switching timeframe redraws at once. It only draws: it never trades and only touches
// its own objects (names start with "ICTS").
//
// Install: double-click "ICT Scanner Levels.algo" (or Automate → Indicators → New, paste this file, Build), then add it
// to a chart. The feed key is built in when the indicator is compiled on a PC that has %LOCALAPPDATA%\ictscan\feed-key.txt
// (see the .csproj); otherwise paste it into "Feed key". Works with any cTrader broker (prices are aligned).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Indicators
{
    public enum SessionStyle { KillZones, Continuous }

    public enum PanelCorner { TopLeft, TopRight, BottomLeft, BottomRight }

    public enum SmtCompare { AllThree, Nasdaq_SP500, Nasdaq_Dow, SP500_Dow }

    [Indicator(IsOverlay = true, TimeZone = TimeZones.UTC, AccessRights = AccessRights.None, AutoRescale = false)]
    public partial class ICTScannerLevels : Indicator
    {
        [Parameter("Scanner URL", DefaultValue = "https://scanner.zuristay.com", Group = "Connection")]
        public string BaseUrl { get; set; }

        [Parameter("Feed key (blank = built-in)", DefaultValue = "", Group = "Connection")]
        public string FeedKey { get; set; }

        [Parameter("Scanner symbol (blank = this chart)", DefaultValue = "", Group = "Connection")]
        public string ScannerSymbol { get; set; }

        [Parameter("Refresh every (seconds)", DefaultValue = 60, MinValue = 15, Group = "Connection")]
        public int RefreshSeconds { get; set; }

        [Parameter("Previous month (M PXH / PXL)", DefaultValue = false, Group = "Levels")]
        public bool ShowMonth { get; set; }

        [Parameter("Previous week (W PXH / PXL)", DefaultValue = true, Group = "Levels")]
        public bool ShowWeek { get; set; }

        [Parameter("Previous day (D PXH / PXL)", DefaultValue = true, Group = "Levels")]
        public bool ShowDay { get; set; }

        [Parameter("Previous 90-min cycle", DefaultValue = true, Group = "Levels")]
        public bool Show90mLevels { get; set; }

        [Parameter("Session levels (PDH, PWH, Asia H…)", DefaultValue = false, Group = "Levels")]
        public bool ShowLevels { get; set; }

        [Parameter("Asia box", DefaultValue = true, Group = "Sessions")]
        public bool ShowAsia { get; set; }

        [Parameter("London box", DefaultValue = true, Group = "Sessions")]
        public bool ShowLondon { get; set; }

        [Parameter("New York box (09:30–11:30)", DefaultValue = false, Group = "Sessions")]
        public bool ShowNewYork { get; set; }

        [Parameter("Session times", DefaultValue = SessionStyle.KillZones, Group = "Sessions")]
        public SessionStyle Sessions { get; set; }

        [Parameter("Days of sessions", DefaultValue = 3, MinValue = 1, MaxValue = 5, Group = "Sessions")]
        public int SessionDays { get; set; }

        [Parameter("90-minute boxes (NY day)", DefaultValue = true, Group = "Sessions")]
        public bool ShowBoxes { get; set; }

        [Parameter("SMT card", DefaultValue = true, Group = "SMT")]
        public bool ShowSmt { get; set; }

        [Parameter("SMT compare", DefaultValue = SmtCompare.AllThree, Group = "SMT")]
        public SmtCompare SmtIndices { get; set; }

        [Parameter("SMT card position", DefaultValue = PanelCorner.TopLeft, Group = "SMT")]
        public PanelCorner Corner { get; set; }

        [Parameter("Fit to timeframe", DefaultValue = true, Group = "Look")]
        public bool FitTimeframe { get; set; }

        [Parameter("Label size", DefaultValue = 10, MinValue = 7, MaxValue = 20, Group = "Look")]
        public int FontSize { get; set; }

        [Parameter("Align prices to this broker", DefaultValue = true, Group = "Look")]
        public bool AutoAlign { get; set; }

        // Last feed per symbol/settings/timeframe class: in memory and in cTrader's LocalStorage (shared by every copy of this
        // indicator and kept across restarts), so switching timeframe redraws at once from the stored drawing.
        private static readonly Dictionary<string, string> Cache = new Dictionary<string, string>();
        private const int FreshSeconds = 600;

        private string Prefix = "ICTS_";
        private readonly HashSet<string> _drawn = new HashSet<string>();
        private Bars _m5;
        private bool _busy, _rendering, _prefetched;
        private string _lastBody, _cacheKey;
        private int _tier;
        private string _status = "ICT Scanner · connecting…";
        private Border _card;
        // labels pushed one text row further out: name → (price at the box edge, +1 above / -1 below); re-placed on zoom
        private readonly Dictionary<string, Tuple<double, int>> _rowLabels = new Dictionary<string, Tuple<double, int>>();

        protected override void Initialize()
        {
            // own object names per indicator copy, so two copies on one chart never fight over the same objects
            Prefix = "ICTS" + (InstanceId ?? "").Replace("-", "").PadRight(6, '0').Substring(0, 6) + "_";
            _tier = Tier();
            _cacheKey = CacheKey(_tier);
            Chart.ZoomChanged += _ => { EnsureDrawn(); PlaceRowLabels(); };
            Chart.ScrollChanged += _ => { EnsureDrawn(); PlaceRowLabels(); };
            Chart.SizeChanged += _ => PlaceRowLabels();
            Chart.ObjectsRemoved += _ => EnsureDrawn();
            var cached = ReadCache(_cacheKey);
            if (cached != null)
                Render(cached);
            // 5-minute bars only align prices; load them in the background so a timeframe switch never waits for them
            if (AutoAlign)
            {
                if (TimeFrame == TimeFrame.Minute5)
                    _m5 = Bars;
                else
                    MarketData.GetBarsAsync(TimeFrame.Minute5, bars =>
                    {
                        _m5 = bars;
                        if (_lastBody != null)
                            Render(_lastBody, true);
                    });
            }
            Timer.Start(TimeSpan.FromSeconds(Math.Max(15, RefreshSeconds)));
            Fetch();
        }

        public override void Calculate(int index)
        {
        }

        protected override void OnTimer()
        {
            Fetch();
        }

        protected override void OnDestroy()
        {
            _rendering = true;
            foreach (var name in _drawn)
                Chart.RemoveObject(name);
            Chart.RemoveObject(Prefix + "status");
            if (_card != null)
                Chart.RemoveControl(_card);
        }

        private string FeedSymbol()
        {
            return string.IsNullOrWhiteSpace(ScannerSymbol) ? SymbolName : ScannerSymbol.Trim();
        }

        private string SmtPair()
        {
            switch (SmtIndices)
            {
                case SmtCompare.Nasdaq_SP500: return "nq-es";
                case SmtCompare.Nasdaq_Dow: return "nq-ym";
                case SmtCompare.SP500_Dow: return "es-ym";
                default: return "all";
            }
        }

        private string Groups()
        {
            var g = new List<string>();
            if (SessionsCsv().Length > 0) g.Add("sessions");
            if (ShowBoxes) g.Add("boxes");
            if (KindsCsv().Length > 0) g.Add("cycles");
            if (ShowLevels) g.Add("levels");
            if (ShowSmt) g.Add("smt");
            return string.Join(",", g);
        }

        private string KindsCsv()
        {
            var k = new List<string>();
            if (ShowMonth) k.Add("month");
            if (ShowWeek) k.Add("week");
            if (ShowDay) k.Add("day");
            if (Show90mLevels) k.Add("90m");
            return string.Join(",", k);
        }

        private string SessionsCsv()
        {
            var s = new List<string>();
            if (ShowAsia) s.Add("asia");
            if (ShowLondon) s.Add("london");
            if (ShowNewYork) s.Add("ny");
            return string.Join(",", s);
        }

        // Timeframe class the scanner fits the drawing to: 1 ≤ M5 · 2 ≤ M30 · 3 = H1 · 4 ≤ H4 · 5 ≥ D1 (0 = draw everything).
        private int Tier()
        {
            if (!FitTimeframe)
                return 0;
            try
            {
                if (TimeFrame <= TimeFrame.Minute5) return 1;
                if (TimeFrame <= TimeFrame.Minute30) return 2;
                if (TimeFrame <= TimeFrame.Hour) return 3;
                if (TimeFrame <= TimeFrame.Hour4) return 4;
                return 5;
            }
            catch (Exception)
            {
                return 1;  // tick / renko / range charts
            }
        }

        private static string TierName(int tier)
        {
            return tier == 1 ? "≤M5" : tier == 2 ? "≤M30" : tier == 3 ? "H1" : tier == 4 ? "≤H4" : tier == 5 ? "≥D1" : "all";
        }

        private string CacheKey(int tier)
        {
            return string.Join("|", FeedSymbol(), Groups(), KindsCsv(), SessionsCsv(), SessionDays, Sessions, SmtPair(), tier);
        }

        private string Url(int tier, string key)
        {
            return string.Format(CultureInfo.InvariantCulture,
                "{0}/api/feed/{1}?groups={2}&kinds={3}&sn={4}&days={5}&sess={6}&smt={7}&tier={8}&token={9}",
                (BaseUrl ?? "").TrimEnd('/'), Uri.EscapeDataString(FeedSymbol()), Groups(), KindsCsv(), SessionsCsv(),
                SessionDays, Sessions == SessionStyle.Continuous ? "full" : "kz", SmtPair(), tier, Uri.EscapeDataString(key));
        }

        private static string StorageKey(string cacheKey)
        {
            return "feed_" + new string(cacheKey.Select(c => char.IsLetterOrDigit(c) ? c : '_').ToArray());
        }

        private string ReadCache(string cacheKey)
        {
            string body;
            if (Cache.TryGetValue(cacheKey, out body))
                return body;
            try
            {
                body = LocalStorage.GetString(StorageKey(cacheKey), LocalStorageScope.Type);
            }
            catch (Exception)
            {
                body = null;
            }
            if (!string.IsNullOrEmpty(body))
                Cache[cacheKey] = body;
            return string.IsNullOrEmpty(body) ? null : body;
        }

        private void WriteCache(string cacheKey, string body)
        {
            body = "#CACHED|" + DateTimeOffset.UtcNow.ToUnixTimeSeconds().ToString(CultureInfo.InvariantCulture) + "\n" + body;
            Cache[cacheKey] = body;
            try
            {
                LocalStorage.SetString(StorageKey(cacheKey), body, LocalStorageScope.Type);
            }
            catch (Exception e)
            {
                Print("ICT Scanner: could not keep the drawing for the next timeframe switch: {0}", e.Message);
            }
        }

        private bool IsFresh(string cacheKey)
        {
            var body = ReadCache(cacheKey);
            if (body == null || !body.StartsWith("#CACHED|"))
                return false;
            var end = body.IndexOf('\n');
            long t;
            return end > 8 && long.TryParse(body.Substring(8, end - 8), NumberStyles.Integer, CultureInfo.InvariantCulture, out t)
                   && DateTimeOffset.UtcNow.ToUnixTimeSeconds() - t < FreshSeconds;
        }

        // cTrader can drop drawings when the chart reloads history (zooming / scrolling out): put them straight back.
        private void EnsureDrawn()
        {
            if (_rendering || _lastBody == null || _drawn.Count == 0)
                return;
            if (_drawn.Any(n => Chart.FindObject(n) == null))
                Render(_lastBody, true);
        }

        private void Fetch()
        {
            if (_busy)
                return;
            var key = string.IsNullOrWhiteSpace(FeedKey) ? BuiltInFeedKey : FeedKey.Trim();
            if (string.IsNullOrWhiteSpace(key))
            {
                ShowStatus("ICT Scanner ✕ paste the feed key (indicator settings → Feed key)", Color.IndianRed);
                return;
            }
            var groups = Groups();
            if (groups.Length == 0)
            {
                Render("#ICTSCAN|1|-|0|0|0|0\n", true);
                return;
            }
            _busy = true;
            Http.GetAsync(Url(_tier, key), response => BeginInvokeOnMainThread(() =>
            {
                _busy = false;
                if (response.IsSuccessful)
                {
                    WriteCache(_cacheKey, response.Body);
                    Render(response.Body);
                    if (!_prefetched)
                    {
                        _prefetched = true;
                        Prefetch(1, key);
                    }
                }
                else
                    ShowStatus("ICT Scanner ✕ " + FirstError(response.Body, response.StatusCode) + " — showing the last drawing", Color.IndianRed);
            }));
        }

        // Download the other timeframe classes one after another in the background, so the next timeframe switch draws
        // straight from storage instead of waiting for the scanner.
        private void Prefetch(int tier, string key)
        {
            while (FitTimeframe && tier <= 5 && (tier == _tier || IsFresh(CacheKey(tier))))
                tier++;
            if (!FitTimeframe || tier > 5)
            {
                try { LocalStorage.Flush(LocalStorageScope.Type); } catch (Exception) { }
                return;
            }
            var cacheKey = CacheKey(tier);
            Http.GetAsync(Url(tier, key), response => BeginInvokeOnMainThread(() =>
            {
                if (response.IsSuccessful)
                    WriteCache(cacheKey, response.Body);
                Prefetch(tier + 1, key);
            }));
        }

        private static string FirstError(string body, int code)
        {
            foreach (var line in (body ?? "").Split('\n'))
                if (line.StartsWith("#ERR|"))
                    return line.Substring(5).Replace('|', ' ');
            return code == 0 ? "scanner not reachable" : "HTTP " + code;
        }

        private void Render(string body, bool force = false)
        {
            if (!force && body == _lastBody && !_drawn.Any(n => Chart.FindObject(n) == null))
            {
                ShowStatus(_status, Color.Gray);
                return;
            }
            _rendering = true;
            try
            {
                _lastBody = body;
                var keep = new HashSet<string>();
                var panel = new List<string[]>();
                double offset = 0;
                var scannerSym = "";
                foreach (var raw in body.Split('\n'))
                {
                    var line = raw.TrimEnd('\r');
                    if (line.Length == 0)
                        continue;
                    var f = line.Split('|');
                    if (f[0] == "#ICTSCAN" && f.Length >= 7)
                    {
                        scannerSym = f[2];
                        offset = AutoAlign ? Offset(long.Parse(f[4], CultureInfo.InvariantCulture), Num(f[5])) : 0;
                        continue;
                    }
                    if (line.StartsWith("#") || f.Length < 13)
                        continue;
                    if (f[0] == "P")
                    {
                        panel.Add(f);
                        continue;
                    }
                    var name = Prefix + f[2];
                    try
                    {
                        Draw(name, f, offset);
                        keep.Add(name);
                    }
                    catch (Exception e)
                    {
                        Print("ICT Scanner: could not draw {0}: {1}", f[2], e.Message);
                    }
                }
                foreach (var old in _drawn)
                    if (!keep.Contains(old))
                        Chart.RemoveObject(old);
                _drawn.Clear();
                _drawn.UnionWith(keep);
                foreach (var gone in _rowLabels.Keys.Where(k => !keep.Contains(k)).ToList())
                    _rowLabels.Remove(gone);
                PlaceRowLabels();
                DrawCard(panel);
                _status = string.Format(CultureInfo.InvariantCulture, "ICT Scanner · {0} · {1} objects · fit {2} · {3:HH:mm} UTC{4}", scannerSym,
                    keep.Count, TierName(_tier), Server.TimeInUtc, offset != 0 ? string.Format(CultureInfo.InvariantCulture, " · aligned {0:+0.##;-0.##}", offset) : "");
                ShowStatus(_status, Color.Gray);
            }
            finally
            {
                _rendering = false;
            }
        }

        private void Draw(string name, string[] f, double offset)
        {
            var t1 = FromUnix(f[3]);
            var p1 = Num(f[4]) + offset;
            var color = Argb(f[7]);
            var fill = f[8] == "1";
            var width = int.Parse(f[9], CultureInfo.InvariantCulture);
            var style = f[10] == "dash" ? LineStyle.Lines : f[10] == "dot" ? LineStyle.Dots : LineStyle.Solid;
            switch (f[0])
            {
                case "R":
                    var r = Chart.DrawRectangle(name, t1, p1, FromUnix(f[5]), Num(f[6]) + offset, color, width, style);
                    r.IsFilled = fill;
                    r.IsInteractive = false;
                    r.ZIndex = fill ? 0 : 1;
                    break;
                case "L":
                    var l = Chart.DrawTrendLine(name, t1, p1, FromUnix(f[5]), Num(f[6]) + offset, color, width, style);
                    l.ExtendToInfinity = f[11] == "1";
                    l.IsInteractive = false;
                    break;
                case "T":
                    var flags = int.Parse(f[11], CultureInfo.InvariantCulture);
                    var t = Chart.DrawText(name, string.Join("|", f, 12, f.Length - 12), (flags & 1) != 0 ? RightOfLastBar() : t1, p1, color);
                    if ((flags & 2) != 0)
                        _rowLabels[name] = Tuple.Create(p1, f[10] == "below" ? -1 : 1);
                    else
                        _rowLabels.Remove(name);
                    t.FontSize = FontSize;
                    t.IsBold = (flags & 4) != 0;
                    t.IsInteractive = false;
                    t.HorizontalAlignment = HorizontalAlignment.Right;   // text runs to the right of its time
                    t.VerticalAlignment = f[10] == "below" ? VerticalAlignment.Bottom : f[10] == "middle" ? VerticalAlignment.Center : VerticalAlignment.Top;
                    break;
            }
        }

        // Neighbouring boxes put every other label one text row further out; a row is a fixed number of pixels, so its
        // price distance depends on the zoom and is recomputed whenever the view changes.
        private void PlaceRowLabels()
        {
            if (_rowLabels.Count == 0 || Chart.Height <= 0 || Chart.TopY <= Chart.BottomY)
                return;
            var row = (Chart.TopY - Chart.BottomY) / Chart.Height * FontSize * 1.75;
            foreach (var kv in _rowLabels)
            {
                var t = Chart.FindObject(kv.Key) as ChartText;
                if (t != null)
                    t.Y = kv.Value.Item1 + kv.Value.Item2 * row;
            }
        }

        // SMT card: a dark rounded box with one line per panel row, edged in the verdict colour.
        private void DrawCard(List<string[]> rows)
        {
            if (_card != null)
            {
                Chart.RemoveControl(_card);
                _card = null;
            }
            if (rows.Count == 0)
                return;
            var stack = new StackPanel { Orientation = Orientation.Vertical };
            for (var i = 0; i < rows.Count; i++)
            {
                var f = rows[i];
                stack.AddChild(new TextBlock
                {
                    Text = string.Join("|", f, 12, f.Length - 12),
                    ForegroundColor = Argb(f[7]),
                    FontSize = i == 0 ? FontSize + 1 : FontSize,
                    FontWeight = i == 0 ? FontWeight.Bold : FontWeight.Normal,
                    Margin = new Thickness(0, i == 0 ? 0 : 3, 0, 0),
                });
            }
            var top = Corner == PanelCorner.TopLeft || Corner == PanelCorner.TopRight;
            var left = Corner == PanelCorner.TopLeft || Corner == PanelCorner.BottomLeft;
            _card = new Border
            {
                Child = stack,
                BackgroundColor = Color.FromArgb(215, 13, 18, 26),
                BorderColor = Argb(rows[0][7]),
                BorderThickness = new Thickness(1),
                CornerRadius = new CornerRadius(8),
                Padding = new Thickness(12, 8, 12, 9),
                Margin = new Thickness(left ? 10 : 0, top ? 34 : 0, left ? 0 : 70, top ? 0 : 34),
                HorizontalAlignment = left ? HorizontalAlignment.Left : HorizontalAlignment.Right,
                VerticalAlignment = top ? VerticalAlignment.Top : VerticalAlignment.Bottom,
                IsHitTestVisible = false,
            };
            Chart.AddControl(_card);
        }

        // A few bars to the right of the last candle on this chart's timeframe (empty space for level labels).
        private DateTime RightOfLastBar()
        {
            var n = Bars.Count;
            var step = n > 1 ? Bars.OpenTimes[n - 1] - Bars.OpenTimes[n - 2] : TimeSpan.FromMinutes(5);
            return Bars.OpenTimes[n - 1] + TimeSpan.FromTicks(step.Ticks * 2);
        }

        // Broker price minus scanner price on the scanner's last closed 5-minute bar (0 on Pepperstone; fixes other brokers).
        // Until the 5-minute bars have loaded in the background, the last offset measured for this symbol is used.
        private double Offset(long barUnix, double scannerClose)
        {
            var storeKey = "offset_" + new string(SymbolName.Where(char.IsLetterOrDigit).ToArray());
            if (_m5 == null)
            {
                double saved;
                string s = null;
                try { s = LocalStorage.GetString(storeKey, LocalStorageScope.Type); } catch (Exception) { }
                return double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out saved) ? saved : 0;
            }
            var i = _m5.OpenTimes.GetIndexByExactTime(DateTimeOffset.FromUnixTimeSeconds(barUnix).UtcDateTime);
            if (i < 0 || scannerClose <= 0)
                return 0;
            var diff = _m5.ClosePrices[i] - scannerClose;
            var offset = Math.Abs(diff) < scannerClose * 0.02 ? Math.Round(diff, Symbol.Digits) : 0;
            try { LocalStorage.SetString(storeKey, offset.ToString(CultureInfo.InvariantCulture), LocalStorageScope.Type); } catch (Exception) { }
            return offset;
        }

        private void ShowStatus(string text, Color color)
        {
            var s = Chart.DrawStaticText(Prefix + "status", text, VerticalAlignment.Bottom, HorizontalAlignment.Left, color);
            s.FontSize = 9;
        }

        private static DateTime FromUnix(string unix)
        {
            return DateTimeOffset.FromUnixTimeSeconds(long.Parse(unix, CultureInfo.InvariantCulture)).UtcDateTime;
        }

        private static double Num(string s)
        {
            return double.Parse(s, CultureInfo.InvariantCulture);
        }

        private static Color Argb(string hex)
        {
            var v = uint.Parse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture);
            return Color.FromArgb((int)(v >> 24), (int)((v >> 16) & 255), (int)((v >> 8) & 255), (int)(v & 255));
        }
    }
}