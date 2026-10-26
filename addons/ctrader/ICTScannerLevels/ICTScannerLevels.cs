// ICT Scanner Levels — draws the ICT scanner's levels on a cTrader chart, straight from the scanner's web feed.
//
// No cTrader MCP needed: the indicator downloads /api/feed/<symbol> from the scanner (data from the cTrader Open API) every
// minute and draws 90-minute session boxes, previous-cycle PXH / PXL / EQ, session levels and the SMT read. It only draws:
// it never trades and only touches objects whose names start with "ICTS_".
//
// Install: double-click "ICT Scanner Levels.algo" (or Automate → Indicators → New, paste this file, Build), then add it to
// any NAS100 / US30 / US500 chart and paste the feed key into "Feed key".
using System;
using System.Collections.Generic;
using System.Globalization;
using cAlgo.API;
using cAlgo.API.Internals;

namespace cAlgo.Indicators
{
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

        [Parameter("90-minute boxes", DefaultValue = true, Group = "Show")]
        public bool ShowBoxes { get; set; }

        [Parameter("PXH / PXL levels", DefaultValue = true, Group = "Show")]
        public bool ShowCycles { get; set; }

        [Parameter("Cycles (month,week,day,90m)", DefaultValue = "week,day,90m", Group = "Show")]
        public string Kinds { get; set; }

        [Parameter("Session levels (Asia, London, PDH…)", DefaultValue = false, Group = "Show")]
        public bool ShowLevels { get; set; }

        [Parameter("SMT divergence", DefaultValue = true, Group = "Show")]
        public bool ShowSmt { get; set; }

        [Parameter("Label size", DefaultValue = 10, MinValue = 7, MaxValue = 20, Group = "Look")]
        public int FontSize { get; set; }

        [Parameter("Align prices to this broker", DefaultValue = true, Group = "Look")]
        public bool AutoAlign { get; set; }

        private const string Prefix = "ICTS_";
        private readonly HashSet<string> _drawn = new HashSet<string>();
        private Bars _m5;
        private bool _busy;
        private string _lastBody;
        private string _status = "ICT Scanner · connecting…";

        protected override void Initialize()
        {
            _m5 = MarketData.GetBars(TimeFrame.Minute5);
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
            foreach (var name in _drawn)
                Chart.RemoveObject(name);
            Chart.RemoveObject(Prefix + "status");
        }

        private string Groups()
        {
            var g = new List<string>();
            if (ShowBoxes) g.Add("boxes");
            if (ShowCycles) g.Add("cycles");
            if (ShowLevels) g.Add("levels");
            if (ShowSmt) g.Add("smt");
            return string.Join(",", g);
        }

        private void Fetch()
        {
            if (_busy)
                return;
            var groups = Groups();
            if (groups.Length == 0)
            {
                Render("#ICTSCAN|1|-|0|0|0|0\n");
                return;
            }
            var sym = string.IsNullOrWhiteSpace(ScannerSymbol) ? SymbolName : ScannerSymbol.Trim();
            var url = string.Format("{0}/api/feed/{1}?groups={2}&kinds={3}&token={4}", (BaseUrl ?? "").TrimEnd('/'),
                Uri.EscapeDataString(sym), groups, Uri.EscapeDataString(Kinds ?? ""), Uri.EscapeDataString(FeedKey ?? ""));
            _busy = true;
            Http.GetAsync(url, response => BeginInvokeOnMainThread(() =>
            {
                _busy = false;
                if (response.IsSuccessful)
                    Render(response.Body);
                else
                    ShowStatus("ICT Scanner ✕ " + FirstError(response.Body, response.StatusCode), Color.IndianRed);
            }));
        }

        private static string FirstError(string body, int code)
        {
            foreach (var line in (body ?? "").Split('\n'))
                if (line.StartsWith("#ERR|"))
                    return line.Substring(5).Replace('|', ' ');
            return code == 0 ? "scanner not reachable" : "HTTP " + code;
        }

        private void Render(string body)
        {
            if (body == _lastBody)
            {
                ShowStatus(_status, Color.Gray);
                return;
            }
            _lastBody = body;
            var keep = new HashSet<string>();
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
            _status = string.Format(CultureInfo.InvariantCulture, "ICT Scanner · {0} · {1} objects · {2:HH:mm} UTC{3}", scannerSym, keep.Count,
                Server.TimeInUtc, offset != 0 ? string.Format(CultureInfo.InvariantCulture, " · aligned {0:+0.##;-0.##}", offset) : "");
            ShowStatus(_status, Color.Gray);
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
                    var t = Chart.DrawText(name, string.Join("|", f, 12, f.Length - 12), t1, p1, color);
                    t.FontSize = FontSize;
                    t.IsInteractive = false;
                    break;
            }
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
