//+------------------------------------------------------------------+
//| ICT Scanner Levels.mq5                                           |
//| Draws the ICT scanner's levels on an MT5 chart from the scanner's |
//| web feed: 90-minute session boxes, previous-cycle PXH/PXL/EQ,     |
//| session levels and the SMT read.                                 |
//|                                                                  |
//| DRAWING ONLY: this expert contains no trading functions at all   |
//| (no OrderSend / CTrade) and only touches objects named "ICTS_*". |
//| It is an Expert Advisor because MT5 allows WebRequest only in    |
//| experts and scripts, not in indicators.                          |
//|                                                                  |
//| Setup: Tools → Options → Expert Advisors → tick "Allow           |
//| WebRequest for listed URL" and add https://scanner.zuristay.com  |
//+------------------------------------------------------------------+
#property copyright "ICT Scanner"
#property version   "1.20"
#property description "Draws the ICT scanner's 90-min boxes, PXH/PXL and SMT. Never trades."

enum SessionTimes { KillZones = 0, Continuous = 1 };

input string InpUrl        = "https://scanner.zuristay.com"; // Scanner URL
input string InpKey        = "";                            // Feed key
input string InpSymbol     = "";                            // Scanner symbol (blank = this chart)
input int    InpRefresh    = 60;                            // Refresh every (seconds)
input bool   InpSessions   = true;                          // Asia + London boxes
input SessionTimes InpSessStyle = KillZones;                // Session times (kill zones / continuous)
input bool   InpBoxes      = true;                          // 90-minute boxes
input bool   InpCycles     = true;                          // PXH / PXL levels
input string InpKinds      = "week,day,90m";                // Cycles (month,week,day,90m)
input bool   InpLevels     = false;                         // Session levels (Asia, London, PDH...)
input bool   InpSmt        = true;                          // SMT divergence
input int    InpFontSize   = 9;                             // Label size
input bool   InpAutoAlign  = true;                          // Align prices to this broker

#define PREFIX "ICTS_"
string g_last_body = "";
string g_status = "ICT Scanner - connecting...";

//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(MathMax(15, InpRefresh));
   Fetch();
   return(INIT_SUCCEEDED);
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   ObjectsDeleteAll(0, PREFIX);
   ChartRedraw();
  }

void OnTick() {}

void OnTimer() { Fetch(); }

//+------------------------------------------------------------------+
string Groups()
  {
   string g = "";
   if(InpSessions) g += "sessions,";
   if(InpBoxes)  g += "boxes,";
   if(InpCycles) g += "cycles,";
   if(InpLevels) g += "levels,";
   if(InpSmt)    g += "smt,";
   if(StringLen(g) > 0) g = StringSubstr(g, 0, StringLen(g) - 1);
   return g;
  }

