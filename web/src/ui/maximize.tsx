import { useEffect, useState } from "react";

/** Full-screen toggle for a panel (charts): Escape or the button restores it. Charts use `autoSize`, so they follow. */
export function useMaximize() {
  const [max, setMax] = useState(false);
  useEffect(() => {
    if (!max) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMax(false);
    window.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [max]);
  return {
    max,
    toggle: () => setMax((m) => !m),
    /** classes for the panel itself */
    frame: max ? "fixed inset-0 z-[60] flex flex-col bg-ink-950 fade-in" : "",
  };
}

export function MaximizeButton({ max, onClick }: { max: boolean; onClick: () => void }) {
  const label = max ? "Exit full screen (Esc)" : "Full screen";
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-label={label}
      aria-pressed={max}
      className="grid h-7 w-7 place-items-center rounded-md border border-ink-700 text-ink-300 hover:bg-ink-800 hover:text-ink-100"
    >
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
        {max ? (
          <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
        ) : (
          <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
        )}
      </svg>
    </button>
  );
}
