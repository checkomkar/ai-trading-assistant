import { useEffect, useMemo, useRef, useState } from "react";
import type { IndicatorInfo } from "../../api";

/** An indicator the viewer added to the chart (the server computes it with the engine's own code). */
export interface AddedIndicator {
  name: string;
  period: number | null;
  mult: number | null;
  color: string;
}

export const PALETTE = ["#a78bfa", "#14b8a6", "#ec4899", "#84cc16", "#f97316", "#06b6d4", "#eab308", "#64748b", "#3987e5", "#d95926"];
export const MAX_ADDED = 10;

const norm = (s: string) => s.toLowerCase();

/** Every word of the query must appear in the name, label, category or an alias; names and exact aliases rank first. */
export function searchCatalogue(items: IndicatorInfo[], query: string): IndicatorInfo[] {
  const words = norm(query).split(/\s+/).filter(Boolean);
  if (!words.length) return items;
  const q = words.join(" ");
  return items
    .map((e, i) => {
      const hay = [e.name, e.label, e.category, ...e.aliases].map(norm).join(" ");
      if (!words.every((w) => hay.includes(w))) return null;
      const rank = norm(e.name) === q || e.aliases.map(norm).includes(q) ? 0 : norm(e.name).startsWith(q) || norm(e.label).startsWith(q) ? 1 : 2;
      return { e, rank, i };
    })
    .filter((x): x is { e: IndicatorInfo; rank: number; i: number } => x !== null)
    .sort((a, b) => a.rank - b.rank || a.i - b.i)
    .map((x) => x.e);
}

/** TradingView-style "Indicators" dialog: search the catalogue, add with default settings, adjust or remove. */
export function IndicatorPicker({
  catalogue,
  added,
  onChange,
  onClose,
}: {
  catalogue: IndicatorInfo[];
  added: AddedIndicator[];
  onChange: (next: AddedIndicator[]) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    input.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const info = useMemo(() => new Map(catalogue.map((e) => [e.name, e])), [catalogue]);
  const results = useMemo(() => searchCatalogue(catalogue, query), [catalogue, query]);
  const grouped = useMemo(() => {
    const m = new Map<string, IndicatorInfo[]>();
    for (const e of results) m.set(e.category, [...(m.get(e.category) ?? []), e]);
    return [...m.entries()];
  }, [results]);
  const full = added.length >= MAX_ADDED;

  const add = (e: IndicatorInfo) => {
    if (full) return;
    const used = new Set(added.map((a) => a.color));
    const color = PALETTE.find((c) => !used.has(c)) ?? PALETTE[added.length % PALETTE.length];
    onChange([...added, { name: e.name, period: e.period?.default ?? null, mult: e.mult?.default ?? null, color }]);
  };
  const patch = (i: number, p: Partial<AddedIndicator>) => onChange(added.map((a, j) => (j === i ? { ...a, ...p } : a)));
  const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="ind-title"
        className="flex max-h-[85dvh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink-800 px-4 py-3">
          <h2 id="ind-title" className="text-base font-semibold text-ink-100">Indicators</h2>
          <input
            ref={input}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search: ema, bollinger, supertrend, cpr…"
            aria-label="Search indicators"
            className="min-w-0 flex-1 rounded-md border border-ink-700 bg-ink-850 px-3 py-1.5 text-sm text-ink-100 placeholder:text-ink-500"
          />
          <button onClick={onClose} className="rounded-md px-2 py-1 text-sm text-ink-400 hover:bg-ink-800 hover:text-ink-100" aria-label="Close">✕</button>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 sm:grid-cols-[minmax(0,1fr)_240px]">
          <div className="min-h-0 overflow-y-auto px-2 py-2" aria-label="Search results">
            {!grouped.length && <p className="px-2 py-6 text-center text-sm text-ink-400">No indicator matches "{query}".</p>}
            {grouped.map(([cat, items]) => (
              <section key={cat} className="mb-2">
                <h3 className="px-2 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-ink-500">{cat}</h3>
                <ul>
                  {items.map((e) => (
                    <li key={e.name}>
                      <button
                        onClick={() => add(e)}
                        disabled={full}
                        className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-ink-100 hover:bg-ink-800 disabled:opacity-40"
                      >
                        <span className="min-w-0 flex-1 truncate">{e.label}</span>
                        <span className="shrink-0 text-[11px] text-ink-500">{e.pane === "price" ? "on chart" : e.pane}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>

          <aside className="min-h-0 overflow-y-auto border-t border-ink-800 px-3 py-2 sm:border-l sm:border-t-0" aria-label="Added indicators">
            <h3 className="pb-2 text-[11px] font-semibold uppercase tracking-wide text-ink-500">
              On this chart ({added.length}/{MAX_ADDED})
            </h3>
            {!added.length && <p className="text-xs text-ink-400">Pick an indicator on the left. Your strategy's own indicators are always shown.</p>}
            <ul className="space-y-2">
              {added.map((a, i) => {
                const e = info.get(a.name);
                return (
                  <li key={`${a.name}-${i}`} className="rounded-md border border-ink-800 p-2 text-xs">
                    <div className="flex items-center gap-2">
                      <input type="color" value={a.color} onChange={(ev) => patch(i, { color: ev.target.value })}
                        aria-label={`${a.name} colour`} className="h-4 w-4 cursor-pointer rounded border-0 bg-transparent p-0" />
                      <span className="min-w-0 flex-1 truncate text-ink-100">{e?.label.replace(/\(.*$/, "") ?? a.name}</span>
                      <button onClick={() => onChange(added.filter((_, j) => j !== i))} className="text-ink-400 hover:text-down" aria-label={`Remove ${a.name}`}>✕</button>
                    </div>
                    {(e?.period || e?.mult) && (
                      <div className="mt-1.5 flex gap-2">
                        {e?.period && (
                          <label className="flex items-center gap-1 text-ink-400">
                            Length
                            <input type="number" min={e.period.min} max={e.period.max} value={a.period ?? e.period.default}
                              onChange={(ev) => patch(i, { period: clamp(Number(ev.target.value) || e.period!.default, e.period!.min, e.period!.max) })}
                              className="num w-14 rounded border border-ink-700 bg-ink-850 px-1 py-0.5 text-right text-ink-100" />
                          </label>
                        )}
                        {e?.mult && (
                          <label className="flex items-center gap-1 text-ink-400">
                            Mult
                            <input type="number" step={0.1} min={e.mult.min} max={e.mult.max} value={a.mult ?? e.mult.default}
                              onChange={(ev) => patch(i, { mult: clamp(Number(ev.target.value) || e.mult!.default, e.mult!.min, e.mult!.max) })}
                              className="num w-14 rounded border border-ink-700 bg-ink-850 px-1 py-0.5 text-right text-ink-100" />
                          </label>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
            {added.length > 0 && (
              <button onClick={() => onChange([])} className="mt-3 text-[11px] text-ink-400 hover:text-ink-100">Remove all</button>
            )}
          </aside>
        </div>
      </div>
    </div>
  );
}
