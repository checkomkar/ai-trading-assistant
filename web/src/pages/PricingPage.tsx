import { useEffect, useState } from "react";
import { getJSON, postJSON, type PlanInfo, type Plans } from "../api";
import { useAuth } from "../auth";
import { brand } from "../brand";

declare global {
  interface Window {
    Razorpay?: new (opts: Record<string, unknown>) => { open: () => void };
  }
}

const LABEL: Record<string, string> = {
  generations: "AI strategy generations",
  chat_messages: "Assistant messages",
  speech_requests: "Voice inputs",
  backtests: "Backtests",
  strategies: "Saved strategies",
  docs: "Uploaded files",
};
const ORDER = ["generations", "chat_messages", "backtests", "strategies", "docs", "speech_requests"] as const;

function loadCheckout(): Promise<void> {
  return new Promise((resolve, reject) => {
    if (window.Razorpay) return resolve();
    const s = document.createElement("script");
    s.src = "https://checkout.razorpay.com/v1/checkout.js";
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Could not load the payment window. Check your connection."));
    document.head.appendChild(s);
  });
}

const rupees = (n: number) => (n === 0 ? "Free" : `₹${n.toLocaleString("en-IN")}`);

export function PricingPage() {
  const { me, usage, refreshUsage } = useAuth();
  const [plans, setPlans] = useState<Plans | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getJSON<Plans>("/billing/plans").then(setPlans).catch((e) => setMsg({ ok: false, text: (e as Error).message }));
  }, []);

  const current = usage?.plan.id ?? me?.plan;

  const upgrade = async (p: PlanInfo) => {
    setBusy(p.id);
    setMsg(null);
    try {
      const co = await postJSON<{ subscription_id: string; key_id: string; email: string }>("/billing/checkout", { plan: p.id });
      await loadCheckout();
      const rz = new window.Razorpay!({
        key: co.key_id,
        subscription_id: co.subscription_id,
        name: brand.name,
        description: `${p.name} plan, monthly`,
        prefill: { email: co.email },
        theme: { color: "#f5b301" },
        handler: () => {
          // The browser's word is not enough: access starts when the payment provider's signed webhook arrives.
          setMsg({ ok: true, text: "Payment received. Activating your plan…" });
          let tries = 0;
          const poll = setInterval(async () => {
            tries += 1;
            refreshUsage();
            const u = await getJSON<{ plan: { id: string } }>("/billing/usage").catch(() => null);
            if (u?.plan.id === p.id) {
              clearInterval(poll);
              setMsg({ ok: true, text: `You are on ${p.name}. Thank you!` });
            } else if (tries > 20) {
              clearInterval(poll);
              setMsg({ ok: true, text: "Your payment is confirmed; the plan can take a minute to appear. Refresh shortly." });
            }
          }, 3000);
        },
      });
      rz.open();
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const cancel = async () => {
    if (!window.confirm("Stop renewing? Your plan keeps working until the period you paid for ends.")) return;
    try {
      await postJSON("/billing/cancel", {});
      setMsg({ ok: true, text: "Renewal stopped. You keep your plan until the end of this period." });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    }
  };

  return (
    <main className="mx-auto w-full max-w-5xl flex-1 space-y-8 p-4 sm:p-6">
      <header>
        <h1 className="text-xl font-semibold">Plans and usage</h1>
        <p className="mt-1 text-sm text-ink-400">
          AI features (strategy generation, assistant, voice) use your plan's allowance. Backtests, charts and paper trading never do.
        </p>
      </header>

      {msg && <p className={`rounded-md border px-3 py-2 text-sm ${msg.ok ? "border-up/40 bg-up/10 text-up" : "border-down/40 bg-down/10 text-down"}`} role="status">{msg.text}</p>}

      {usage && me?.auth_enabled && (
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
          <div className="mb-3 flex flex-wrap items-baseline gap-x-3">
            <h2 className="text-sm font-semibold">Your {usage.plan.name} plan</h2>
            <span className="text-xs text-ink-400">
              {usage.plan.lifetime ? "Trial limits are for your whole trial." : usage.renews ? `Paid until ${new Date(usage.renews).toLocaleDateString("en-IN")}` : "Monthly limits"}
            </span>
            {usage.subscription && usage.plan.id !== "trial" && <button onClick={cancel} className="ml-auto text-xs text-ink-400 underline hover:text-ink-100">Cancel renewal</button>}
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {ORDER.map((k) => {
              const u = usage.usage[k];
              const pct = u.limit ? Math.min(100, Math.round((100 * u.used) / u.limit)) : 0;
              return (
                <div key={k}>
                  <div className="mb-1 flex justify-between text-xs">
                    <span className="text-ink-300">{LABEL[k]}</span>
                    <span className="num text-ink-100">{u.used}{u.limit !== null && <span className="text-ink-400"> / {u.limit}</span>}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded bg-ink-800">
                    <div className={`h-full ${pct >= 90 ? "bg-down" : "bg-accent"}`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
          {usage.ai_allowance_pct !== null && usage.ai_allowance_pct >= 80 && (
            <p className="mt-3 text-xs text-down">You have used {usage.ai_allowance_pct}% of this plan's overall AI allowance.</p>
          )}
        </section>
      )}

      <section className="grid gap-4 md:grid-cols-3">
        {plans?.plans.map((p) => {
          const isCurrent = p.id === current;
          return (
            <div key={p.id} className={`flex flex-col rounded-lg border p-5 ${p.id === "pro" ? "border-accent/60 bg-ink-900" : "border-ink-800 bg-ink-900"}`}>
              <div className="flex items-baseline justify-between">
                <h3 className="text-base font-semibold">{p.name}</h3>
                {p.id === "pro" && <span className="rounded bg-accent/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent">Popular</span>}
              </div>
              <p className="mt-2"><span className="num text-2xl font-semibold">{rupees(p.price_inr)}</span>{p.price_inr > 0 && <span className="text-xs text-ink-400"> / month, GST included</span>}</p>
              <p className="mt-1 text-xs text-ink-400">{p.tagline}</p>
              <ul className="my-4 flex-1 space-y-1.5 text-xs text-ink-300">
                {p.features.map((f) => <li key={f} className="flex gap-2"><span className="text-up">✓</span>{f}</li>)}
              </ul>
              {isCurrent ? (
                <span className="rounded-md bg-ink-800 px-3 py-2 text-center text-xs font-medium text-ink-300">Your current plan</span>
              ) : p.price_inr === 0 ? null : (
                <button disabled={!p.buyable || !!busy || !me?.auth_enabled} onClick={() => upgrade(p)}
                  className="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-ink-950 disabled:cursor-not-allowed disabled:opacity-50">
                  {busy === p.id ? "Opening…" : p.buyable ? `Upgrade to ${p.name}` : "Not available yet"}
                </button>
              )}
            </div>
          );
        })}
      </section>
      <p className="text-center text-[11px] text-ink-500">{plans?.note} Research and paper trading only; {brand.name} does not give trading advice.</p>
    </main>
  );
}
