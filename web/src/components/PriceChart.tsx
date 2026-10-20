import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import { getJSON, type CandleData, type CandleMessage } from "../api";

// Lightweight Charts renders in UTC; shifting by +5:30 makes the axis read IST.
const IST_OFFSET = 19_800;
const toTime = (iso: string) => (Math.floor(new Date(iso).getTime() / 1000) + IST_OFFSET) as UTCTimestamp;

const SYMBOLS = [
  { id: "NIFTY", label: "NIFTY" },
  { id: "BANKNIFTY", label: "BANKNIFTY" },
  { id: "NIFTY-FUT", label: "NIFTY FUT" },
  { id: "BANKNIFTY-FUT", label: "BANKNIFTY FUT" },
  { id: "INDIAVIX", label: "VIX" },
];
const TIMEFRAMES = ["1m", "5m"];

interface Props {
  symbol: string;
  timeframe: string;
  onSymbol: (s: string) => void;
  onTimeframe: (tf: string) => void;
  liveCandle: CandleMessage | null;
}

export function PriceChart({ symbol, timeframe, onSymbol, onTimeframe, liveCandle }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volume = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastTime = useRef<number>(0);
  const [error, setError] = useState<string | null>(null);
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#9aa7b8",
        fontFamily: "IBM Plex Mono, monospace",
        fontSize: 11,
      },
      grid: { vertLines: { color: "#1a202b" }, horzLines: { color: "#1a202b" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#252d3a" },
      timeScale: { borderColor: "#252d3a", timeVisible: true, secondsVisible: false, rightOffset: 4 },
    });
    candles.current = c.addCandlestickSeries({
      upColor: "#22c55e",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#22c55e",
      wickDownColor: "#f43f5e",
    });
    volume.current = c.addHistogramSeries({
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    c.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    getJSON<{ candles: CandleData[] }>(`/market/candles?symbol=${symbol}&timeframe=${timeframe}&limit=500`)
      .then(({ candles: rows }) => {
        if (cancelled || !candles.current || !volume.current) return;
        candles.current.setData(rows.map((r) => ({ time: toTime(r.ts), open: r.open, high: r.high, low: r.low, close: r.close })));
        volume.current.setData(
          rows.map((r) => ({
            time: toTime(r.ts),
            value: r.volume,
            color: r.close >= r.open ? "rgba(34,197,94,0.35)" : "rgba(244,63,94,0.35)",
          })),
        );
        lastTime.current = rows.length ? toTime(rows[rows.length - 1].ts) : 0;
        setCount(rows.length);
        chart.current?.timeScale().scrollToRealTime();
      })
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [symbol, timeframe]);

  useEffect(() => {
    if (!liveCandle || liveCandle.timeframe !== timeframe || liveCandle.symbol?.toUpperCase() !== symbol) return;
    const k = liveCandle.candle;
    const t = toTime(k.ts);
    if (t < lastTime.current) return; // stale frame from before a symbol switch
    lastTime.current = t;
    candles.current?.update({ time: t, open: k.open, high: k.high, low: k.low, close: k.close });
    volume.current?.update({
      time: t,
      value: k.volume,
      color: k.close >= k.open ? "rgba(34,197,94,0.35)" : "rgba(244,63,94,0.35)",
    });
  }, [liveCandle, symbol, timeframe]);

  return (
    <section className="flex min-h-[380px] flex-col rounded-lg border border-ink-800 bg-ink-900">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {SYMBOLS.map((s) => (
            <button
              key={s.id}
              onClick={() => onSymbol(s.id)}
              className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                symbol === s.id ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"
              }`}
            >
              {s.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-1 rounded-md bg-ink-850 p-0.5">
          {TIMEFRAMES.map((tf) => (
            <button
              key={tf}
              onClick={() => onTimeframe(tf)}
              className={`num rounded px-2 py-0.5 text-xs ${timeframe === tf ? "bg-accent text-ink-950 font-semibold" : "text-ink-300 hover:text-ink-100"}`}
            >
              {tf}
            </button>
          ))}
        </div>
      </div>
      <div className="relative flex-1">
        <div ref={box} className="absolute inset-0" />
        {error && <div className="absolute inset-0 grid place-items-center text-sm text-down">{error}</div>}
        {!error && count <= 1 && (
          <div className="pointer-events-none absolute left-3 top-3 text-xs text-ink-400">
            Building candles from live ticks…
          </div>
        )}
      </div>
    </section>
  );
}
