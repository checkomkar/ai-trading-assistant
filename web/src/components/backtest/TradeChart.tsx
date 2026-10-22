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
import { getJSON, type BacktestTrade } from "../../api";
import { fmtPrice } from "../../format";
import { bollinger, ema, macd, rsi, sma, type Bar, type Pt } from "./indicators";

// Lightweight Charts renders UTC; shift by +5:30 so the axis reads IST.
const IST = 19_800;
const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
const T = (utcSec: number) => (utcSec + IST) as UTCTimestamp;

const COLORS = { up: "#22c55e", down: "#f43f5e", emaFast: "#3987e5", emaSlow: "#d95926", sma: "#a78bfa", bb: "#64748b", rsi: "#eab308", macd: "#3987e5", signal: "#d95926" };

const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "60m"] as const;
// Trading days of history requested per timeframe, so a 1m chart does not pull a year of minutes.
const DAYS: Record<string, number> = { "1m": 8, "3m": 25, "5m": 60, "15m": 150, "30m": 250, "60m": 250, "1d": 400 };
// Bars shown on first paint.
const VISIBLE: Record<string, number> = { "1m": 375, "3m": 250, "5m": 230, "15m": 200, "30m": 150, "60m": 120, "1d": 160 };

interface ChartData {
  underlying: string;
  timeframe: string;
  bars: Bar[];
  symbol?: string;
  symbols?: string[];
}

interface Settings {
  emaA: { on: boolean; period: number };
  emaB: { on: boolean; period: number };
  sma: { on: boolean; period: number };
  bb: { on: boolean; period: number };
  rsi: { on: boolean; period: number };
  macd: { on: boolean };
}

const DEFAULTS: Settings = {
  emaA: { on: true, period: 9 },
  emaB: { on: true, period: 21 },
  sma: { on: false, period: 50 },
  bb: { on: false, period: 20 },
  rsi: { on: false, period: 14 },
  macd: { on: false },
};

