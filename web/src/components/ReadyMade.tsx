import { useEffect, useState } from "react";
import { getJSON } from "../api";

interface Entry {
  name: string;
  category: string;
  description: string;
  timeframe: string;
  spec: Record<string, unknown>;
}

const NOTE: Record<string, string> = {
  Momentum: "Ride a move that is already under way.",
  Trend: "Join an established trend on a pullback.",
  Scalp: "Quick in-and-out trades with tight stops.",
  Sideways: "Fade extremes when the market is range-bound.",
};

/** Ready-made starting points. Picking one only loads it into the editor; nothing is saved until the user saves. */
export function ReadyMade({ onPick }: { onPick: (name: string, spec: Record<string, unknown>) => void }) {
  const [data, setData] = useState<{ categories: string[]; strategies: Entry[] } | null>(null);
  const [cat, setCat] = useState<string>("Momentum");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getJSON<{ categories: string[]; strategies: Entry[] }>("/strategies/library").then(setData).catch((e) => setError((e as Error).message));
  }, []);

  if (error) return null;
  if (!data) return null;
  const shown = data.strategies.filter((s) => s.category === cat);
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
      <h2 className="text-sm font-semibold">Ready-made strategies</h2>
      <p className="mt-1 text-[11px] text-ink-400">Starting points to backtest and change, not recommendations. None is proven.</p>
      <div className="mt-3 flex flex-wrap gap-1 rounded-lg bg-ink-850 p-1" role="tablist">
        {data.categories.map((c) => (
          <button key={c} role="tab" aria-selected={cat === c} onClick={() => setCat(c)}
            className={`flex-1 rounded-md px-2 py-1 text-xs font-semibold ${cat === c ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}>
            {c}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-ink-400">{NOTE[cat]}</p>
      <ul className="mt-2 flex flex-col gap-1.5">
        {shown.map((s) => (
          <li key={s.name}>
            <button onClick={() => onPick(s.name, s.spec)} title={s.description}
              className="w-full rounded-md border border-ink-800 px-3 py-2 text-left hover:border-accent hover:bg-ink-850">
              <span className="block text-xs font-semibold text-ink-100">{s.name}</span>
              <span className="mt-0.5 block text-[11px] leading-snug text-ink-400">{s.description.replace(/^[A-Za-z]+:\s*/, "")}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
