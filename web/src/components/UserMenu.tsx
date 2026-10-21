import { useEffect, useRef, useState } from "react";
import { useAuth } from "../auth";

/** Header chip: AI generations left (the number a trial user watches), plan, and the sign-out menu. */
export function UserMenu({ pricingActive }: { pricingActive: boolean }) {
  const { me, usage, signOut } = useAuth();
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const close = (e: MouseEvent) => !box.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  if (!me?.auth_enabled) return null; // local single-user mode: no accounts, no limits

  const gens = usage?.usage.generations;
  const low = gens && gens.remaining !== null && gens.remaining <= 1;
  return (
    <div className="flex items-center gap-2" ref={box}>
      {gens && (
        <a href="#/pricing" title="AI strategy generations left in your plan"
          className={`rounded-full px-2.5 py-1 text-[11px] font-medium ring-1 ${low ? "bg-down/10 text-down ring-down/40" : "bg-ink-800 text-ink-300 ring-ink-600"}`}>
          {gens.limit === null ? "Unlimited AI" : <><span className="num">{gens.remaining}</span> AI left</>}
        </a>
      )}
      <a href="#/pricing" aria-current={pricingActive ? "page" : undefined}
        className="rounded bg-accent/15 px-2 py-1 text-[11px] font-semibold uppercase tracking-wider text-accent ring-1 ring-accent/30">
        {usage?.plan.name ?? me.plan ?? "…"}
      </a>
      <div className="relative">
        <button onClick={() => setOpen((o) => !o)} aria-haspopup="menu" aria-expanded={open} aria-label="Account menu"
          className="grid h-7 w-7 place-items-center rounded-full bg-ink-700 text-xs font-semibold uppercase text-ink-100">
          {(me.name || me.email || "?").slice(0, 1)}
        </button>
        {open && (
          <div role="menu" className="absolute right-0 z-30 mt-2 w-56 rounded-md border border-ink-700 bg-ink-900 p-1 text-xs shadow-xl">
            <div className="truncate px-3 py-2 text-ink-400">{me.email}</div>
            <a role="menuitem" href="#/pricing" onClick={() => setOpen(false)} className="block rounded px-3 py-2 text-ink-100 hover:bg-ink-800">Plan and usage</a>
            <button role="menuitem" onClick={() => signOut()} className="block w-full rounded px-3 py-2 text-left text-ink-100 hover:bg-ink-800">Sign out</button>
          </div>
        )}
      </div>
    </div>
  );
}
