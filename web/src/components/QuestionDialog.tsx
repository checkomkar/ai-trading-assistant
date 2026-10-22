import { useEffect, useRef, useState } from "react";

const OTHER = "__other__";
const AI = "__ai__";
// answers the model offered that our own "Other…" and "Let the AI decide" buttons already cover
const OWN_OPTIONS = /^(other|none of the above|let the ai|you decide|ai decide)/i;
const AI_ANSWER = "you choose a sensible default and list it under assumptions";

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
  // picked: a tapped answer, OTHER (type your own) or AI (let the AI decide); null = not answered
  const [picked, setPicked] = useState<(string | null)[]>(() => questions.map(() => null));
  const [custom, setCustom] = useState<string[]>(() => questions.map(() => ""));
  const box = useRef<HTMLDivElement>(null);

  // The parent passes a new onClose function on every render (each keystroke), so keep it in a ref: depending on it would
  // re-run the focus line below and pull the cursor out of the answer box.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    box.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const answer = (i: number) =>
    picked[i] === OTHER ? custom[i].trim() || null : picked[i] === AI ? AI_ANSWER : picked[i];
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
            Tap an answer, pick "Other…" to type your own, or let the AI decide. Anything you skip, the AI fills with a sensible default and lists it as an assumption.
          </p>
        </header>

        <div className="flex flex-col gap-3 overflow-y-auto px-4 py-3">
          {questions.map((q, i) => (
            <section key={q} className="rounded-lg border border-ink-800 bg-ink-850 p-3">
              <h3 className="text-sm text-ink-100">
                <span className="num mr-1.5 text-ink-400">{i + 1}.</span>
                {q}
              </h3>
              <div role="radiogroup" aria-label={q} className="mt-2 flex flex-wrap gap-1.5">
                {[...(choices[i] ?? []).filter((c) => !OWN_OPTIONS.test(c)), OTHER, AI].map((c) => {
                  const on = picked[i] === c;
                  return (
                    <button
                      key={c}
                      role="radio"
                      aria-checked={on}
                      onClick={() => setPicked(set(picked, i, on ? null : c))}
                      className={`rounded-full border px-3 py-1 text-xs ${on ? "border-accent bg-accent text-ink-950" : c === OTHER || c === AI ? "border-dashed border-ink-700 text-ink-300 hover:border-accent" : "border-ink-700 text-ink-200 hover:border-accent"}`}
                    >
                      {c === OTHER ? "Other…" : c === AI ? "Let the AI decide" : c}
                    </button>
                  );
                })}
              </div>
              {picked[i] === OTHER && (
                <input
                  autoFocus
                  value={custom[i]}
                  onChange={(e) => setCustom(set(custom, i, e.target.value))}
                  placeholder="Type your answer"
                  aria-label={`Your own answer to: ${q}`}
                  className="mt-2 w-full rounded border border-ink-700 bg-ink-900 px-2 py-1.5 text-xs text-ink-100 outline-none focus:border-accent"
                />
              )}
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
