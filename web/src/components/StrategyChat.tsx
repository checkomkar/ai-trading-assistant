import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { postJSON } from "../api";
import { md } from "../pages/AssistantPage";

interface Draft {
  type: "strategy_draft";
  name: string;
  english: string;
  spec: Record<string, unknown>;
}
interface Msg {
  id: string | number;
  role: "user" | "assistant";
  content: string;
  cards?: Draft[];
}

const BEFORE = [
  "How can I improve this strategy?",
  "When would this strategy fail?",
  "Explain these rules in simple words",
  "What exit rules would suit it better?",
];
const AFTER = [
  "Why did it win or lose money?",
  "How can I improve these results?",
  "Is this enough trades to trust?",
  "Which exits or filters hurt the most?",
];

/** Ask questions about the strategy that is open, before or after a backtest. The server tells the assistant the rules
 *  and the latest backtest, so answers are about this strategy and these numbers. */
export function StrategyChat({
  spec,
  backtestId,
  onLoadSpec,
}: {
  spec: Record<string, unknown> | null;
  backtestId: string | null;
  onLoadSpec: (s: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);
  const conv = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [msgs, busy]);

  const ask = async (q: string) => {
    const body = q.trim();
    if (!body || busy || !spec) return;
    setBusy(true);
    setError(null);
    setText("");
    setMsgs((m) => [...m, { id: `u${Date.now()}`, role: "user", content: body }]);
    try {
      if (!conv.current) conv.current = (await postJSON<{ id: string }>("/chat/conversations", {})).id;
      const reply = await postJSON<Msg>(`/chat/conversations/${conv.current}/messages`, {
        text: body,
        context: { spec, backtest_id: backtestId },
      });
      setMsgs((m) => [...m, reply]);
    } catch (e) {
      setError((e as Error).message);
      setText(body);
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
    }
  };

  const chips = backtestId ? AFTER : BEFORE;
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <button
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2.5 text-left"
      >
        <span className="text-sm font-semibold text-ink-100">Talk to this strategy</span>
        <span className="text-[11px] text-ink-400">
          {backtestId ? "it knows the rules and the latest backtest" : "ask about the rules, or run a backtest first to discuss the results"}
        </span>
        <span className="ml-auto text-ink-400" aria-hidden>{open ? "▴" : "▾"}</span>
      </button>

      {open && (
        <div className="border-t border-ink-800 px-3 py-3">
          {msgs.length === 0 && (
            <div className="flex flex-wrap gap-1.5">
              {chips.map((c) => (
                <button
                  key={c}
                  onClick={() => ask(c)}
                  disabled={busy || !spec}
                  className="rounded-full border border-ink-700 px-3 py-1 text-xs text-ink-200 hover:border-accent disabled:opacity-50"
                >
                  {c}
                </button>
              ))}
            </div>
          )}

          <div className="mt-1 flex max-h-[460px] flex-col gap-3 overflow-y-auto">
            {msgs.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="ml-auto max-w-[85%] rounded-2xl bg-ink-800 px-3 py-2 text-sm text-ink-100">
                  {m.content}
                </div>
              ) : (
                <div key={m.id} className="max-w-full text-sm leading-relaxed text-ink-200">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={md}>{m.content}</ReactMarkdown>
                  {m.cards?.map((c, i) => (
                    <div key={i} className="mt-2 rounded-lg border border-accent/40 bg-accent/5 p-3 text-xs">
                      <div className="font-semibold text-ink-100">Suggested version: {c.name}</div>
                      <pre className="mt-1 whitespace-pre-wrap font-sans text-ink-300">{c.english}</pre>
                      <button
                        onClick={() => {
                          onLoadSpec(c.spec);
                          setLoaded(`${m.id}-${i}`);
                        }}
                        className="mt-2 rounded-md border border-ink-700 px-3 py-1 text-ink-100 hover:border-accent"
                      >
                        {loaded === `${m.id}-${i}` ? "Loaded in the editor ✓" : "Load into the editor"}
                      </button>
                    </div>
                  ))}
                </div>
              ),
            )}
            {busy && <div className="text-xs text-ink-400">Thinking…</div>}
            <div ref={end} />
          </div>

          {error && (
            <p className="mt-2 text-xs text-down" role="alert">
              {error}
            </p>
          )}

          <form
            className="mt-3 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              ask(text);
            }}
          >
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={spec ? "Ask anything about this strategy…" : "Build or open a strategy first"}
              disabled={!spec}
              aria-label="Ask about this strategy"
              className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-ink-100 outline-none focus:border-accent"
            />
            <button
              type="submit"
              disabled={busy || !text.trim() || !spec}
              className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
            >
              Ask
            </button>
          </form>
          {msgs.length > 0 && !busy && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {chips.slice(0, 3).map((c) => (
                <button key={c} onClick={() => ask(c)} className="rounded-full border border-ink-800 px-2.5 py-0.5 text-[11px] text-ink-400 hover:border-accent hover:text-ink-100">
                  {c}
                </button>
              ))}
            </div>
          )}
          <p className="mt-2 text-[10px] text-ink-400">Research and paper trading only, not investment advice. Counts toward your assistant messages.</p>
        </div>
      )}
    </section>
  );
}
