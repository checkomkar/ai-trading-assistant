import { useEffect, useRef, useState } from "react";

/** The strategy translator needs answers before it can build the rules: one card per question with tappable
 *  answers (and a free-text box), or hand the whole thing to the Assistant to talk it through. */
export function QuestionDialog({
  questions,
  choices,
  busy,
  onSubmit,
  onAssistant,
  onClose,
}: {
  questions: string[];
  choices: string[][];
  busy: boolean;
  onSubmit: (answers: (string | null)[]) => void;
  onAssistant: () => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<(string | null)[]>(() => questions.map(() => null));
  const [custom, setCustom] = useState<string[]>(() => questions.map(() => ""));
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const answer = (i: number) => (custom[i].trim() ? custom[i].trim() : picked[i]);
  const answers = questions.map((_, i) => answer(i));
  const count = answers.filter(Boolean).length;
  const set = <T,>(arr: T[], i: number, v: T) => arr.map((x, j) => (j === i ? v : x));

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/60 p-4" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div
        ref={box}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="qd-title"
        className="flex max-h-[90dvh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-ink-700 bg-ink-900 shadow-2xl outline-none"
      >
        <header className="border-b border-ink-800 px-4 py-3">
          <h2 id="qd-title" className="text-base font-semibold text-ink-100">A few questions before the rules are built</h2>
          <p className="mt-0.5 text-xs text-ink-400">
            Tap an answer or type your own. Anything you skip, the AI fills with a sensible default and lists it as an assumption.
          </p>
        </header>

        <div className="flex flex-col gap-3 overflow-y-auto px-4 py-3">
          {questions.map((q, i) => (
            <section key={q} className="rounded-lg border border-ink-800 bg-ink-850 p-3">
              <h3 className="text-sm text-ink-100">
                <span className="num mr-1.5 text-ink-400">{i + 1}.</span>
                {q}
              </h3>
              {choices[i]?.length > 0 && (
                <div role="radiogroup" aria-label={q} className="mt-2 flex flex-wrap gap-1.5">
                  {choices[i].map((c) => {
                    const on = picked[i] === c && !custom[i].trim();
                    return (
                      <button
                        key={c}
                        role="radio"
                        aria-checked={on}
                        onClick={() => {
                          setPicked(set(picked, i, on ? null : c));
                          setCustom(set(custom, i, ""));
                        }}
                        className={`rounded-full border px-3 py-1 text-xs ${on ? "border-accent bg-accent text-ink-950" : "border-ink-700 text-ink-200 hover:border-accent"}`}
                      >
                        {c}
                      </button>
                    );
                  })}
                </div>
              )}
              <input
                value={custom[i]}
                onChange={(e) => setCustom(set(custom, i, e.target.value))}
                placeholder={choices[i]?.length ? "Or type your own answer" : "Type your answer"}
                aria-label={`Your own answer to: ${q}`}
                className="mt-2 w-full rounded border border-ink-700 bg-ink-900 px-2 py-1.5 text-xs text-ink-100 outline-none focus:border-accent"
              />
            </section>
          ))}
        </div>

        <footer className="flex flex-wrap items-center gap-2 border-t border-ink-800 px-4 py-3">
          <span className="num text-[11px] text-ink-400">
            {count} of {questions.length} answered
          </span>
          <button onClick={onClose} className="ml-auto rounded-md px-3 py-1.5 text-xs text-ink-400 hover:text-ink-100">
            Cancel
          </button>
          <button onClick={onAssistant} className="rounded-md border border-ink-700 px-3 py-1.5 text-xs text-ink-100 hover:border-accent">
            Build it with the Assistant
          </button>
          <button
            onClick={() => onSubmit(answers)}
            disabled={busy}
            className="rounded-md bg-accent px-4 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
          >
            {busy ? "Building…" : count === 0 ? "Let the AI choose defaults" : "Build with these answers"}
          </button>
        </footer>
      </div>
    </div>
  );
}
