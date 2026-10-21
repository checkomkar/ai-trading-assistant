import { useState, type FormEvent } from "react";
import { brand } from "../brand";
import { useAuth } from "../auth";
import { Logo } from "../components/Logo";

export function LoginPage() {
  const { me, signIn, signUp } = useAuth();
  const canSignUp = me?.signup_open !== false;
  const [mode, setMode] = useState<"in" | "up">("in");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [invite, setInvite] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "in") await signIn(email, password);
      else await signUp(email, password, name, invite);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = "w-full rounded-md border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-ink-100 outline-none focus:border-accent";
  return (
    <div className="grid min-h-dvh place-items-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <Logo size={40} />
          <p className="text-sm text-ink-400">{brand.tagline}</p>
        </div>
        <form onSubmit={submit} className="space-y-3 rounded-lg border border-ink-800 bg-ink-900 p-5">
          <h1 className="text-base font-semibold">{mode === "in" ? "Sign in" : "Create your account"}</h1>
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
          {mode === "up" && me?.invite_required && (
            <label className="block text-xs text-ink-300">
              Invite code
              <input className={`${field} mt-1`} required value={invite} onChange={(e) => setInvite(e.target.value)} />
            </label>
          )}
          {error && <p className="text-xs text-down" role="alert">{error}</p>}
          <button disabled={busy} className="w-full rounded-md bg-accent px-3 py-2 text-sm font-semibold text-ink-950 disabled:opacity-60">
            {busy ? "Please wait…" : mode === "in" ? "Sign in" : "Start free trial"}
          </button>
          {mode === "up" && <p className="text-[11px] text-ink-400">The trial includes 5 AI strategy generations. No card needed.</p>}
        </form>
        {canSignUp && (
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