function loadSettings(): Settings {
  try {
    return { ...DEFAULTS, ...JSON.parse(localStorage.getItem("trade-chart-indicators") ?? "{}") };
  } catch {
    return DEFAULTS;
  }
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

export function TradeChart({
  runId,
  trades: allTrades,
  selected,
  onSelect,
  daily = false,
}: {
  runId: string;
  trades: BacktestTrade[];
  selected: BacktestTrade | null;
  onSelect: (t: BacktestTrade | null) => void;
  daily?: boolean; // a stock run: daily candles of one stock at a time
}) {
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
  const [cfg, setCfg] = useState<Settings>(loadSettings);
  const [menu, setMenu] = useState(false);

  useEffect(() => {
    try {
      localStorage.setItem("trade-chart-indicators", JSON.stringify(cfg));
    } catch {
      /* storage unavailable: settings just won't persist */
    }
  }, [cfg]);

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

  useEffect(() => {
    let alive = true;
    setError(null);
    const q = new URLSearchParams();
    if (tf && !daily) q.set("timeframe", tf);
    if (daily && symbol) q.set("symbol", symbol);
    if (fromDate) q.set("from_date", fromDate);
    else if (!daily) q.set("last_days", String(DAYS[tf ?? "5m"] ?? 60));
    getJSON<ChartData>(`/backtests/${runId}/chart?${q}`)
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [runId, tf, fromDate, symbol, daily]);

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

  // Indicators: overlays share the price scale; RSI / MACD get their own band at the bottom.
  useEffect(() => {
    const c = chart.current;
    if (!c || !data) return;
    const added: ISeriesApi<"Line" | "Histogram">[] = [];
    const vals = new Map<string, Map<number, number>>();
    const line = (name: string, color: string, pts: Pt[], opts: { scale?: string; width?: 1 | 2; dashed?: boolean } = {}) => {
      const s = c.addLineSeries({
        color, lineWidth: opts.width ?? 2, lineStyle: opts.dashed ? 2 : 0, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false, priceScaleId: opts.scale, title: "",
      });
      s.setData(pts.map(([t, v]) => ({ time: T(t), value: v })));
      added.push(s);
      vals.set(name, new Map(pts));
      return s;
    };
    if (cfg.emaA.on) line(`EMA${cfg.emaA.period}`, COLORS.emaFast, ema(data.bars, cfg.emaA.period));
    if (cfg.emaB.on) line(`EMA${cfg.emaB.period}`, COLORS.emaSlow, ema(data.bars, cfg.emaB.period));
    if (cfg.sma.on) line(`SMA${cfg.sma.period}`, COLORS.sma, sma(data.bars, cfg.sma.period));
    if (cfg.bb.on) {
      const b = bollinger(data.bars, cfg.bb.period, 2);
      line(`BB↑${cfg.bb.period}`, COLORS.bb, b.upper, { width: 1 });
      line(`BB mid`, COLORS.bb, b.mid, { width: 1, dashed: true });
      line(`BB↓${cfg.bb.period}`, COLORS.bb, b.lower, { width: 1 });
    }
    const subs = [cfg.rsi.on, cfg.macd.on].filter(Boolean).length;
    const band = (i: number) => ({ top: 1 - 0.2 * (subs - i) + 0.02, bottom: 0.2 * (subs - i - 1) + 0.02 });
    c.priceScale("right").applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 + 0.2 * subs } });
    let slot = 0;
    if (cfg.rsi.on) {
      const m = band(slot++);
      const s = line(`RSI${cfg.rsi.period}`, COLORS.rsi, rsi(data.bars, cfg.rsi.period), { scale: "rsi", width: 1 });
      c.priceScale("rsi").applyOptions({ scaleMargins: m, borderVisible: false });
      s.createPriceLine({ price: 70, color: "#475569", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
      s.createPriceLine({ price: 30, color: "#475569", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
    }
    if (cfg.macd.on) {
      const m = band(slot++);
      const r = macd(data.bars);
      const h = c.addHistogramSeries({ priceScaleId: "macd", priceLineVisible: false, lastValueVisible: false });
      h.setData(r.hist.map(([t, v]) => ({ time: T(t), value: v, color: v >= 0 ? "#22c55e80" : "#f43f5e80" })));
      added.push(h);
      vals.set("MACD hist", new Map(r.hist));
      line("MACD", COLORS.macd, r.macd, { scale: "macd", width: 1 });
      line("MACD sig", COLORS.signal, r.signal, { scale: "macd", width: 1 });
      c.priceScale("macd").applyOptions({ scaleMargins: m, borderVisible: false });
    }
    values.current = vals;
    return () => {
      // When the dataset changed the chart itself is already disposed (its cleanup ran first), taking the series with it.
      if (chart.current === c) for (const s of added) c.removeSeries(s);
      values.current = new Map();
    };
  }, [data, cfg, byBar]);

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
  const toggle = <K extends keyof Settings>(k: K, patch: Partial<Settings[K]>) => setCfg((s) => ({ ...s, [k]: { ...s[k], ...patch } }));
  const pickTf = (x: string) => {
    setTf(x);
    setFromDate(null);
    setData(null);
  };

  const row = (label: string, k: keyof Settings, period: boolean, color: string) => {
    const s = cfg[k] as { on: boolean; period?: number };
    return (
      <label key={k} className="flex items-center gap-2 py-0.5">
        <input type="checkbox" checked={s.on} onChange={(e) => toggle(k, { on: e.target.checked } as never)} />
        <span className="h-0.5 w-3 rounded" style={{ background: color }} />
        <span className="w-24 text-ink-200">{label}</span>
        {period && (
          <input
            type="number" min={2} max={200} value={s.period}
            onChange={(e) => toggle(k, { period: Math.min(200, Math.max(2, Number(e.target.value) || 2)) } as never)}
            className="num w-14 rounded border border-ink-700 bg-ink-850 px-1 py-0.5 text-right text-ink-100"
            aria-label={`${label} period`}
          />
        )}
      </label>
    );
  };

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
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
          <div className="relative">
            <button
              onClick={() => setMenu((m) => !m)}
              aria-expanded={menu}
              className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-200 hover:bg-ink-800"
            >
              Indicators ▾
            </button>
            {menu && (
              <div className="absolute right-0 z-20 mt-1 w-60 rounded-md border border-ink-700 bg-ink-850 p-2 text-[11px] shadow-lg">
                {row("EMA", "emaA", true, COLORS.emaFast)}
                {row("EMA", "emaB", true, COLORS.emaSlow)}
                {row("SMA", "sma", true, COLORS.sma)}
                {row("Bollinger (2σ)", "bb", true, COLORS.bb)}
                {row("RSI", "rsi", true, COLORS.rsi)}
                {row("MACD (12,26,9)", "macd", false, COLORS.macd)}
                <button onClick={() => setCfg(DEFAULTS)} className="mt-1 text-ink-400 hover:text-ink-100">Reset</button>
              </div>
            )}
          </div>
        </div>
        <ul className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-300" aria-label="Legend">
          <li className="flex items-center gap-1"><span style={{ color: COLORS.up }}>▲</span>{daily ? "Buy shares" : "Buy CE"}</li>
          {!daily && <li className="flex items-center gap-1"><span style={{ color: COLORS.down }}>▼</span>Buy PE</li>}
          <li className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS.up }} />/<span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS.down }} />Exit, profit / loss</li>
        </ul>
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

      <div className="relative h-[480px]">
        <div ref={box} className="absolute inset-0" />
        {!data && !error && <div className="absolute inset-0 grid place-items-center text-xs text-ink-400">Loading candles…</div>}
        {error && <div className="absolute inset-0 grid place-items-center text-xs text-down">{error}</div>}
      </div>
      {selected && (
        <div className="flex justify-end px-3 pb-2">
          <button onClick={() => onSelect(null)} className="text-[11px] text-ink-400 hover:text-ink-100">Clear selection</button>
        </div>
      )}
    </section>
  );
}
