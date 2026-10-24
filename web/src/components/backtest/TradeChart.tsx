import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { getJSON, sendJSON, type BacktestTrade, type ChartSeries, type IndicatorInfo } from "../../api";
import { fmtPrice } from "../../format";
import { MaximizeButton, useMaximize } from "../../ui/maximize";
import { IndicatorPicker, PALETTE, type AddedIndicator } from "./IndicatorPicker";

// Lightweight Charts renders UTC; shift by +5:30 so the axis reads IST.
const IST = 19_800;
const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
const T = (utcSec: number) => (utcSec + IST) as UTCTimestamp;

const COLORS = { up: "#22c55e", down: "#f43f5e", emaFast: "#3987e5", emaSlow: "#d95926" };
// Strategy indicators: the validated EMA colours first, then the shared palette.
const STRATEGY_COLORS = ["#3987e5", "#d95926", "#eab308", "#a78bfa", "#14b8a6", "#ec4899", "#84cc16", "#f97316", "#06b6d4", "#64748b"];

const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "60m"] as const;
// Trading days of history requested per timeframe, so a 1m chart does not pull a year of minutes.
const DAYS: Record<string, number> = { "1m": 8, "3m": 25, "5m": 60, "15m": 150, "30m": 250, "60m": 250, "1d": 400 };
// Bars shown on first paint.
const VISIBLE: Record<string, number> = { "1m": 375, "3m": 250, "5m": 230, "15m": 200, "30m": 150, "60m": 120, "1d": 160 };
const PREF_KEY = "chart-indicators";
const LOCAL_KEY = "trade-chart-indicators-v2";

type Bar = [number, number, number, number, number];

interface ChartData {
  underlying: string;
  timeframe: string;
  run_timeframe?: string;
  exact?: boolean;
  bars: Bar[];
  series?: ChartSeries[];
  symbol?: string;
  symbols?: string[];
}

interface Hover {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  values: [string, number][];
  trades: BacktestTrade[];
}

const exitBar = (t: BacktestTrade) => t.exit_bar_time ?? t.exit_time;

