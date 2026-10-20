import { useEffect, useRef, useState } from "react";
import type { Quote } from "../api";
import { fmtPct, fmtPrice, fmtSigned, trendClass } from "../format";

interface Props {
  label: string;
  quote: Quote | null | undefined;
  active?: boolean;
  onClick?: () => void;
}

export function QuoteCard({ label, quote, active, onClick }: Props) {
  const prev = useRef<number | null>(null);
  const [flash, setFlash] = useState<"" | "flash-up" | "flash-down">("");

  useEffect(() => {
    if (!quote) return;
    if (prev.current != null && quote.ltp !== prev.current) {
      setFlash(quote.ltp > prev.current ? "flash-up" : "flash-down");
      const t = setTimeout(() => setFlash(""), 400);
      prev.current = quote.ltp;
      return () => clearTimeout(t);
    }
    prev.current = quote.ltp;
  }, [quote?.ltp]); // eslint-disable-line react-hooks/exhaustive-deps

  const lo = quote?.low ?? 0;
  const hi = quote?.high ?? 0;
  const pos = quote && hi > lo ? ((quote.ltp - lo) / (hi - lo)) * 100 : 50;
  const Tag = onClick ? "button" : "div";

  return (
    <Tag
      onClick={onClick}
      className={`group w-full rounded-lg border p-3.5 text-left transition-colors ${
        active ? "border-accent/60 bg-ink-850" : "border-ink-800 bg-ink-900 hover:border-ink-700"
      } ${onClick ? "cursor-pointer" : ""}`}
    >
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium tracking-wide text-ink-300">{label}</span>
        <span className={`num ${trendClass(quote?.change)}`}>{fmtPct(quote?.change_pct)}</span>
      </div>
      <div className={`num mt-1.5 text-2xl font-semibold tracking-tight ${flash}`}>
        {quote ? fmtPrice(quote.ltp) : <span className="text-ink-600">—</span>}
      </div>
      <div className={`num text-xs ${trendClass(quote?.change)}`}>
        {quote?.change != null ? (quote.change > 0 ? "▲ " : quote.change < 0 ? "▼ " : "") : ""}
        {fmtSigned(quote?.change)}
      </div>
      <div className="mt-3">
        <div className="relative h-1 rounded-full bg-ink-700">
          <div
            className="absolute top-1/2 h-2.5 w-0.5 -translate-y-1/2 rounded bg-ink-100"
            style={{ left: `calc(${Math.min(100, Math.max(0, pos))}% - 1px)` }}
          />
        </div>
        <div className="num mt-1 flex justify-between text-[10px] text-ink-400">
          <span>L {fmtPrice(quote?.low)}</span>
          <span>H {fmtPrice(quote?.high)}</span>
        </div>
      </div>
    </Tag>
  );
}
