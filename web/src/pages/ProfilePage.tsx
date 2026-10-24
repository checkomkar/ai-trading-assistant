import { useEffect, useState } from "react";
import { ApiError, getJSON, postJSON, sendJSON, type Me, type Profile } from "../api";
import { useAuth } from "../auth";
import { Spinner, toast } from "../ui/feedback";

const date = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }) : "—";

export const STATUS: Record<string, { label: string; tone: string; note: (p: Profile) => string }> = {
  trial: { label: "Free trial", tone: "text-accent ring-accent/40 bg-accent/10", note: () => "Trial allowances are for the life of the account." },
  active: { label: "Active", tone: "text-up ring-up/40 bg-up/10", note: (p) => `Renews on ${date(p.period_end)}.` },
  ending: { label: "Not renewing", tone: "text-accent ring-accent/40 bg-accent/10", note: (p) => `Your plan works until ${date(p.period_end)}, then moves to the trial.` },
  lapsed: { label: "Expired", tone: "text-down ring-down/40 bg-down/10", note: () => "Your paid period ended; trial limits apply." },
  owner: { label: "Owner", tone: "text-sim ring-sim/40 bg-sim/10", note: () => "Operator account: no limits." },
  local: { label: "Local", tone: "text-ink-300 ring-ink-600 bg-ink-800", note: () => "Single-user mode on your own machine: no accounts and no limits." },
};

const USAGE_LABEL: Record<string, string> = {
  generations: "AI strategy generations",
  chat_messages: "Assistant messages",
  speech_requests: "Voice inputs",
  backtests: "Backtests",
  strategies: "Saved strategies",
  docs: "Uploaded documents",
};

