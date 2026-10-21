import { useEffect, useState } from "react";
import type { QuotaInfo } from "../api";

/** Opens whenever the server answers 402: says which allowance ran out and points to the plans. */
export function UpgradePrompt() {
  const [q, setQ] = useState<QuotaInfo | null>(null);
  useEffect(() => {
    const on = (e: Event) => setQ((e as CustomEvent<QuotaInfo>).detail);
    window.addEventListener("striktek:quota", on);
    return () => window.removeEventListener("striktek:quota", on);
  }, []);
  if (!q) return null;
  const canUpgrade = q.plan === "trial" || q.plan === "pro";
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" role="dialog" aria-modal="true" aria-label="Plan limit reached" onClick={() => setQ(null)}>
      <div className="w-full max-w-sm rounded-lg border border-ink-700 bg-ink-900 p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <h2 className="text-base font-semibold">Plan limit reached</h2>
        <p className="mt-2 text-sm text-ink-300">{q.message}</p>
        <p className="mt-2 text-xs text-ink-400">Backtests and paper trading do not use AI allowance, so those keep working.</p>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={() => setQ(null)} className="rounded-md px-3 py-1.5 text-sm text-ink-300 hover:text-ink-100">Not now</button>
          {canUpgrade && (
            <a href="#/pricing" onClick={() => setQ(null)} className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-ink-950">See plans</a>
          )}
        </div>
      </div>
    </div>
  );
}