function compact(v: number) {
  const a = Math.abs(v);
  const s = a >= 1e5 ? `${(a / 1e5).toFixed(1)}L` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}k` : a.toFixed(0);
  return `${v < 0 ? "−" : "+"}₹${s}`;
}

const istDay = (t: number) => new Date((t + IST) * 1000).toISOString().slice(0, 10);
const extraParam = (xs: AddedIndicator[]) => xs.map((x) => `${x.name}:${x.period ?? ""}:${x.mult ?? ""}`).join(",");

function loadLocal(): AddedIndicator[] {
  try {
    const v = JSON.parse(localStorage.getItem(LOCAL_KEY) ?? "[]");
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

/** The series id the server gives an added indicator ("name|period|mult|"), to match its colour. */
const sameIndicator = (s: ChartSeries, a: AddedIndicator) => {
  const [n, p, m] = s.id.split("|");
  return n === a.name && (p === "" ? a.period == null : Number(p) === a.period) && (m === "" || a.mult == null || Number(m) === a.mult);
};

export function TradeChart({
  runId,
  trades: allTrades,
  selected,
  onSelect,
  daily = false,
  fill = false,
}: {
  runId: string;
  trades: BacktestTrade[];
  selected: BacktestTrade | null;
  onSelect: (t: BacktestTrade | null) => void;
  daily?: boolean; // a stock run: daily candles of one stock at a time
  fill?: boolean; // grow to the parent's height instead of a fixed one
}) {
  const mx = useMaximize();
  const [symbol, setSymbol] = useState<string | null>(null);
  const trades = useMemo(
    () => (daily ? allTrades.filter((t) => t.contract === (symbol ?? allTrades[0]?.contract)) : allTrades),
    [daily, allTrades, symbol],
  );
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const values = useRef<Map<string, Map<number, number>>>(new Map());
  const [tf, setTf] = useState<string | null>(null); // null = the run's own timeframe
  const [fromDate, setFromDate] = useState<string | null>(null); // widen the range to reach a selected trade
  const [data, setData] = useState<ChartData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [added, setAdded] = useState<AddedIndicator[]>(loadLocal);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [catalogue, setCatalogue] = useState<IndicatorInfo[] | null>(null);
  const [picker, setPicker] = useState(false);
  const prefsLoaded = useRef(false);

  // The viewer's added indicators: per account on the server (falls back to this browser if that fails).
  useEffect(() => {
    getJSON<{ value: AddedIndicator[] | null }>(`/prefs/${PREF_KEY}`)
      .then((r) => {
        if (Array.isArray(r.value)) setAdded(r.value);
      })
      .catch(() => undefined)
      .finally(() => {
        prefsLoaded.current = true;
      });
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(LOCAL_KEY, JSON.stringify(added));
    } catch {
      /* storage unavailable: settings just won't persist in this browser */
    }
    if (prefsLoaded.current) sendJSON("PUT", `/prefs/${PREF_KEY}`, { value: added }, { quiet: true }).catch(() => undefined);
  }, [added]);

  const openPicker = () => {
    setPicker(true);
    if (!catalogue) getJSON<IndicatorInfo[]>("/indicators").then(setCatalogue).catch((e: Error) => setError(e.message));
  };

  // Index for hover lookups: bar time -> trades entering or exiting on that bar.
  const byBar = useMemo(() => {
    const m = new Map<number, BacktestTrade[]>();
    for (const t of trades) {
      for (const k of new Set([sec(t.entry_time), sec(exitBar(t))])) {
        m.set(k, [...(m.get(k) ?? []), t]);
      }
    }
    return m;
  }, [trades]);

  const extra = extraParam(added);
  useEffect(() => {
    let alive = true;
    setError(null);
    const q = new URLSearchParams();
    if (tf && !daily) q.set("timeframe", tf);
    if (daily && symbol) q.set("symbol", symbol);
    if (fromDate) q.set("from_date", fromDate);
    else if (!daily) q.set("last_days", String(DAYS[tf ?? "5m"] ?? 60));
    if (extra) q.set("extra", extra);
    getJSON<ChartData>(`/backtests/${runId}/chart?${q}`)
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [runId, tf, fromDate, symbol, daily, extra]);

  // Picking a trade of another stock switches the chart to that stock.
  useEffect(() => {
    if (daily && selected && selected.contract !== (symbol ?? data?.symbol)) {
      setSymbol(selected.contract);
      setFromDate(null);
      setData(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, daily]);

  // A selected trade older than the loaded range: load back to it.
  useEffect(() => {
    if (!selected || !data?.bars.length) return;
    const entry = sec(selected.entry_time);
    if (entry < data.bars[0][0]) setFromDate(istDay(entry - 4 * 86_400));
  }, [selected, data]);

  // Colour per series: added indicators keep the colour picked for them; strategy ones get the validated EMA colours first.
  const series = data?.series ?? [];
  const colorOf = useMemo(() => {
    const m = new Map<string, string>();
    // The strategy's two shortest moving averages take the validated fast / slow EMA colours, the rest the palette.
    const mas = series
      .filter((s) => s.source === "strategy" && ["ema", "sma", "wma", "hma", "dema", "tema", "rma", "vwma"].includes(s.name))
      .sort((a, b) => Number(a.id.split("|")[1]) - Number(b.id.split("|")[1]));
    mas.slice(0, 2).forEach((s, i) => m.set(s.id, i === 0 ? COLORS.emaFast : COLORS.emaSlow));
    const rest = STRATEGY_COLORS.filter((c) => c !== COLORS.emaFast && c !== COLORS.emaSlow);
    let k = 0;
    for (const s of series) {
      if (m.has(s.id)) continue;
      if (s.source === "user") {
        const a = added.find((x) => sameIndicator(s, x));
        m.set(s.id, a?.color ?? PALETTE[m.size % PALETTE.length]);
      } else {
        m.set(s.id, rest[k++ % rest.length]);
      }
    }
    return m;
  }, [series, added]);

  // Build the chart once per dataset; indicators and markers are layered on in the effects below.
  useEffect(() => {
    if (!box.current || !data) return;
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa7b8", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: "#161b24" }, horzLines: { color: "#161b24" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: !daily, secondsVisible: false, rightOffset: 3 },
    });
    const cs = c.addCandlestickSeries({
      upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    cs.setData(data.bars.map(([t, o, h, l, cl]) => ({ time: T(t), open: o, high: h, low: l, close: cl })));

    const bars = new Map(data.bars.map((b) => [b[0], b]));
    c.subscribeCrosshairMove((e: MouseEventParams) => {
      if (e.time == null) return setHover(null);
      const t = (e.time as number) - IST;
      const b = bars.get(t);
      if (!b) return setHover(null);
      const vals: [string, number][] = [];
      values.current.forEach((m, k) => {
        const v = m.get(t);
        if (v != null) vals.push([k, v]);
      });
      setHover({ t, o: b[1], h: b[2], l: b[3], c: b[4], values: vals, trades: byBar.get(t) ?? [] });
    });
    c.subscribeClick((e: MouseEventParams) => {
      if (e.time == null) return;
      const hit = byBar.get((e.time as number) - IST);
      if (hit?.length) onSelect(hit[0]);
    });
    const n = data.bars.length;
    if (n && daily) c.timeScale().fitContent();
    else if (n) c.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - (VISIBLE[data.timeframe] ?? 230)), to: n + 3 });
    chart.current = c;
    candles.current = cs;
    return () => {
      c.remove();
      chart.current = null;
      candles.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, byBar]);

  // Indicators, as the server computed them: "price" ones overlay the candles; every other pane (oscillators, futures,
  // MACD, VIX, signals ...) gets its own band at the bottom, with the strategy's thresholds as dashed lines.
  useEffect(() => {
    const c = chart.current;
    if (!c || !data) return;
    const shown = series.filter((s) => !hidden.has(s.id) && s.points.length);
    const panes = [...new Set(shown.filter((s) => s.pane !== "price").map((s) => s.pane))];
    const h = panes.length ? Math.min(0.22, 0.6 / panes.length) : 0;
    const added_: ISeriesApi<"Line" | "Histogram">[] = [];
    const vals = new Map<string, Map<number, number>>();
    for (const s of shown) {
      const color = colorOf.get(s.id) ?? "#9aa7b8";
      const scale = s.pane === "price" ? "right" : `pane:${s.pane}`;
      const histogram = s.pane === "signals" || s.name === "macd_hist" || s.name === "volume";
      let api: ISeriesApi<"Line" | "Histogram">;
      if (histogram) {
        const hs = c.addHistogramSeries({ priceScaleId: scale, priceLineVisible: false, lastValueVisible: false });
        hs.setData(s.points.map(([t, v]) => ({ time: T(t), value: v, color: s.name === "macd_hist" ? (v >= 0 ? "#22c55e80" : "#f43f5e80") : `${color}aa` })));
        api = hs;
      } else {
        const ls = c.addLineSeries({
          color, lineWidth: s.source === "strategy" ? 2 : 1, priceLineVisible: false, lastValueVisible: false,
          crosshairMarkerVisible: false, priceScaleId: scale, title: "",
        });
        ls.setData(s.points.map(([t, v]) => ({ time: T(t), value: v })));
        api = ls;
      }
      for (const th of s.thresholds) {
        api.createPriceLine({ price: th, color: "#64748b", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "" });
      }
      added_.push(api);
      vals.set(s.label, new Map(s.points));
    }
    // Scales exist only once a series uses them, so the bands are laid out after the series are added.
    c.priceScale("right").applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 + h * panes.length } });
    panes.forEach((p, i) => {
      // pane i from the top of the indicator area: bands stack down to the bottom edge
      const fromBottom = panes.length - 1 - i;
      c.priceScale(`pane:${p}`).applyOptions({
        scaleMargins: { top: 1 - h * (fromBottom + 1) + 0.015, bottom: h * fromBottom + 0.015 },
        borderVisible: false,
      });
    });
    values.current = vals;
    return () => {
      // When the dataset changed the chart itself is already disposed (its cleanup ran first), taking the series with it.
      if (chart.current === c) for (const s of added_) c.removeSeries(s);
      values.current = new Map();
    };
  }, [data, series, hidden, colorOf, byBar]);

  // Markers: all trades, labelled only for the selected one (labels on every trade are unreadable).
  useEffect(() => {
    const cs = candles.current;
    if (!cs || !data) return;
    const have = new Set(data.bars.map((b) => b[0]));
    const ms: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const sel = selected?.trade_id === t.trade_id;
      const long = t.direction === "LONG";
      if (have.has(sec(t.entry_time))) {
        ms.push({
          time: T(sec(t.entry_time)),
          position: long ? "belowBar" : "aboveBar",
          shape: long ? "arrowUp" : "arrowDown",
          color: long ? COLORS.up : COLORS.down,
          size: sel ? 1.6 : 1,
          text: sel ? `${daily ? "Buy" : long ? "Buy CE" : "Buy PE"} @${fmtPrice(t.entry_price)}` : "",
        });
      }
      // On a coarser timeframe the exit minute is not a bar start; snap to the bar that contains it.
      const ex = [...have].reduce((best, b) => (b <= sec(exitBar(t)) && b > best ? b : best), 0);
      if (ex) {
        ms.push({
          time: T(ex),
          position: long ? "aboveBar" : "belowBar",
          shape: "circle",
          color: t.net_pnl > 0 ? COLORS.up : COLORS.down,
          size: sel ? 1.4 : 0.8,
          text: sel ? `${t.exit_reason} ${compact(t.net_pnl)}` : "",
        });
      }
    }
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    cs.setMarkers(ms);
  }, [trades, selected, data, byBar]);

  // Zoom to the selected trade.
  useEffect(() => {
    const c = chart.current;
    if (!c || !selected || !data) return;
    const tfSec = (data.bars[1]?.[0] ?? 0) - (data.bars[0]?.[0] ?? 0) || 300;
    const entry = sec(selected.entry_time);
    const exit = sec(exitBar(selected));
    // Pad by a third of the trade's length (min 40 bars) so marker labels aren't clipped at the edges.
    const pad = Math.max(40 * tfSec, (exit - entry) / 3);
    c.timeScale().setVisibleRange({ from: T(entry - pad), to: T(exit + pad) });
  }, [selected, data]);

  const active = daily ? "1d" : (tf ?? data?.timeframe ?? "");
  const pickTf = (x: string) => {
    setTf(x);
    setFromDate(null);
    setData(null);
  };
  const toggle = (id: string) =>
    setHidden((h) => {
      const n = new Set(h);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const removeAdded = (s: ChartSeries) => setAdded((xs) => xs.filter((x) => !sameIndicator(s, x)));

  return (
    <section className={mx.max ? mx.frame : `flex flex-col rounded-lg border border-ink-800 bg-ink-900 ${fill ? "h-full min-h-[420px]" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-2.5">
        <div>
          <h3 className="text-sm font-semibold">
            {data?.underlying ?? "Underlying"} · {active} with trades
          </h3>
          <p className="text-[11px] text-ink-400">Click a marker or a row in the trade table to zoom in · scroll / drag to pan</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {daily && (
            <select
              value={symbol ?? data?.symbol ?? ""}
              onChange={(e) => {
                setSymbol(e.target.value);
                setFromDate(null);
                setData(null);
              }}
              aria-label="Stock"
              className="rounded-md border border-ink-700 bg-ink-850 px-2 py-1 text-[11px] text-ink-100"
            >
              {(data?.symbols ?? []).map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          )}
          <div role="group" aria-label="Timeframe" className={`flex overflow-hidden rounded-md border border-ink-700 ${daily ? "hidden" : ""}`}>
            {TIMEFRAMES.map((x) => (
              <button
                key={x}
                onClick={() => pickTf(x)}
                aria-pressed={active === x}
                className={`px-2 py-1 text-[11px] ${active === x ? "bg-accent text-ink-950" : "text-ink-300 hover:bg-ink-800"}`}
              >
                {x}
              </button>
            ))}
          </div>
          <button
            onClick={openPicker}
            className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-200 hover:bg-ink-800"
          >
            ƒ Indicators{added.length ? ` (${added.length})` : ""}
          </button>
          <MaximizeButton max={mx.max} onClick={mx.toggle} />
        </div>
        <ul className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-300" aria-label="Legend">
          <li className="flex items-center gap-1"><span style={{ color: COLORS.up }}>▲</span>{daily ? "Buy shares" : "Buy CE"}</li>
          {!daily && <li className="flex items-center gap-1"><span style={{ color: COLORS.down }}>▼</span>Buy PE</li>}
          <li className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS.up }} />/<span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS.down }} />Exit, profit / loss</li>
        </ul>
        {series.length > 0 && (
          <ul className="flex w-full flex-wrap items-center gap-1.5 pb-0.5 text-[11px]" aria-label="Indicators on the chart">
            {series.map((s) => (
              <li key={s.id} className={`flex items-center gap-1 rounded border px-1.5 py-0.5 ${hidden.has(s.id) ? "border-ink-800 text-ink-500" : "border-ink-700 text-ink-200"}`}>
                <button onClick={() => toggle(s.id)} className="flex items-center gap-1" aria-pressed={!hidden.has(s.id)} title={hidden.has(s.id) ? "Show" : "Hide"}>
                  <span className="inline-block h-0.5 w-3 rounded" style={{ background: colorOf.get(s.id) }} />
                  {s.label}
                  {s.source === "strategy" && <span className="text-[10px] text-ink-500">strategy</span>}
                </button>
                {s.source === "user" && (
                  <button onClick={() => removeAdded(s)} className="text-ink-500 hover:text-down" aria-label={`Remove ${s.label}`}>✕</button>
                )}
              </li>
            ))}
          </ul>
        )}
        {data && data.exact === false && series.some((s) => s.source === "strategy") && (
          <p className="w-full text-[11px] text-amber-300/90">
            Strategy indicators are drawn on {data.timeframe} candles; the strategy itself ran on {data.run_timeframe}. Switch back to {data.run_timeframe} to see the exact values it used.
          </p>
        )}
      </div>

      <div className="num flex min-h-[20px] flex-wrap gap-x-3 px-3 pt-1 text-[11px] text-ink-400">
        {hover ? (
          <>
            <span>{new Date(hover.t * 1000).toLocaleString("en-IN", daily
              ? { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" }
              : { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</span>
            <span>O <b className="font-normal text-ink-100">{fmtPrice(hover.o)}</b></span>
            <span>H <b className="font-normal text-ink-100">{fmtPrice(hover.h)}</b></span>
            <span>L <b className="font-normal text-ink-100">{fmtPrice(hover.l)}</b></span>
            <span>C <b className="font-normal text-ink-100">{fmtPrice(hover.c)}</b></span>
            {hover.values.map(([k, v]) => (
              <span key={k}>{k} <b className="font-normal text-ink-100">{fmtPrice(v)}</b></span>
            ))}
            {hover.trades.map((t) => (
              <span key={t.trade_id} className="text-ink-100">
                {sec(t.entry_time) === hover.t ? `Entry ${t.contract} @${fmtPrice(t.entry_price)}` : `Exit (${t.exit_reason}) ${compact(t.net_pnl)}`}
              </span>
            ))}
          </>
        ) : selected ? (
          <span className="text-ink-300">
            Selected: {selected.contract} · {selected.signal_reason}
          </span>
        ) : null}
      </div>

      <div className={`relative ${mx.max ? "min-h-0 flex-1" : fill ? "min-h-[320px] flex-1" : "h-[520px]"}`}>
        <div ref={box} className="absolute inset-0" />
        {!data && !error && <div className="absolute inset-0 grid place-items-center text-xs text-ink-400">Loading candles…</div>}
        {error && <div className="absolute inset-0 grid place-items-center text-xs text-down">{error}</div>}
      </div>
      {selected && (
        <div className="flex flex-wrap items-start justify-between gap-3 px-3 pb-2">
          {selected.checks?.length ? (
            <div className="text-[11px]">
              <p className="mb-0.5 text-ink-400">At the signal bar ({new Date(selected.signal_time).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false })}):</p>
              <ul className="space-y-0.5" aria-label="Entry conditions">
                {selected.checks.map(([text, ok], i) => (
                  <li key={i} className={ok ? "text-ink-200" : ok === false ? "text-down" : "text-ink-500"}>
                    <span aria-hidden="true">{ok ? "✓" : ok === false ? "✗" : "?"}</span> {text}
                  </li>
                ))}
              </ul>
            </div>
          ) : <span />}
          <button onClick={() => onSelect(null)} className="text-[11px] text-ink-400 hover:text-ink-100">Clear selection</button>
        </div>
      )}
      {picker && (
        <IndicatorPicker
          catalogue={catalogue ?? []}
          added={added}
          onChange={setAdded}
          onClose={() => setPicker(false)}
        />
      )}
    </section>
  );
}