string UrlEncode(const string s)
  {
   uchar b[];
   int n = StringToCharArray(s, b, 0, WHOLE_ARRAY, CP_UTF8) - 1;
   string out = "";
   for(int i = 0; i < n; i++)
     {
      uchar c = b[i];
      if((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~')
         out += CharToString(c);
      else
         out += StringFormat("%%%02X", c);
     }
   return out;
  }

void Fetch()
  {
   if(InpKey == "")
     {
      Status("ICT Scanner X paste the feed key (Inputs > Feed key)", clrIndianRed);
      return;
     }
   string groups = Groups();
   if(groups == "")
     {
      ObjectsDeleteAll(0, PREFIX);
      Status("ICT Scanner - nothing selected", clrGray);
      return;
     }
   string sym = InpSymbol == "" ? _Symbol : InpSymbol;
   string base = InpUrl;
   while(StringLen(base) > 0 && StringGetCharacter(base, StringLen(base) - 1) == '/')
      base = StringSubstr(base, 0, StringLen(base) - 1);
   string url = base + "/api/feed/" + UrlEncode(sym) + "?groups=" + groups + "&kinds=" + UrlEncode(InpKinds)
                + "&sess=" + (InpSessStyle == Continuous ? "full" : "kz") + "&token=" + UrlEncode(InpKey);
   char post[], result[];
   string headers;
   ResetLastError();
   int code = WebRequest("GET", url, "", 10000, post, result, headers);
   if(code == -1)
     {
      int err = GetLastError();
      if(err == 4014)
         Status("ICT Scanner X add " + base + " in Tools > Options > Expert Advisors > Allow WebRequest", clrIndianRed);
      else
         Status("ICT Scanner X scanner not reachable (error " + IntegerToString(err) + ")", clrIndianRed);
      return;
     }
   string body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   if(code != 200)
     {
      Status("ICT Scanner X " + FirstError(body, code), clrIndianRed);
      return;
     }
   Render(body);
  }

string FirstError(const string body, int code)
  {
   string lines[];
   int n = StringSplit(body, '\n', lines);
   for(int i = 0; i < n; i++)
      if(StringFind(lines[i], "#ERR|") == 0)
        {
         string e = StringSubstr(lines[i], 5);
         StringReplace(e, "|", " ");
         return e;
        }
   return "HTTP " + IntegerToString(code);
  }

//+------------------------------------------------------------------+
//| Feed: #ICTSCAN|1|SYM|price|barUnix|barClose|generated            |
//|       kind|group|id|t1|p1|t2|p2|AARRGGBB|fill|width|style|ext|txt |
//+------------------------------------------------------------------+
void Render(const string body)
  {
   if(body == g_last_body)
     {
      Status(g_status, clrGray);
      return;
     }
   g_last_body = body;
   string lines[];
   int n = StringSplit(body, '\n', lines);
   string keep[];
   int kept = 0;
   double offset = 0;
   string scanner_sym = "";
   long shift = ServerShift();
   int panel_line = 0;
   for(int i = 0; i < n; i++)
     {
      string line = lines[i];
      StringReplace(line, "\r", "");
      if(StringLen(line) == 0)
         continue;
      string f[];
      int k = StringSplit(line, '|', f);
      if(f[0] == "#ICTSCAN" && k >= 7)
        {
         scanner_sym = f[2];
         offset = InpAutoAlign ? Offset((datetime)(StringToInteger(f[4]) + shift), StringToDouble(f[5])) : 0;
         continue;
        }
      if(StringGetCharacter(line, 0) == '#' || k < 13)
         continue;
      string name = PREFIX + f[2];
      bool ok;
      if(f[0] == "P")
        {
         if(panel_line == 0)
           {
            // card first, so the labels created after it are drawn on top
            int rows = 0, chars = 0;
            for(int q = i; q < n; q++)
              {
               if(StringFind(lines[q], "P|") != 0)
                  continue;
               string qf[];
               int qk = StringSplit(lines[q], '|', qf);
               rows++;
               chars = MathMax(chars, qk >= 13 ? StringLen(qf[12]) : 0);
              }
            Card(rows, chars, HexToUint(f[7]));
            ArrayResize(keep, kept + 1);
            keep[kept++] = PREFIX + "card";
           }
         ok = PanelLine(name, panel_line++, f, k);
        }
      else
         ok = Draw(name, f, k, offset, shift);
      if(ok)
        {
         ArrayResize(keep, kept + 1);
         keep[kept++] = name;
        }
     }
   // remove our objects that are no longer in the feed
   for(int j = ObjectsTotal(0, -1, -1) - 1; j >= 0; j--)
     {
      string nm = ObjectName(0, j, -1, -1);
      if(StringFind(nm, PREFIX) != 0 || nm == PREFIX + "status")
         continue;
      bool found = false;
      for(int q = 0; q < kept && !found; q++)
         found = (keep[q] == nm);
      if(!found)
         ObjectDelete(0, nm);
     }
   g_status = StringFormat("ICT Scanner - %s - %d objects - %s UTC%s", scanner_sym, kept,
                           TimeToString(TimeGMT(), TIME_MINUTES),
                           offset != 0 ? StringFormat(" - aligned %+.2f", offset) : "");
   Status(g_status, clrGray);
   ChartRedraw();
  }

bool Draw(const string name, string &f[], int k, double offset, long shift)
  {
   datetime t1 = (datetime)(StringToInteger(f[3]) + shift);
   double p1 = StringToDouble(f[4]) + offset;
   uint argb = HexToUint(f[7]);
   bool fill = (f[8] == "1");
   int width = (int)StringToInteger(f[9]);
   ENUM_LINE_STYLE style = f[10] == "dash" ? STYLE_DASH : f[10] == "dot" ? STYLE_DOT : STYLE_SOLID;
   color col = Blend(argb);
   string kind = f[0];
   if(kind == "R" || kind == "L")
     {
      datetime t2 = (datetime)(StringToInteger(f[5]) + shift);
      double p2 = StringToDouble(f[6]) + offset;
      ENUM_OBJECT type = kind == "R" ? OBJ_RECTANGLE : OBJ_TREND;
      if(ObjectFind(0, name) < 0 && !ObjectCreate(0, name, type, 0, t1, p1, t2, p2))
         return false;
      ObjectMove(0, name, 0, t1, p1);
      ObjectMove(0, name, 1, t2, p2);
      ObjectSetInteger(0, name, OBJPROP_COLOR, col);
      ObjectSetInteger(0, name, OBJPROP_WIDTH, width);
      ObjectSetInteger(0, name, OBJPROP_STYLE, style);
      if(kind == "R")
        {
         ObjectSetInteger(0, name, OBJPROP_FILL, fill);
         ObjectSetInteger(0, name, OBJPROP_BACK, fill);   // filled boxes go behind the candles (TradingView look)
        }
      else
        {
         ObjectSetInteger(0, name, OBJPROP_RAY_RIGHT, f[11] == "1");
         ObjectSetInteger(0, name, OBJPROP_RAY_LEFT, false);
        }
     }
   else if(kind == "T")
     {
      string text = f[12];
      for(int i = 13; i < k; i++)
         text += "|" + f[i];
      if(f[11] == "1")   // right of the last candle (empty space)
         t1 = iTime(_Symbol, _Period, 0) + 2 * PeriodSeconds(_Period);
      if(ObjectFind(0, name) < 0 && !ObjectCreate(0, name, OBJ_TEXT, 0, t1, p1))
         return false;
      ObjectMove(0, name, 0, t1, p1);
      ObjectSetString(0, name, OBJPROP_TEXT, text);
      ObjectSetString(0, name, OBJPROP_FONT, "Arial");
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, f[10] == "below" ? ANCHOR_LEFT_UPPER : f[10] == "middle" ? ANCHOR_LEFT : ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, name, OBJPROP_COLOR, col);
     }
   else
      return false;
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   return true;
  }

// SMT card: a dark box (OBJ_RECTANGLE_LABEL) with one label per line, top left below the chart title.
#define CARD_X 10
#define CARD_Y 30
bool PanelLine(const string name, int line, string &f[], int k)
  {
   string text = f[12];
   for(int i = 13; i < k; i++)
      text += "|" + f[i];
   ObjectDelete(0, name);   // re-create so the text is drawn on top of the card background
   if(!ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0))
      return false;
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, CARD_X + 12);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, CARD_Y + 8 + line * (InpFontSize + 9));
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetString(0, name, OBJPROP_FONT, line == 0 ? "Arial Bold" : "Arial");
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, InpFontSize);
   ObjectSetInteger(0, name, OBJPROP_COLOR, Rgb(HexToUint(f[7])));
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
   return true;
  }

