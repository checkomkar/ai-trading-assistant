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

// Lightweight Charts renders UTC; shift by +5:30 so the axis reads IST.
const IST = 19_800;
const sec = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
const T = (utcSec: number) => (utcSec + IST) as UTCTimestamp;

const COLORS = { up: "#22c55e", down: "#f43f5e", emaFast: "#3987e5", emaSlow: "#d95926" };

interface ChartData {
  underlying: string;
  timeframe: string;
  bars: [number, number, number, number, number][];
  ema_fast: { period: number; points: [number, number][] };
  ema_slow: { period: number; points: [number, number][] };
}

interface Hover {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  fast?: number;
  slow?: number;
  trades: BacktestTrade[];
}

const exitBar = (t: BacktestTrade) => t.exit_bar_time ?? t.exit_time;

function compact(v: number) {
  const a = Math.abs(v);
  const s = a >= 1e5 ? `${(a / 1e5).toFixed(1)}L` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}k` : a.toFixed(0);
  return `${v < 0 ? "−" : "+"}₹${s}`;
}

export function TradeChart({
  runId,
  trades,
  selected,
  onSelect,
}: {
  runId: string;
  trades: BacktestTrade[];
  selected: BacktestTrade | null;
  onSelect: (t: BacktestTrade | null) => void;
}) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [data, setData] = useState<ChartData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);

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
    setData(null);
    setError(null);
    getJSON<ChartData>(`/backtests/${runId}/chart`)
      .then((d) => alive && setData(d))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [runId]);

  // Build the chart once per dataset.
  useEffect(() => {
    if (!box.current || !data) return;
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa7b8", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: "#161b24" }, horzLines: { color: "#161b24" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 3 },
    });
    const cs = c.addCandlestickSeries({
      upColor: COLORS.up, downColor: COLORS.down, borderVisible: false, wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    cs.setData(data.bars.map(([t, o, h, l, cl]) => ({ time: T(t), open: o, high: h, low: l, close: cl })));
    const line = (color: string, pts: [number, number][]) => {
      const s = c.addLineSeries({ color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
      s.setData(pts.map(([t, v]) => ({ time: T(t), value: v })));
    };
    line(COLORS.emaFast, data.ema_fast.points);
    line(COLORS.emaSlow, data.ema_slow.points);

    const fast = new Map(data.ema_fast.points);
    const slow = new Map(data.ema_slow.points);
    const bars = new Map(data.bars.map((b) => [b[0], b]));
    c.subscribeCrosshairMove((e: MouseEventParams) => {
      if (e.time == null) return setHover(null);
      const t = (e.time as number) - IST;
      const b = bars.get(t);
      if (!b) return setHover(null);
      setHover({ t, o: b[1], h: b[2], l: b[3], c: b[4], fast: fast.get(t), slow: slow.get(t), trades: byBar.get(t) ?? [] });
    });
    c.subscribeClick((e: MouseEventParams) => {
      if (e.time == null) return;
      const hit = byBar.get((e.time as number) - IST);
      if (hit?.length) onSelect(hit[0]);
    });
    // Default view: the last ~3 sessions.
    const n = data.bars.length;
    if (n) c.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 230), to: n + 3 });
    chart.current = c;
    candles.current = cs;
    return () => {
      c.remove();
      chart.current = null;
      candles.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, byBar]);

  // Markers: all trades, labelled only for the selected one (labels on every trade are unreadable).
  useEffect(() => {
    const cs = candles.current;
    if (!cs || !data) return;
    const ms: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const sel = selected?.trade_id === t.trade_id;
      const long = t.direction === "LONG";
      ms.push({
        time: T(sec(t.entry_time)),
        position: long ? "belowBar" : "aboveBar",
        shape: long ? "arrowUp" : "arrowDown",
        color: long ? COLORS.up : COLORS.down,
        size: sel ? 1.6 : 1,
        text: sel ? `${long ? "Buy CE" : "Buy PE"} @${fmtPrice(t.entry_price)}` : "",
      });
      ms.push({
        time: T(sec(exitBar(t))),
        position: long ? "aboveBar" : "belowBar",
        shape: "circle",
        color: t.net_pnl > 0 ? COLORS.up : COLORS.down,
        size: sel ? 1.4 : 0.8,
        text: sel ? `${t.exit_reason} ${compact(t.net_pnl)}` : "",
      });
    }
    ms.sort((a, b) => (a.time as number) - (b.time as number));
    cs.setMarkers(ms);
  }, [trades, selected, data]);

  // Zoom to the selected trade.
  useEffect(() => {
    const c = chart.current;
    if (!c || !selected || !data) return;
    const tf = (data.bars[1]?.[0] ?? 0) - (data.bars[0]?.[0] ?? 0) || 300;
    const entry = sec(selected.entry_time);
    const exit = sec(exitBar(selected));
    // Pad by a third of the trade's length (min 40 bars) so marker labels aren't clipped at the edges.
    const pad = Math.max(40 * tf, (exit - entry) / 3);
    c.timeScale().setVisibleRange({ from: T(entry - pad), to: T(exit + pad) });
  }, [selected, data]);

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 pt-2.5">
        <div>
          <h3 className="text-sm font-semibold">
            {data?.underlying ?? "Underlying"} · {data?.timeframe ?? ""} with trades
          </h3>
          <p className="text-[11px] text-ink-400">Click a marker or a row in the trade table to zoom in · scroll / drag to pan</p>
        </div>
        <ul className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-300" aria-label="Legend">
          <li className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: COLORS.emaFast }} />EMA {data?.ema_fast.period ?? 9}</li>
          <li className="flex items-center gap-1.5"><span className="h-0.5 w-4 rounded" style={{ background: COLORS.emaSlow }} />EMA {data?.ema_slow.period ?? 21}</li>
          <li className="flex items-center gap-1"><span style={{ color: COLORS.up }}>▲</span>Buy CE</li>
          <li className="flex items-center gap-1"><span style={{ color: COLORS.down }}>▼</span>Buy PE</li>
          <li className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS.up }} />/<span className="inline-block h-2 w-2 rounded-full" style={{ background: COLORS.down }} />Exit, profit / loss</li>
        </ul>
      </div>

      <div className="num flex min-h-[20px] flex-wrap gap-x-3 px-3 pt-1 text-[11px] text-ink-400">
        {hover ? (
          <>
            <span>{new Date(hover.t * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })}</span>
            <span>O <b className="font-normal text-ink-100">{fmtPrice(hover.o)}</b></span>
            <span>H <b className="font-normal text-ink-100">{fmtPrice(hover.h)}</b></span>
            <span>L <b className="font-normal text-ink-100">{fmtPrice(hover.l)}</b></span>
            <span>C <b className="font-normal text-ink-100">{fmtPrice(hover.c)}</b></span>
            {hover.fast != null && <span>EMA{data?.ema_fast.period} <b className="font-normal text-ink-100">{fmtPrice(hover.fast)}</b></span>}
            {hover.slow != null && <span>EMA{data?.ema_slow.period} <b className="font-normal text-ink-100">{fmtPrice(hover.slow)}</b></span>}
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

      <div className="relative h-[420px]">
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
