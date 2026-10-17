// Lightweight Charts wrapper with New York time axis and zone/level helpers.
const TF_SEC = { m5: 300, m15: 900, h1: 3600, h4: 14400, d1: 86400, w1: 604800 };

const NY_TICK_TIME = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false });
const NY_TICK_DAY = new Intl.DateTimeFormat('en-GB', { timeZone: 'America/New_York', day: '2-digit', month: 'short' });

function makeChart(container, digits) {
  const chart = LightweightCharts.createChart(container, {
    layout: { background: { color: '#0d1117' }, textColor: '#c9d1d9' },
    grid: { vertLines: { color: '#1b2330' }, horzLines: { color: '#1b2330' } },
    rightPriceScale: { borderColor: '#263040' },
    timeScale: { borderColor: '#263040', timeVisible: true, secondsVisible: false,
      tickMarkFormatter: (t, type) => (type >= 3 ? NY_TICK_TIME : NY_TICK_DAY).format(new Date(t * 1000)) },
    localization: { timeFormatter: t => nyShort(t) + ' NY' },
    autoSize: true,
  });
  const series = chart.addCandlestickSeries({
    upColor: '#26a69a', downColor: '#ef5350', borderVisible: false, wickUpColor: '#26a69a', wickDownColor: '#ef5350',
    priceFormat: { type: 'price', precision: digits, minMove: Math.pow(10, -digits) },
  });
  return { chart, series, lines: [], markers: [] };
}

function clearOverlays(c) { c.lines.forEach(l => c.series.removePriceLine(l)); c.lines = []; c.markers = []; c.series.setMarkers([]); }
function hline(c, price, color, title, style = 2, width = 1) {
  if (price == null) return;
  c.lines.push(c.series.createPriceLine({ price: Number(price), color, lineWidth: width, lineStyle: style, axisLabelVisible: true, title }));
}
function zone(c, lo, hi, color, title) { hline(c, hi, color, title, 1); hline(c, lo, color, '', 1); }

function barTimeAt(data, t) {  // start time of the bar containing t (seconds)
  let lo = 0, hi = data.length - 1, ans = null;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (data[mid].time <= t) { ans = data[mid].time; lo = mid + 1; } else hi = mid - 1; }
  return ans;
}
function addMarker(c, data, iso, opts) {
  const t = barTimeAt(data, Math.floor(new Date(iso).getTime() / 1000));
  if (t == null) return;
  c.markers.push({ time: t, ...opts });
  c.markers.sort((a, b) => a.time - b.time);
  c.series.setMarkers(c.markers);
}
