import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  LineType,
  createChart,
  type IChartApi,
  type MouseEventParams,
  type UTCTimestamp,
} from "lightweight-charts";
import { MaximizeButton, useMaximize } from "../../ui/maximize";
import type { BacktestMetrics } from "../../api";
import { fmtPrice } from "../../format";

const IST_OFFSET = 19_800;
const toTime = (iso: string) => (Math.floor(new Date(iso).getTime() / 1000) + IST_OFFSET) as UTCTimestamp;
const dayTime = (d: string) => (Math.floor(Date.parse(d + "T00:00:00Z") / 1000)) as UTCTimestamp;

const BASE = {
  autoSize: true,
  layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: "#9aa7b8", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 },
  grid: { vertLines: { visible: false }, horzLines: { color: "#1a202b" } },
  crosshair: { mode: CrosshairMode.Magnet },
  rightPriceScale: { borderVisible: false },
  // a panel opening or folding keeps the same dates in view instead of leaving a blank gap
  timeScale: { borderVisible: false, lockVisibleTimeRangeOnResize: true },
  // drag to pan; zoom with the buttons (or the mouse wheel in full screen, where it cannot fight page scrolling)
  handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
  handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: true },
} as const;

interface Tip {
  x: number;
  y: number;
  lines: [string, string][];
}

function useChart(build: (c: IChartApi) => void, deps: unknown[]) {
  const box = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip | null>(null);
  const tipRef = useRef(setTip);
  const chart = useRef<IChartApi | null>(null);
  useEffect(() => {
    if (!box.current) return;
    const c = createChart(box.current, BASE);
    build(c);
    c.timeScale().fitContent();
    chart.current = c;
    return () => {
      chart.current = null;
      c.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { box, tip, setTip: tipRef.current, chart };
}

/** Zoom the time axis around the middle of what is shown: factor < 1 zooms in. */
function zoom(c: IChartApi | null, factor: number) {
  const r = c?.timeScale().getVisibleLogicalRange();
  if (!c || !r) return;
  const mid = (r.from + r.to) / 2;
  const half = Math.max(3, ((r.to - r.from) / 2) * factor);
  const from = Math.max(mid - half, -1); // never scroll into blank space before the first point
  c.timeScale().setVisibleLogicalRange({ from, to: from + 2 * half });
}

function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  return (
    <div
      className="pointer-events-none absolute z-10 rounded-md border border-ink-700 bg-ink-850/95 px-2.5 py-1.5 text-[11px] shadow-lg"
      style={{ left: Math.max(4, tip.x + 14), top: Math.max(4, tip.y - 10) }}
    >
      {tip.lines.map(([k, v]) => (
        <div key={k} className="flex justify-between gap-4">
          <span className="text-ink-400">{k}</span>
          <span className="num text-ink-100">{v}</span>
        </div>
      ))}
    </div>
  );
}

export function EquityChart({ metrics, capital }: { metrics: BacktestMetrics; capital: number }) {
  const pts = metrics.equity_curve.filter((p) => p.ts);
  const { box, tip, setTip, chart } = useChart(
    (c) => {
      const s = c.addAreaSeries({
        lineColor: "#f5b301",
        lineWidth: 2,
        lineType: LineType.WithSteps,
        topColor: "rgba(245,179,1,0.10)",
        bottomColor: "rgba(245,179,1,0.0)",
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        crosshairMarkerBorderColor: "#0f131a",
        crosshairMarkerBorderWidth: 2,
      });
      const seen = new Map<number, { equity: number; drawdown: number }>();
      for (const p of pts) seen.set(toTime(p.ts!), { equity: p.equity, drawdown: p.drawdown ?? 0 });
      s.setData([...seen.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ time: t as UTCTimestamp, value: v.equity })));
      s.createPriceLine({ price: capital, color: "#344052", lineWidth: 1, lineStyle: 0, axisLabelVisible: false, title: "" });
      c.subscribeCrosshairMove((e: MouseEventParams) => {
        const v = e.time != null ? seen.get(e.time as number) : undefined;
        if (!e.point || !v) return setTip(null);
        const d = new Date(((e.time as number) - IST_OFFSET) * 1000);
        setTip({
          x: e.point.x,
          y: e.point.y,
          lines: [
            ["Time", d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })],
            ["Equity", `₹${fmtPrice(v.equity)}`],
            ["Drawdown", `₹${fmtPrice(v.drawdown)}`],
          ],
        });
      });
    },
    [metrics, capital],
  );
  return (
    <ChartFrame title="Equity curve" subtitle={`Starting capital ₹${fmtPrice(capital)} · after costs`} chart={chart}>
      <div ref={box} className="absolute inset-0" />
      <Tooltip tip={tip} />
      {pts.length === 0 && <Empty />}
    </ChartFrame>
  );
}

