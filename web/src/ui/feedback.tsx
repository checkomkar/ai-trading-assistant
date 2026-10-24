import { useEffect, useSyncExternalStore } from "react";

/** App-wide feedback: an animated loader while any operation runs, and toasts when one finishes.
 *  `api.ts` tracks every request that changes something (POST/PATCH/PUT/DELETE) and toasts its failures; pages add
 *  success toasts and can `track` any other promise. */

export type ToastKind = "success" | "error" | "info";
export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
  detail?: string;
}

interface State {
  pending: { id: number; label: string }[];
  toasts: Toast[];
}

let state: State = { pending: [], toasts: [] };
const listeners = new Set<() => void>();
let seq = 0;
const set = (next: State) => {
  state = next;
  listeners.forEach((l) => l());
};
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Show the loader until `p` settles. `label` says what is happening ("Running backtest…"). */
export function track<T>(p: Promise<T>, label = "Working…"): Promise<T> {
  const id = ++seq;
  set({ ...state, pending: [...state.pending, { id, label }] });
  const done = () => set({ ...state, pending: state.pending.filter((x) => x.id !== id) });
  p.then(done, done);
  return p;
}

function push(kind: ToastKind, text: string, detail?: string) {
  const id = ++seq;
  // the same message twice in a row (a retry that failed again) replaces the first instead of stacking
  const rest = state.toasts.filter((t) => !(t.text === text && t.kind === kind)).slice(-3);
  set({ ...state, toasts: [...rest, { id, kind, text, detail }] });
  setTimeout(() => dismiss(id), kind === "error" ? 8000 : 4500);
}

export function dismiss(id: number) {
  set({ ...state, toasts: state.toasts.filter((t) => t.id !== id) });
}

export const toast = {
  success: (text: string, detail?: string) => push("success", text, detail),
  error: (text: string, detail?: string) => push("error", text, detail),
  info: (text: string, detail?: string) => push("info", text, detail),
};

export function useBusy(): { busy: boolean; label: string | null } {
  const s = useSyncExternalStore(subscribe, () => state);
  return { busy: s.pending.length > 0, label: s.pending.at(-1)?.label ?? null };
}

/** The top loader bar, a "working" pill and the toast stack. Mounted once at the root. */
export function FeedbackLayer() {
  const s = useSyncExternalStore(subscribe, () => state);
  const busy = s.pending.length > 0;
  const label = s.pending.at(-1)?.label;
  useEffect(() => {
    document.body.toggleAttribute("aria-busy", busy);
  }, [busy]);
  return (
    <>
      <div className={`fb-bar ${busy ? "fb-bar-on" : ""}`} aria-hidden />
      <div
        role="status"
        aria-live="polite"
        className={`fixed bottom-4 left-4 z-[70] flex items-center gap-2 rounded-full border border-ink-700 bg-ink-900/95 px-3 py-1.5 text-xs text-ink-100 shadow-lg backdrop-blur transition-all duration-300 ${
          busy ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-3 opacity-0"
        }`}
      >
        <Spinner />
        <span>{label ?? "Working…"}</span>
      </div>
      <div className="pointer-events-none fixed right-4 bottom-4 z-[80] flex w-[min(380px,calc(100vw-2rem))] flex-col gap-2" aria-live="assertive">
        {s.toasts.map((t) => (
          <div
            key={t.id}
            role={t.kind === "error" ? "alert" : "status"}
            className={`fb-toast pointer-events-auto flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-xs shadow-xl backdrop-blur ${
              t.kind === "success"
                ? "border-up/40 bg-ink-900/95"
                : t.kind === "error"
                  ? "border-down/50 bg-ink-900/95"
                  : "border-ink-700 bg-ink-900/95"
            }`}
          >
            <span
              aria-hidden
              className={`mt-px grid h-4 w-4 shrink-0 place-items-center rounded-full text-[10px] font-bold text-ink-950 ${
                t.kind === "success" ? "bg-up" : t.kind === "error" ? "bg-down" : "bg-accent"
              }`}
            >
              {t.kind === "success" ? "✓" : t.kind === "error" ? "!" : "i"}
            </span>
            <div className="min-w-0 flex-1">
              <p className="font-medium text-ink-100">{t.text}</p>
              {t.detail && <p className="mt-0.5 break-words text-ink-300">{t.detail}</p>}
            </div>
            <button onClick={() => dismiss(t.id)} aria-label="Dismiss" className="text-ink-400 hover:text-ink-100">
              ✕
            </button>
          </div>
        ))}
      </div>
    </>
  );
}

export function Spinner({ size = 14, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className={`animate-spin ${className}`} aria-hidden>
      <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="3" />
      <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="var(--color-accent)" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

/** A panel-sized loader: pulsing candles, for long operations like a backtest. */
export function CandleLoader({ title, detail }: { title: string; detail?: string }) {
  return (
    <div className="flex flex-col items-center gap-4 py-10 text-center" role="status" aria-live="polite">
      <div className="flex h-14 items-end gap-1.5" aria-hidden>
        {[0, 1, 2, 3, 4, 5, 6].map((i) => (
          <span key={i} className="fb-candle" style={{ animationDelay: `${i * 0.11}s`, background: i % 3 === 1 ? "var(--color-down)" : "var(--color-up)" }} />
        ))}
      </div>
      <div>
        <p className="text-sm font-medium text-ink-100">{title}</p>
        {detail && <p className="mt-1 text-xs text-ink-400">{detail}</p>}
      </div>
    </div>
  );
}
