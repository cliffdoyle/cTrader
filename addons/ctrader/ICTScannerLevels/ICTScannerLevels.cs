// ICT Scanner Levels — draws the ICT scanner's levels on a cTrader chart, straight from the scanner's web feed.
//
// No cTrader MCP needed: the indicator downloads /api/feed/<symbol> from the scanner (data from the cTrader Open API) every
// minute and draws Asia / London boxes, 90-minute boxes, previous-cycle PXH / PXL / EQ and an SMT card. It only draws: it
// never trades and only touches its own objects (names start with "ICTS").
//
// Install: double-click "ICT Scanner Levels.algo" (or Automate → Indicators → New, paste this file, Build), then add it
// once to a chart and paste the feed key into "Feed key". Works with any cTrader broker (prices are aligned).
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

    [Indicator(IsOverlay = true, TimeZone = TimeZones.UTC, AccessRights = AccessRights.None, AutoRescale = false)]
    public class ICTScannerLevels : Indicator
    {
        [Parameter("Scanner URL", DefaultValue = "https://scanner.zuristay.com", Group = "Connection")]
        public string BaseUrl { get; set; }

        [Parameter("Feed key", DefaultValue = "", Group = "Connection")]
        public string FeedKey { get; set; }

        [Parameter("Scanner symbol (blank = this chart)", DefaultValue = "", Group = "Connection")]
        public string ScannerSymbol { get; set; }

        [Parameter("Refresh every (seconds)", DefaultValue = 60, MinValue = 15, Group = "Connection")]
        public int RefreshSeconds { get; set; }

        [Parameter("Asia + London boxes", DefaultValue = true, Group = "Show")]
        public bool ShowSessions { get; set; }

        [Parameter("Session times", DefaultValue = SessionStyle.KillZones, Group = "Show")]
        public SessionStyle Sessions { get; set; }

        [Parameter("90-minute boxes", DefaultValue = true, Group = "Show")]
        public bool ShowBoxes { get; set; }

        [Parameter("PXH / PXL levels", DefaultValue = true, Group = "Show")]
        public bool ShowCycles { get; set; }

        [Parameter("Cycles (month,week,day,90m)", DefaultValue = "week,day,90m", Group = "Show")]
        public string Kinds { get; set; }

        [Parameter("Session levels (PDH, PWH…)", DefaultValue = false, Group = "Show")]
        public bool ShowLevels { get; set; }

        [Parameter("SMT card", DefaultValue = true, Group = "Show")]
        public bool ShowSmt { get; set; }

        [Parameter("SMT card position", DefaultValue = PanelCorner.TopLeft, Group = "Look")]
        public PanelCorner Corner { get; set; }

        [Parameter("Label size", DefaultValue = 10, MinValue = 7, MaxValue = 20, Group = "Look")]
        public int FontSize { get; set; }

        [Parameter("Align prices to this broker", DefaultValue = true, Group = "Look")]
        public bool AutoAlign { get; set; }

        // Last feed per symbol/settings, shared by all copies in this cTrader, so a reloaded chart redraws at once.
        private static readonly Dictionary<string, string> Cache = new Dictionary<string, string>();

        private string Prefix = "ICTS_";
        private readonly HashSet<string> _drawn = new HashSet<string>();
        private Bars _m5;
        private bool _busy, _rendering;
        private string _lastBody, _cacheKey;
        private string _status = "ICT Scanner · connecting…";
        private Border _card;
        // labels pushed one text row further out: name → (price at the box edge, +1 above / -1 below); re-placed on zoom
        private readonly Dictionary<string, Tuple<double, int>> _rowLabels = new Dictionary<string, Tuple<double, int>>();

        protected override void Initialize()
        {
            // own object names per indicator copy, so two copies on one chart never fight over the same objects
            Prefix = "ICTS" + (InstanceId ?? "").Replace("-", "").PadRight(6, '0').Substring(0, 6) + "_";
            _m5 = MarketData.GetBars(TimeFrame.Minute5);
            _cacheKey = string.Join("|", FeedSymbol(), Groups(), Kinds, Sessions);
            Chart.ZoomChanged += _ => { EnsureDrawn(); PlaceRowLabels(); };
            Chart.ScrollChanged += _ => { EnsureDrawn(); PlaceRowLabels(); };
            Chart.SizeChanged += _ => PlaceRowLabels();
            Chart.ObjectsRemoved += _ => EnsureDrawn();
            string cached;
            if (Cache.TryGetValue(_cacheKey, out cached))
                Render(cached);
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

        private string Groups()
        {
            var g = new List<string>();
            if (ShowSessions) g.Add("sessions");
            if (ShowBoxes) g.Add("boxes");
            if (ShowCycles) g.Add("cycles");
            if (ShowLevels) g.Add("levels");
            if (ShowSmt) g.Add("smt");
            return string.Join(",", g);
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
            if (string.IsNullOrWhiteSpace(FeedKey))
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
            var url = string.Format("{0}/api/feed/{1}?groups={2}&kinds={3}&sess={4}&token={5}", (BaseUrl ?? "").TrimEnd('/'),
                Uri.EscapeDataString(FeedSymbol()), groups, Uri.EscapeDataString(Kinds ?? ""),
                Sessions == SessionStyle.Continuous ? "full" : "kz", Uri.EscapeDataString(FeedKey ?? ""));
            _busy = true;
            Http.GetAsync(url, response => BeginInvokeOnMainThread(() =>
            {
                _busy = false;
                if (response.IsSuccessful)
                {
                    Cache[_cacheKey] = response.Body;
                    Render(response.Body);
                }
                else
                    ShowStatus("ICT Scanner ✕ " + FirstError(response.Body, response.StatusCode) + " — showing the last drawing", Color.IndianRed);
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
                _status = string.Format(CultureInfo.InvariantCulture, "ICT Scanner · {0} · {1} objects · {2:HH:mm} UTC{3}", scannerSym,
                    keep.Count, Server.TimeInUtc, offset != 0 ? string.Format(CultureInfo.InvariantCulture, " · aligned {0:+0.##;-0.##}", offset) : "");
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
        private double Offset(long barUnix, double scannerClose)
        {
            var i = _m5.OpenTimes.GetIndexByExactTime(DateTimeOffset.FromUnixTimeSeconds(barUnix).UtcDateTime);
            if (i < 0 || scannerClose <= 0)
                return 0;
            var diff = _m5.ClosePrices[i] - scannerClose;
            return Math.Abs(diff) < scannerClose * 0.02 ? Math.Round(diff, Symbol.Digits) : 0;
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