export function DailyPnlChart({ metrics }: { metrics: BacktestMetrics }) {
  const { box, tip, setTip, chart } = useChart(
    (c) => {
      const s = c.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false, base: 0 });
      const byTime = new Map<number, number>();
      s.setData(
        metrics.daily_pnl.map((d) => {
          byTime.set(dayTime(d.date), d.pnl);
          return { time: dayTime(d.date), value: d.pnl, color: d.pnl >= 0 ? "#22c55e" : "#f43f5e" };
        }),
      );
      c.subscribeCrosshairMove((e: MouseEventParams) => {
        const v = e.time != null ? byTime.get(e.time as number) : undefined;
        if (!e.point || v === undefined) return setTip(null);
        const d = new Date((e.time as number) * 1000).toISOString().slice(0, 10);
        setTip({ x: e.point.x, y: e.point.y, lines: [["Day", d], ["Net P&L", `${v >= 0 ? "+" : "−"}₹${fmtPrice(Math.abs(v))}`]] });
      });
    },
    [metrics],
  );
  return (
    <ChartFrame title="Daily P&L" subtitle="Bars above zero are profitable days · no-trade days shown as zero" chart={chart}>
      <div ref={box} className="absolute inset-0" />
      <Tooltip tip={tip} />
    </ChartFrame>
  );
}

function ChartFrame({ title, subtitle, chart, children }: { title: string; subtitle: string; chart: React.RefObject<IChartApi | null>; children: React.ReactNode }) {
  const mx = useMaximize();
  useEffect(() => {
    chart.current?.applyOptions({ handleScale: { mouseWheel: mx.max }, handleScroll: { mouseWheel: mx.max } });
  }, [mx.max, chart]);
  return (
    <section className={mx.max ? mx.frame : "rounded-lg border border-ink-800 bg-ink-900"}>
      <div className="flex items-start gap-2 px-3 pt-2.5">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-semibold">{title}</h3>
          <p className="text-[11px] text-ink-400">{subtitle}</p>
        </div>
        <ZoomButtons onIn={() => zoom(chart.current, 0.6)} onOut={() => zoom(chart.current, 1.6)} onReset={() => chart.current?.timeScale().fitContent()} />
        <MaximizeButton max={mx.max} onClick={mx.toggle} />
      </div>
      <div className={`relative ${mx.max ? "min-h-0 flex-1" : "h-56"}`}>{children}</div>
    </section>
  );
}

function Empty() {
  return <div className="absolute inset-0 grid place-items-center text-xs text-ink-400">No trades in this period</div>;
}

export function ZoomButtons({ onIn, onOut, onReset }: { onIn: () => void; onOut: () => void; onReset: () => void }) {
  const b = "grid h-7 w-7 place-items-center text-ink-300 hover:bg-ink-800 hover:text-ink-100";
  return (
    <div role="group" aria-label="Zoom" className="flex overflow-hidden rounded-md border border-ink-700">
      <button type="button" onClick={onIn} className={b} title="Zoom in" aria-label="Zoom in">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3M8 11h6M11 8v6" /></svg>
      </button>
      <button type="button" onClick={onOut} className={`${b} border-l border-ink-700`} title="Zoom out" aria-label="Zoom out">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.3-4.3M8 11h6" /></svg>
      </button>
      <button type="button" onClick={onReset} className={`${b} border-l border-ink-700 px-2 text-[10px] font-medium`} style={{ width: "auto" }} title="Show everything (or double-click the time axis)">
        All
      </button>
    </div>
  );
}
