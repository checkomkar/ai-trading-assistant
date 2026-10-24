import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { getJSON, type CandleData, type PaperPosition, type PaperTrade } from "../api";
import { fmtPrice } from "../format";
import { MaximizeButton, useMaximize } from "../ui/maximize";

// Lightweight Charts renders in UTC; shifting by +5:30 makes the axis read IST.
const IST_OFFSET = 19_800;
const toTime = (iso: string) => (Math.floor(new Date(iso).getTime() / 1000) + IST_OFFSET) as UTCTimestamp;
const TIMEFRAMES = ["1m", "5m"];
const todayIST = () => new Date(Date.now() + IST_OFFSET * 1000).toISOString().slice(0, 10);

interface Props {
  trades: PaperTrade[];
  positions: PaperPosition[];
  day: string;
  onDay: (d: string) => void;
}

/** Index candles for a trading day with every paper entry and exit marked. Option prices are in the labels. */
export function PaperChart({ trades, positions, day, onDay }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [underlying, setUnderlying] = useState("NIFTY");
  const [timeframe, setTimeframe] = useState("1m");
  const [candles, setCandles] = useState<CandleData[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa7b8", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
      grid: { vertLines: { color: "#1a202b" }, horzLines: { color: "#1a202b" } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: "#252d3a" },
      timeScale: { borderColor: "#252d3a", timeVisible: true, secondsVisible: false, rightOffset: 4 },
    });
    series.current = c.addCandlestickSeries({
      upColor: "#22c55e", downColor: "#f43f5e", borderVisible: false, wickUpColor: "#22c55e", wickDownColor: "#f43f5e",
    });
    chart.current = c;
    return () => {
      c.remove();
      chart.current = null;
      series.current = null;
    };
  }, []);

  const shownDay = day || todayIST();
  const isToday = shownDay === todayIST();
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      getJSON<{ candles: CandleData[] }>(`/market/candles?symbol=${underlying}&timeframe=${timeframe}&limit=5000&day=${shownDay}`)
        .then((r) => {
          if (cancelled) return;
          setCandles(r.candles);
          setError(null);
        })
        .catch((e) => !cancelled && setError((e as Error).message));
    load();
    const t = isToday ? setInterval(load, 10_000) : undefined;
    return () => {
      cancelled = true;
      if (t) clearInterval(t);
    };
  }, [underlying, timeframe, shownDay, isToday]);

  const mine = trades.filter((t) => t.underlying === underlying && t.trading_day === shownDay);
  const open = positions.filter((p) => p.underlying === underlying && p.entry_time && isToday);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    s.setData(candles.map((r) => ({ time: toTime(r.ts), open: r.open, high: r.high, low: r.low, close: r.close })));
    // Snap each marker to the candle that contains it: a marker on a time with no candle is dropped.
    const step = timeframe === "5m" ? 300 : 60;
    const snap = (iso: string) => (Math.floor(toTime(iso) / step) * step) as UTCTimestamp;
    const markers: SeriesMarker<Time>[] = [];
    for (const t of mine) {
      const ce = t.direction === "LONG";
      markers.push({
        time: snap(t.entry_time), position: ce ? "belowBar" : "aboveBar", shape: ce ? "arrowUp" : "arrowDown",
        color: "#3987e5", text: `${ce ? "CE" : "PE"} in ${fmtPrice(t.entry_price)}`,
      });
      markers.push({
        time: snap(t.exit_time), position: ce ? "aboveBar" : "belowBar", shape: "circle",
        color: t.net_pnl >= 0 ? "#22c55e" : "#f43f5e",
        text: `out ${fmtPrice(t.exit_price)} · ${t.exit_reason} · ${t.net_pnl >= 0 ? "+" : "−"}₹${fmtPrice(Math.abs(t.net_pnl))}`,
      });
    }
    for (const p of open) {
      const ce = p.direction === "LONG";
      markers.push({
        time: snap(p.entry_time as string), position: ce ? "belowBar" : "aboveBar", shape: ce ? "arrowUp" : "arrowDown",
        color: "#e5a539", text: `OPEN ${ce ? "CE" : "PE"} ${p.entry_price != null ? fmtPrice(p.entry_price) : ""}`,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    s.setMarkers(markers);
    chart.current?.timeScale().fitContent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, timeframe, JSON.stringify(mine.map((t) => t.id)), JSON.stringify(open.map((p) => [p.correlation_id, p.entry_time]))]);

  const mx = useMaximize();
  return (
    <section className={mx.max ? mx.frame : "rounded-lg border border-ink-800 bg-ink-900"}>
      <div className="flex flex-wrap items-center gap-3 border-b border-ink-800 px-3 py-2">
        <h2 className="mr-1 text-sm font-semibold">Chart</h2>
        <div className="flex gap-1">
          {["NIFTY", "BANKNIFTY"].map((u) => (
            <button key={u} onClick={() => setUnderlying(u)}
              className={`rounded px-2 py-0.5 text-xs ${underlying === u ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}>{u}</button>
          ))}
        </div>
        <div className="flex gap-1">
          {TIMEFRAMES.map((tf) => (
            <button key={tf} onClick={() => setTimeframe(tf)}
              className={`rounded px-2 py-0.5 text-xs ${timeframe === tf ? "bg-accent text-ink-950" : "text-ink-400 hover:text-ink-100"}`}>{tf}</button>
          ))}
        </div>
        <input type="date" value={shownDay} onChange={(e) => onDay(e.target.value)}
          className="rounded border border-ink-700 bg-ink-850 px-2 py-0.5 text-xs text-ink-100" />
        <span className="num ml-auto text-[11px] text-ink-400">
          {mine.length} trade{mine.length === 1 ? "" : "s"}{open.length ? ` · ${open.length} open` : ""} · {underlying} index, option prices in labels
        </span>
        <MaximizeButton max={mx.max} onClick={mx.toggle} />
      </div>
      {error && <p className="px-3 py-2 text-xs text-down" role="alert">{error}</p>}
      <div className={`relative ${mx.max ? "min-h-0 flex-1" : "h-[360px]"}`}>
        <div ref={box} className="absolute inset-0" />
        {!error && candles.length === 0 && (
          <div className="absolute inset-0 grid place-items-center text-xs text-ink-400">No stored candles for {shownDay}.</div>
        )}
      </div>
    </section>
  );
}
