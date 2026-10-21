import { useEffect, useRef, useState, type FormEvent } from "react";
import { brand } from "../brand";
import { useAuth } from "../auth";
import { Logo } from "../components/Logo";

type Method = "email" | "phone";

interface GoogleId {
  initialize: (o: { client_id: string; callback: (r: { credential: string }) => void }) => void;
  renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
}
declare global {
  interface Window {
    google?: { accounts: { id: GoogleId } };
  }
}

/** Google's own button (Google Identity Services). Hidden when the server has no client id. */
function GoogleButton({ clientId, onCredential }: { clientId: string; onCredential: (c: string) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const cb = useRef(onCredential);
  cb.current = onCredential;
  useEffect(() => {
    const draw = () => {
      if (!window.google || !box.current) return;
      window.google.accounts.id.initialize({ client_id: clientId, callback: (r) => cb.current(r.credential) });
      window.google.accounts.id.renderButton(box.current, { theme: "outline", size: "large", width: 320, text: "continue_with" });
    };
    if (window.google) return draw();
    const s = document.createElement("script");
    s.src = "https://accounts.google.com/gsi/client";
    s.async = true;
    s.onload = draw;
    document.head.appendChild(s);
    return () => {
      s.onload = null;
    };
  }, [clientId]);
  return <div ref={box} className="flex min-h-[44px] justify-center" />;
}

export function LoginPage() {
  const { me, signIn, signUp, signInWithGoogle, sendOtp, verifyOtp } = useAuth();
  const canSignUp = me?.signup_open !== false;
  const [method, setMethod] = useState<Method>("email");
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [invite, setInvite] = useState("");
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submitEmail = (e: FormEvent) => {
    e.preventDefault();
    return run(() => (mode === "in" ? signIn(email, password) : signUp(email, password, name, invite)));
  };
  const submitPhone = (e: FormEvent) => {
    e.preventDefault();
    return run(async () => {
      if (sentTo === null) setSentTo(await sendOtp(phone));
      else await verifyOtp(sentTo, code, name, invite);
    });
  };

  const field = "w-full rounded-md border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-ink-100 outline-none focus:border-accent";
  const primary = "w-full rounded-md bg-accent px-3 py-2 text-sm font-semibold text-ink-950 disabled:opacity-60";
  const tab = (m: Method, label: string) => (
    <button type="button" onClick={() => { setMethod(m); setError(null); }}
      className={`flex-1 rounded-md px-3 py-1.5 text-xs font-semibold ${method === m ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}>
      {label}
    </button>
  );
  const inviteField = me?.invite_required && (
    <label className="block text-xs text-ink-300">
      Invite code
      <input className={`${field} mt-1`} required value={invite} onChange={(e) => setInvite(e.target.value)} />
    </label>
  );

  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <Logo size={40} />
          <p className="text-sm text-ink-400">{brand.tagline}</p>
        </div>
        <div className="space-y-3 rounded-lg border border-ink-800 bg-ink-900 p-5">
          <h1 className="text-base font-semibold">{mode === "in" ? "Sign in" : "Create your account"}</h1>

          {me?.google_client_id && (
            <>
              <GoogleButton clientId={me.google_client_id} onCredential={(c) => run(() => signInWithGoogle(c, invite))} />
              {inviteField}
              <div className="flex items-center gap-2 text-[11px] text-ink-500">
                <span className="h-px flex-1 bg-ink-800" />or<span className="h-px flex-1 bg-ink-800" />
              </div>
            </>
          )}

          {me?.phone_login && (
            <div className="flex gap-1 rounded-lg bg-ink-850 p-1">
              {tab("email", "Email")}
              {tab("phone", "Phone (OTP)")}
            </div>
          )}

          {method === "email" || !me?.phone_login ? (
            <form onSubmit={submitEmail} className="space-y-3">
              {mode === "up" && (
                <label className="block text-xs text-ink-300">
                  Name
                  <input className={`${field} mt-1`} value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" maxLength={80} />
                </label>
              )}
              <label className="block text-xs text-ink-300">
                Email
                <input className={`${field} mt-1`} type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" />
              </label>
              <label className="block text-xs text-ink-300">
                Password
                <input className={`${field} mt-1`} type="password" required minLength={mode === "up" ? 10 : 1} value={password}
                  onChange={(e) => setPassword(e.target.value)} autoComplete={mode === "in" ? "current-password" : "new-password"} />
                {mode === "up" && <span className="mt-1 block text-[11px] text-ink-400">At least 10 characters.</span>}
              </label>
              {mode === "up" && !me?.google_client_id && inviteField}
              {mode === "up" && me?.google_client_id && me?.invite_required && <p className="text-[11px] text-ink-400">Use the invite code above.</p>}
              <button disabled={busy} className={primary}>{busy ? "Please wait…" : mode === "in" ? "Sign in" : "Start free trial"}</button>
            </form>
          ) : (
            <form onSubmit={submitPhone} className="space-y-3">
              <label className="block text-xs text-ink-300">
                Mobile number
                <input className={`${field} mt-1`} type="tel" required inputMode="tel" autoComplete="tel" placeholder="+91 98765 43210"
                  value={sentTo ?? phone} disabled={sentTo !== null} onChange={(e) => setPhone(e.target.value)} />
              </label>
              {sentTo !== null && (
                <>
                  <label className="block text-xs text-ink-300">
                    6-digit code
                    <input className={`${field} mt-1 tracking-[0.4em]`} required inputMode="numeric" autoComplete="one-time-code" maxLength={6}
                      value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus />
                  </label>
                  <label className="block text-xs text-ink-300">
                    Name (new accounts)
                    <input className={`${field} mt-1`} value={name} onChange={(e) => setName(e.target.value)} maxLength={80} />
                  </label>
                  {!me?.google_client_id && inviteField}
                </>
              )}
              <button disabled={busy} className={primary}>{busy ? "Please wait…" : sentTo === null ? "Send code" : "Verify and continue"}</button>
              {sentTo !== null && (
                <button type="button" className="text-xs text-ink-400 hover:text-ink-100" onClick={() => { setSentTo(null); setCode(""); setError(null); }}>
                  Use a different number or resend
                </button>
              )}
            </form>
          )}

          {error && <p className="text-xs text-down" role="alert">{error}</p>}
          <p className="text-[11px] text-ink-400">
            By continuing you accept the <a className="underline" href="#/terms" target="_blank" rel="noreferrer">Terms</a> and{" "}
            <a className="underline" href="#/privacy" target="_blank" rel="noreferrer">Privacy Policy</a>. {brand.name} gives no investment
            advice, places no real orders, and is not responsible for any trade you make.
          </p>
          <p className="text-[11px] text-ink-400">The trial includes 5 AI strategy generations. No card needed.</p>
        </div>
        {canSignUp && method === "email" && (
          <p className="mt-4 text-center text-xs text-ink-400">
            {mode === "in" ? "New here?" : "Already have an account?"}{" "}
            <button className="text-accent hover:underline" onClick={() => { setMode(mode === "in" ? "up" : "in"); setError(null); }}>
              {mode === "in" ? "Create an account" : "Sign in"}
            </button>
          </p>
        )}
        <p className="mt-6 text-center text-[11px] text-ink-500">Research and paper trading only. No live orders, no trading advice.</p>
      </div>
    </div>
  );
}