/** The signed-in account: who you are, your plan and its status, usage, and account security. */
export function ProfilePage() {
  const { me, usage, signOut, refreshUsage } = useAuth();
  const [p, setP] = useState<Profile | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const [busy, setBusy] = useState<string | null>(null);

  const load = () => {
    setFailed(null);
    return getJSON<Profile>("/auth/profile")
      .then((x) => {
        setP(x);
        setName(x.name);
      })
      .catch((e) => {
        // Accounts off: the profile is known without asking (an API from before this page answers 404 here).
        if (me && !me.auth_enabled) return setP(localProfile(me));
        setFailed(e instanceof ApiError && e.status === 404
          ? "This server does not know the profile page yet. Restart the API after updating, then try again."
          : (e as Error).message || "Could not load your profile.");
      });
  };
  useEffect(() => {
    load();
    refreshUsage();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const act = async (key: string, fn: () => Promise<void>) => {
    setBusy(key);
    try {
      await fn();
    } catch {
      /* api.ts already showed the error */
    } finally {
      setBusy(null);
    }
  };

  if (!p && failed) {
    return (
      <main className="grid flex-1 place-items-center p-10">
        <div className="max-w-sm rounded-xl border border-ink-800 bg-ink-900 p-6 text-center">
          <p className="text-sm font-medium text-ink-100">Profile unavailable</p>
          <p className="mt-1 text-xs text-ink-400">{failed}</p>
          <div className="mt-4 flex justify-center gap-2">
            <button onClick={() => load()} className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110">Try again</button>
            <a href="#/" className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-ink-200 hover:bg-ink-800">Back to the app</a>
          </div>
        </div>
      </main>
    );
  }

  if (!p) {
    return (
      <main className="grid flex-1 place-items-center p-10 text-ink-400">
        <Spinner size={20} />
      </main>
    );
  }

  const st = STATUS[p.status] ?? STATUS.trial;
  const initial = (p.name || p.email || p.phone || "?").trim().slice(0, 1).toUpperCase();
  const pwReady = pw.next.length >= 10 && pw.next === pw.again && (!p.sign_in.includes("password") || pw.current.length > 0);

  return (
    <main className="mx-auto w-full max-w-4xl flex-1 space-y-4 p-4 sm:p-6">
      {/* who */}
      <section className="flex flex-wrap items-center gap-4 rounded-xl border border-ink-800 bg-ink-900 p-5">
        <div className="grid h-16 w-16 shrink-0 place-items-center rounded-2xl bg-gradient-to-br from-accent to-amber-600 text-2xl font-bold text-ink-950">
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-lg font-semibold">{p.local ? "Local user" : p.name || "Your account"}</h1>
          <p className="truncate text-sm text-ink-400">{p.email ?? p.phone ?? "No email on this account"}</p>
          <p className="mt-1 text-[11px] text-ink-500">{p.local ? "Accounts are off on this server" : `Member since ${date(p.created_at)}`}</p>
        </div>
        <div className="flex flex-col items-end gap-1.5">
          <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ${st.tone}`}>{st.label}</span>
          {p.is_admin && !p.local && <span className="text-[10px] uppercase tracking-widest text-sim">Operator</span>}
        </div>
      </section>

      {p.local && (
        <section className="rounded-xl border border-ink-800 bg-ink-900 p-5 text-sm text-ink-300">
          <h2 className="mb-1 text-sm font-semibold text-ink-100">Single-user mode</h2>
          <p>
            This server runs without accounts, so there is no sign-in, profile or plan. To use accounts, sign-in and plans, set{" "}
            <code className="num rounded bg-ink-800 px-1 text-ink-100">AUTH_ENABLED=true</code> in <code className="num">backend/.env</code> and restart the API.
          </p>
        </section>
      )}

      {!p.local && (
        <>
          {/* plan */}
          <section className="rounded-xl border border-ink-800 bg-ink-900 p-5">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <h2 className="text-xs font-medium uppercase tracking-wider text-ink-400">Subscription</h2>
                <p className="mt-1 text-xl font-semibold">
                  {p.plan.name}
                  {p.plan.price_inr > 0 && <span className="num ml-2 text-sm font-normal text-ink-400">₹{p.plan.price_inr.toLocaleString("en-IN")} / month</span>}
                </p>
                <p className="mt-1 text-xs text-ink-400">{st.note(p)}</p>
              </div>
              <div className="flex gap-2">
                <a href="#/pricing" className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110">
                  {p.status === "active" || p.status === "owner" ? "Change plan" : "Upgrade"}
                </a>
                {p.status === "active" && (
                  <button
                    disabled={!!busy}
                    onClick={() =>
                      window.confirm("Stop renewing? Your plan keeps working until the period you paid for ends.") &&
                      act("cancel", async () => {
                        await postJSON("/billing/cancel", {});
                        toast.success("Renewal stopped", `You keep your plan until ${date(p.period_end)}.`);
                        await load();
                      })
                    }
                    className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-ink-200 hover:border-down hover:text-down disabled:opacity-50"
                  >
                    Cancel renewal
                  </button>
                )}
              </div>
            </div>
            {usage && (
              <div className="mt-5 grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
                {Object.entries(usage.usage).map(([k, u]) => (
                  <div key={k}>
                    <div className="flex justify-between text-xs">
                      <span className="text-ink-300">{USAGE_LABEL[k] ?? k}</span>
                      <span className="num text-ink-400">
                        {u.used}
                        {u.limit === null ? " · unlimited" : ` / ${u.limit}`}
                      </span>
                    </div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-ink-800">
                      <div
                        className={`h-full rounded-full ${u.limit !== null && u.used >= u.limit ? "bg-down" : "bg-accent"}`}
                        style={{ width: u.limit === null ? "100%" : `${Math.min(100, (u.used / Math.max(1, u.limit)) * 100)}%`, opacity: u.limit === null ? 0.25 : 1 }}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}
            {usage?.period.start && !usage.plan.lifetime && (
              <p className="mt-3 text-[11px] text-ink-500">Usage period {date(usage.period.start)} – {date(usage.period.end)}</p>
            )}
          </section>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* details */}
            <section className="rounded-xl border border-ink-800 bg-ink-900 p-5">
              <h2 className="mb-3 text-sm font-semibold">Details</h2>
              <label className="block text-xs text-ink-400">
                Name
                <div className="mt-1 flex gap-2">
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    maxLength={80}
                    className="min-w-0 flex-1 rounded-md border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-ink-100 outline-none focus:border-accent"
                  />
                  <button
                    disabled={!!busy || !name.trim() || name.trim() === p.name}
                    onClick={() =>
                      act("name", async () => {
                        const x = await sendJSON<Profile>("PATCH", "/auth/profile", { name });
                        setP(x);
                        setName(x.name);
                        toast.success("Name saved");
                      })
                    }
                    className="rounded-md border border-ink-700 px-3 text-xs text-ink-100 hover:border-accent disabled:opacity-40"
                  >
                    {busy === "name" ? <Spinner size={12} /> : "Save"}
                  </button>
                </div>
              </label>
              <dl className="mt-4 space-y-2 text-xs">
                <Row k="Email" v={p.email ?? "—"} />
                <Row k="Phone" v={p.phone ?? "—"} />
                <Row k="Signs in with" v={p.sign_in.map((m) => ({ password: "Email and password", google: "Google", phone: "Phone (OTP)" })[m] ?? m).join(", ") || "—"} />
                <Row k="Account id" v={<span className="num text-ink-400">{p.id}</span>} />
              </dl>
            </section>

            {/* security */}
            <section className="rounded-xl border border-ink-800 bg-ink-900 p-5">
              <h2 className="mb-3 text-sm font-semibold">Security</h2>
              {p.can_set_password ? (
                <form
                  className="space-y-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    if (!pwReady) return;
                    act("pw", async () => {
                      await postJSON("/auth/password", { current: pw.current, new: pw.next });
                      setPw({ current: "", next: "", again: "" });
                      toast.success(p.sign_in.includes("password") ? "Password changed" : "Password set", "Other devices were signed out.");
                      await load();
                    });
                  }}
                >
                  {p.sign_in.includes("password") && (
                    <Pw label="Current password" value={pw.current} onChange={(v) => setPw({ ...pw, current: v })} auto="current-password" />
                  )}
                  <Pw label={p.sign_in.includes("password") ? "New password" : "Set a password"} value={pw.next} onChange={(v) => setPw({ ...pw, next: v })} auto="new-password" />
                  <Pw label="Repeat it" value={pw.again} onChange={(v) => setPw({ ...pw, again: v })} auto="new-password" />
                  <p className="text-[11px] text-ink-500">
                    {pw.next && pw.next.length < 10 ? "At least 10 characters." : pw.again && pw.again !== pw.next ? "The two do not match." : "Changing it signs out your other devices."}
                  </p>
                  <button disabled={!!busy || !pwReady} className="rounded-md bg-ink-700 px-3 py-1.5 text-xs font-medium text-ink-100 hover:bg-ink-600 disabled:opacity-40">
                    {busy === "pw" ? <Spinner size={12} /> : p.sign_in.includes("password") ? "Change password" : "Set password"}
                  </button>
                </form>
              ) : (
                <p className="text-xs text-ink-400">This account signs in by phone, so it has no password.</p>
              )}
              <div className="mt-5 border-t border-ink-800 pt-4 text-xs">
                <p className="text-ink-300">
                  Signed in on <b className="num text-ink-100">{p.sessions}</b> device{p.sessions === 1 ? "" : "s"}
                  {p.this_session_expires && <span className="text-ink-500"> · this one until {date(p.this_session_expires)}</span>}
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    disabled={!!busy || p.sessions < 2}
                    onClick={() =>
                      act("others", async () => {
                        const r = await postJSON<{ ended: number }>("/auth/sessions/end-others", {});
                        toast.success(`Signed out ${r.ended} other device${r.ended === 1 ? "" : "s"}`);
                        await load();
                      })
                    }
                    className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-200 hover:border-accent disabled:opacity-40"
                  >
                    Sign out other devices
                  </button>
                  <button onClick={() => signOut()} className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-200 hover:border-down hover:text-down">
                    Sign out
                  </button>
                </div>
              </div>
            </section>
          </div>
        </>
      )}
    </main>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-ink-400">{k}</dt>
      <dd className="truncate text-right text-ink-100">{v}</dd>
    </div>
  );
}

function Pw({ label, value, onChange, auto }: { label: string; value: string; onChange: (v: string) => void; auto: string }) {
  return (
    <label className="block text-xs text-ink-400">
      {label}
      <input
        type="password"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoComplete={auto}
        className="mt-1 w-full rounded-md border border-ink-700 bg-ink-850 px-2.5 py-1.5 text-sm text-ink-100 outline-none focus:border-accent"
      />
    </label>
  );
}

function localProfile(me: Me): Profile {
  return {
    local: true, id: me.id ?? "local", name: "Local", email: null, phone: null, is_admin: true, created_at: null, sign_in: [],
    can_set_password: false, plan: { id: "owner", name: "Local · no limits", price_inr: 0 }, status: "local",
    period_start: null, period_end: null, sessions: 0, this_session_expires: null,
  };
}
