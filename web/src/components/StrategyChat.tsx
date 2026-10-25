import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getJSON, postJSON, sendJSON } from "../api";
import { md } from "../pages/AssistantPage";
import { PromptBox } from "../ui/PromptBox";

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

// Which conversation belongs to which strategy, kept per account (a preference row), so a strategy's chat comes back
// after the drawer closes, the page reloads or on another device. The conversations themselves are ordinary chats
// (they also show in the Assistant's history), and the server sends each one's history to the model.
const PREF = "strategy-chats";
type ChatMap = Record<string, string>;

async function readMap(): Promise<ChatMap> {
  const r = await getJSON<{ value: ChatMap | null }>(`/prefs/${PREF}`);
  return r.value && typeof r.value === "object" && !Array.isArray(r.value) ? r.value : {};
}

async function writeMap(name: string, id: string | null) {
  const map = await readMap().catch(() => ({}) as ChatMap);
  if (id) map[name] = id;
  else delete map[name];
  await sendJSON("PUT", `/prefs/${PREF}`, { value: map }, { quiet: true });
}

/** Ask questions about the strategy that is open, before or after a backtest. The server tells the assistant the rules
 *  and the latest backtest, so answers are about this strategy and these numbers. The conversation is kept per
 *  strategy and reopens where it left off. */
export function StrategyChat({
  spec,
  backtestId,
  onLoadSpec,
}: {
  spec: Record<string, unknown> | null;
  backtestId: string | null;
  onLoadSpec: (s: Record<string, unknown>) => void;
}) {
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState<string | null>(null);
  const conv = useRef<string | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const [restoring, setRestoring] = useState(true);
  const name = String(spec?.name ?? "draft");

  // Reopen this strategy's earlier conversation, if there is one.
  useEffect(() => {
    let alive = true;
    conv.current = null;
    setMsgs([]);
    setRestoring(true);
    (async () => {
      try {
        const id = (await readMap())[name];
        if (!id) return;
        const c = await getJSON<{ messages: Msg[] }>(`/chat/conversations/${id}`);
        if (!alive) return;
        conv.current = id;
        setMsgs(c.messages.filter((m) => m.role === "user" || m.role === "assistant"));
      } catch {
        /* deleted in the Assistant, or not reachable: start a new one */
      } finally {
        if (alive) setRestoring(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [name]);

  const newChat = () => {
    conv.current = null;
    setMsgs([]);
    setError(null);
    writeMap(name, null).catch(() => undefined);
  };

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
      if (!conv.current) {
        conv.current = (await postJSON<{ id: string }>("/chat/conversations", {})).id;
        writeMap(name, conv.current).catch(() => undefined);
      }
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
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2 border-b border-ink-800 px-4 py-2 text-[11px] text-ink-400">
        <span className="min-w-0 flex-1">
          {backtestId ? "It knows the rules and the latest backtest." : "Ask about the rules, or run a backtest first to discuss the results."}
          {msgs.length > 0 && " It remembers this conversation."}
        </span>
        {msgs.length > 0 && (
          <button onClick={newChat} disabled={busy} title="Start a fresh conversation about this strategy (the old one stays in the Assistant's history)"
            className="shrink-0 rounded-md border border-ink-700 px-2 py-0.5 text-[11px] text-ink-300 hover:border-accent hover:text-ink-100 disabled:opacity-40">
            New chat
          </button>
        )}
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-4 py-3">
        {restoring && <div className="text-xs text-ink-500">Loading the earlier conversation…</div>}
        {!restoring && msgs.length === 0 && (
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
        {msgs.map((m) =>
          m.role === "user" ? (
            <div key={m.id} className="fade-in ml-auto max-w-[85%] whitespace-pre-wrap rounded-2xl bg-ink-800 px-3 py-2 text-sm text-ink-100">
              {m.content}
            </div>
          ) : (
            <div key={m.id} className="fade-in max-w-full text-sm leading-relaxed text-ink-200">
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
        {busy && (
          <div className="flex items-center gap-1 text-xs text-ink-400" aria-live="polite">
            <span className="chat-dot" />
            <span className="chat-dot" style={{ animationDelay: "0.15s" }} />
            <span className="chat-dot" style={{ animationDelay: "0.3s" }} />
            <span className="ml-1">Thinking…</span>
          </div>
        )}
        {error && (
          <p className="text-xs text-down" role="alert">
            {error}
          </p>
        )}
        <div ref={end} />
      </div>

      <div className="border-t border-ink-800 px-4 py-3">
        {msgs.length > 0 && !busy && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {chips.slice(0, 3).map((c) => (
              <button key={c} onClick={() => ask(c)} className="rounded-full border border-ink-800 px-2.5 py-0.5 text-[11px] text-ink-400 hover:border-accent hover:text-ink-100">
                {c}
              </button>
            ))}
          </div>
        )}
        <PromptBox
          value={text}
          onChange={setText}
          onSubmit={() => ask(text)}
          label="Ask about this strategy"
          placeholder={spec ? "Ask anything about this strategy…" : "Build or open a strategy first"}
          disabled={!spec}
          actions={
            <button
              onClick={() => ask(text)}
              disabled={busy || !text.trim() || !spec}
              className="rounded-md bg-accent px-3 py-1 text-xs font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
            >
              Ask
            </button>
          }
        />
        <p className="mt-2 text-[10px] text-ink-400">Research and paper trading only, not investment advice. Counts toward your assistant messages.</p>
      </div>
    </div>
  );
}