void Card(int lines, int max_chars, uint edge_argb)
  {
   string name = PREFIX + "card";
   if(lines == 0)
     {
      ObjectDelete(0, name);
      return;
     }
   ObjectDelete(0, name);
   ObjectCreate(0, name, OBJ_RECTANGLE_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, CARD_X);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, CARD_Y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, (int)(max_chars * InpFontSize * 0.72) + 26);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, lines * (InpFontSize + 9) + 14);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, C'13,18,26');
   ObjectSetInteger(0, name, OBJPROP_BORDER_TYPE, BORDER_FLAT);
   ObjectSetInteger(0, name, OBJPROP_COLOR, Rgb(edge_argb));
   ObjectSetInteger(0, name, OBJPROP_BACK, false);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
   ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
  }

color Rgb(uint argb)
  {
   return (color)(((argb >> 16) & 255) | (((argb >> 8) & 255) << 8) | ((argb & 255) << 16));
  }

// Seconds to add to UTC to get this broker's server time (chart time).
long ServerShift()
  {
   long d = (long)(TimeTradeServer() - TimeGMT());
   return (long)MathRound(d / 900.0) * 900;
  }

// Broker price minus scanner price on the scanner's last closed 5-minute bar (0 on Pepperstone; fixes other brokers).
double Offset(datetime bar_time, double scanner_close)
  {
   if(scanner_close <= 0)
      return 0;
   int i = iBarShift(_Symbol, PERIOD_M5, bar_time, true);
   if(i < 0)
      return 0;
   double diff = iClose(_Symbol, PERIOD_M5, i) - scanner_close;
   return MathAbs(diff) < scanner_close * 0.02 ? NormalizeDouble(diff, _Digits) : 0;
  }

uint HexToUint(const string hex)
  {
   uint v = 0;
   for(int i = 0; i < StringLen(hex); i++)
     {
      ushort c = StringGetCharacter(hex, i);
      uint d = c >= '0' && c <= '9' ? c - '0' : c >= 'A' && c <= 'F' ? c - 'A' + 10 : c >= 'a' && c <= 'f' ? c - 'a' + 10 : 0;
      v = (v << 4) | d;
     }
   return v;
  }

// MT5 objects have no transparency: mix the colour with the chart background by its alpha (a 15 % fill looks like
// TradingView's translucent boxes once it is drawn behind the candles).
color Blend(uint argb)
  {
   double a = ((argb >> 24) & 255) / 255.0;
   int r = (int)((argb >> 16) & 255), g = (int)((argb >> 8) & 255), b = (int)(argb & 255);
   color bg = (color)ChartGetInteger(0, CHART_COLOR_BACKGROUND);
   int br = (int)(bg & 255), bgc = (int)((bg >> 8) & 255), bb = (int)((bg >> 16) & 255);
   int R = (int)MathRound(a * r + (1 - a) * br), G = (int)MathRound(a * g + (1 - a) * bgc), B = (int)MathRound(a * b + (1 - a) * bb);
   return (color)(R | (G << 8) | (B << 16));
  }

void Status(const string text, color col)
  {
   string name = PREFIX + "status";
   if(ObjectFind(0, name) < 0)
     {
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
      ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_LOWER);
      ObjectSetInteger(0, name, OBJPROP_ANCHOR, ANCHOR_LEFT_LOWER);
      ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 8);
      ObjectSetInteger(0, name, OBJPROP_YDISTANCE, 8);
      ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, name, OBJPROP_HIDDEN, true);
     }
   ObjectSetString(0, name, OBJPROP_TEXT, text);
   ObjectSetInteger(0, name, OBJPROP_COLOR, col);
   ChartRedraw();
  }
//+------------------------------------------------------------------+
