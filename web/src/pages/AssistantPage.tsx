import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { del, getJSON, postJSON, uploadFile, type KnowledgeDoc } from "../api";
import { MicButton } from "../components/MicButton";
import { LogoMark } from "../components/Logo";

interface Card {
  type: "strategy_draft";
  name: string;
  english: string;
  spec: Record<string, unknown>;
  assumptions: string[];
  model?: string;
}
interface Msg {
  id: number | string;
  role: "user" | "assistant";
  content: string;
  cards: Card[];
  doc_ids: string[];
  model?: string | null;
  remembered?: string[];
  tools_used?: string[];
}
interface Conv {
  id: string;
  title: string;
  updated_at: string;
}
interface Note {
  id: number;
  text: string;
  source: string;
}

const ACCEPT = ".pdf,.docx,.xlsx,.xlsm,.csv,.tsv,.txt,.md,.json,.yaml,.yml,.png,.jpg,.jpeg,.webp,.gif,.html,.xml,.log";
const SUGGESTIONS = [
  { title: "Sideways day", text: "Which of my saved strategies suits a sideways day, and why?" },
  { title: "Explain a strategy", text: "Describe the Opening Range Breakout strategy in simple words." },
  { title: "Review results", text: "How did my latest backtests and paper trades do?" },
  { title: "Check my notes", text: "What do my uploaded notes say about risk per trade?" },
];

// ---- small pieces -----------------------------------------------------------------------------
const Icon = ({ d, size = 16 }: { d: string; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d={d} />
  </svg>
);
const I = {
  plus: "M12 5v14M5 12h14",
  clip: "M21.4 11.1l-8.5 8.5a5.5 5.5 0 0 1-7.8-7.8l8.5-8.5a3.7 3.7 0 0 1 5.2 5.2l-8.5 8.5a1.8 1.8 0 0 1-2.6-2.6l7.8-7.8",
  up: "M12 19V5M5 12l7-7 7 7",
  trash: "M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6",
  copy: "M9 9h11v11H9zM5 15H4V4h11v1",
  sidebar: "M3 4h18v16H3zM9 4v16",
  brain: "M12 3a4 4 0 0 0-4 4 4 4 0 0 0-3 6 4 4 0 0 0 3 6 4 4 0 0 0 4 2V3zm0 0a4 4 0 0 1 4 4 4 4 0 0 1 3 6 4 4 0 0 1-3 6 4 4 0 0 1-4 2",
  x: "M18 6L6 18M6 6l12 12",
  file: "M14 3H6v18h12V7zM14 3v4h4",
  check: "M5 12l5 5L20 7",
};

function groupLabel(iso: string): string {
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  return days < 1 ? "Today" : days < 2 ? "Yesterday" : days < 8 ? "Previous 7 days" : "Older";
}

const md = {
  p: ({ children }: { children?: ReactNode }) => <p className="mb-3 last:mb-0">{children}</p>,
  ul: ({ children }: { children?: ReactNode }) => <ul className="mb-3 list-disc space-y-1 pl-5 last:mb-0">{children}</ul>,
  ol: ({ children }: { children?: ReactNode }) => <ol className="mb-3 list-decimal space-y-1 pl-5 last:mb-0">{children}</ol>,
  h1: ({ children }: { children?: ReactNode }) => <h3 className="mb-2 mt-4 text-base font-semibold first:mt-0">{children}</h3>,
  h2: ({ children }: { children?: ReactNode }) => <h3 className="mb-2 mt-4 text-[15px] font-semibold first:mt-0">{children}</h3>,
  h3: ({ children }: { children?: ReactNode }) => <h4 className="mb-1.5 mt-3 text-sm font-semibold first:mt-0">{children}</h4>,
  strong: ({ children }: { children?: ReactNode }) => <strong className="font-semibold text-ink-100">{children}</strong>,
  a: ({ href, children }: { href?: string; children?: ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer" className="text-accent underline underline-offset-2">{children}</a>
  ),
  blockquote: ({ children }: { children?: ReactNode }) => <blockquote className="mb-3 border-l-2 border-ink-600 pl-3 text-ink-300">{children}</blockquote>,
  hr: () => <hr className="my-4 border-ink-700" />,
  pre: ({ children }: { children?: ReactNode }) => (
    <pre className="num mb-3 overflow-x-auto rounded-lg border border-ink-700 bg-ink-950 p-3 text-[12px] leading-relaxed last:mb-0">{children}</pre>
  ),
  code: ({ className, children }: { className?: string; children?: ReactNode }) =>
    className ? <code className={className}>{children}</code> : <code className="num rounded bg-ink-800 px-1.5 py-0.5 text-[12px]">{children}</code>,
  table: ({ children }: { children?: ReactNode }) => (
    <div className="mb-3 overflow-x-auto rounded-lg border border-ink-700 last:mb-0"><table className="w-full text-left text-[13px]">{children}</table></div>
  ),
  th: ({ children }: { children?: ReactNode }) => <th className="border-b border-ink-700 bg-ink-850 px-3 py-1.5 font-semibold">{children}</th>,
  td: ({ children }: { children?: ReactNode }) => <td className="border-t border-ink-800 px-3 py-1.5">{children}</td>,
};

function Avatar() {
  return <LogoMark size={28} className="mt-0.5 shrink-0" />;
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked: nothing to do */
        }
      }}
      title="Copy"
      aria-label="Copy reply"
      className="flex items-center gap-1 rounded-md px-1.5 py-1 text-[11px] text-ink-400 hover:bg-ink-800 hover:text-ink-100"
    >
      <Icon d={done ? I.check : I.copy} size={13} /> {done ? "Copied" : "Copy"}
    </button>
  );
}

// ---- page -------------------------------------------------------------------------------------
export function AssistantPage() {
  const [convs, setConvs] = useState<Conv[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [docs, setDocs] = useState<KnowledgeDoc[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<KnowledgeDoc[]>([]); // files attached to the message being written
  const [busy, setBusy] = useState<"send" | "upload" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reading, setReading] = useState<string | null>(null); // name of the file being uploaded and read
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Record<string, string>>({}); // card key -> confirmation
  const [newNote, setNewNote] = useState("");
  const [dragging, setDragging] = useState(false);
  const [sidebar, setSidebar] = useState(() => window.innerWidth >= 900);
  const [drawer, setDrawer] = useState<"memory" | "files" | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const loadSide = useCallback(async () => {
    const [c, d, m] = await Promise.all([
      getJSON<{ conversations: Conv[] }>("/chat/conversations"),
      getJSON<{ docs: KnowledgeDoc[] }>("/knowledge/docs"),
      getJSON<{ notes: Note[] }>("/chat/memory"),
    ]);
    setConvs(c.conversations);
    setDocs(d.docs);
    setNotes(m.notes);
  }, []);

  useEffect(() => {
    loadSide().catch((e) => setError((e as Error).message));
  }, [loadSide]);

  // Scanned PDFs are read in the background: while any file is still being read, refresh the list every few seconds.
  const anyReading = docs.some((d) => d.status === "ocr");
  useEffect(() => {
    if (!anyReading) return;
    const t = setInterval(() => {
      getJSON<{ docs: KnowledgeDoc[] }>("/knowledge/docs").then((d) => {
        setDocs(d.docs);
        setPending((p) => p.map((x) => d.docs.find((y) => y.id === x.id) ?? x));
      }).catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, [anyReading]);

  useEffect(() => {
    // Scroll only the message list; scrollIntoView would also scroll the whole page.
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [msgs, busy]);

  useEffect(() => {
    const t = box.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = `${Math.min(t.scrollHeight, 200)}px`;
  }, [text, msgs.length]);

  const open = async (id: string | null) => {
    setActive(id);
    setError(null);
    if (window.innerWidth < 900) setSidebar(false);
    if (!id) return setMsgs([]);
    try {
      const c = await getJSON<{ messages: Msg[] }>(`/chat/conversations/${id}`);
      setMsgs(c.messages);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const attach = async (files: FileList | File[] | null) => {
    if (!files?.length) return;
    setBusy("upload");
    setError(null);
    setNotice(null);
    for (const f of Array.from(files)) {
      setReading(f.name);
      try {
        const d = await uploadFile(f);
        setPending((p) => (p.some((x) => x.id === d.id) ? p : [...p, d]));
        if (d.duplicate) setNotice(`${f.name} was already uploaded, so I used the existing copy.`);
        else setDocs((all) => [d, ...all]);
      } catch (e) {
        setError(`${f.name}: ${(e as Error).message}`);
      }
    }
    setReading(null);
    setBusy(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  // Files from the clipboard (screenshots) or a drag: images get a readable name, since browsers call them "image.png".
  const fromTransfer = (dt: DataTransfer | null): File[] =>
    Array.from(dt?.files ?? []).map((f) =>
      /^image\.(png|jpe?g|gif|webp)$/i.test(f.name)
        ? new File([f], `pasted-${new Date().toTimeString().slice(0, 8).replaceAll(":", "")}.${f.name.split(".").pop()}`, { type: f.type })
        : f,
    );

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (busy) return setError("Wait for the current upload or reply to finish.");
    attach(fromTransfer(e.dataTransfer));
  };

  const send = async (override?: string) => {
    const body = (override ?? text).trim();
    if (!body || busy) return;
    setBusy("send");
    setError(null);
    let id = active;
    try {
      if (!id) {
        id = (await postJSON<{ id: string }>("/chat/conversations", {})).id;
        setActive(id);
      }
      const docIds = pending.map((d) => d.id);
      setMsgs((m) => [...m, { id: `tmp${Date.now()}`, role: "user", content: body, cards: [], doc_ids: docIds }]);
      setText("");
      setPending([]);
      const reply = await postJSON<Msg>(`/chat/conversations/${id}/messages`, { text: body, doc_ids: docIds });
      setMsgs((m) => [...m, reply]);
      await loadSide();
    } catch (e) {
      setError((e as Error).message);
      if (id) open(id); // drop the optimistic message if the server stored nothing
    } finally {
      setBusy(null);
    }
  };

  const saveDraft = async (key: string, c: Card) => {
    try {
      const r = await postJSON<{ name: string; version: number }>("/strategies", {
        spec: c.spec,
        source_text: "Drafted by the assistant",
        assumptions: c.assumptions,
        translator_model: c.model ?? null,
      });
      setSaved((s) => ({ ...s, [key]: `Saved as ${r.name} v${r.version}. Find it on the Strategies tab.` }));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const removeConv = async (id: string) => {
    if (!window.confirm("Delete this conversation?")) return;
    await del(`/chat/conversations/${id}`).catch((e) => setError((e as Error).message));
    if (active === id) open(null);
    loadSide();
  };

  const empty = msgs.length === 0 && busy !== "send";
  const title = convs.find((c) => c.id === active)?.title ?? "New chat";

  // ---- composer (centred on an empty chat, docked at the bottom otherwise) ----
  const composer = (
    <div className="mx-auto w-full max-w-3xl px-4">
      {notice && <p className="mb-2 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-xs text-ink-300" role="status">{notice}</p>}
      {error && <p className="mb-2 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down" role="alert">{error}</p>}
      <div className="rounded-2xl border border-ink-700 bg-ink-850 p-2 shadow-lg shadow-black/20 focus-within:border-ink-600">
        {reading && (
          <div className="flex items-center gap-2 px-2 pb-2 pt-1 text-[12px] text-ink-300" role="status">
            <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-600 border-t-accent" />
            <span className="max-w-[260px] truncate">Reading {reading}…</span>
            <span className="text-ink-400">large PDFs can take a minute</span>
          </div>
        )}
        {pending.length > 0 && (
          <div className="flex flex-wrap gap-1.5 px-1 pb-2 pt-1">
            {pending.map((d) => (
              <span key={d.id} className="flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 py-1 pl-2 pr-1 text-[12px] text-ink-300">
                <Icon d={I.file} size={13} />
                <span className="max-w-[180px] truncate">{d.filename}</span>
                {d.status === "ocr" && <span className="text-accent">reading scanned pages…</span>}
                {d.status !== "ready" && d.status !== "ocr" && <span className="text-down">{d.status === "no_vision" ? "can't read image yet" : "no text found"}</span>}
                <button onClick={() => setPending((p) => p.filter((x) => x.id !== d.id))} aria-label="Remove attachment" className="rounded p-0.5 text-ink-400 hover:bg-ink-800 hover:text-down">
                  <Icon d={I.x} size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={box}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              send();
            }
          }}
          onPaste={(e) => {
            const files = fromTransfer(e.clipboardData);
            if (files.length === 0) return; // plain text paste: leave it alone
            e.preventDefault();
            if (busy) return setError("Wait for the current upload or reply to finish.");
            attach(files);
          }}
          rows={1}
          placeholder="Message your assistant…"
          className="block max-h-[200px] w-full resize-none bg-transparent px-2 py-1.5 text-[15px] leading-relaxed text-ink-100 outline-none placeholder:text-ink-400"
        />
        <div className="flex items-center gap-1 pt-1">
          <input ref={fileInput} type="file" multiple accept={ACCEPT} className="hidden" onChange={(e) => attach(e.target.files)} />
          <button
            onClick={() => fileInput.current?.click()}
            disabled={!!busy}
            title="Attach PDF, Word, Excel, text or image files (or paste / drop them)"
            aria-label="Attach files"
            className="grid h-9 w-9 place-items-center rounded-full text-ink-300 hover:bg-ink-800 hover:text-ink-100 disabled:opacity-50"
          >
            {busy === "upload" ? <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-accent" /> : <Icon d={I.clip} size={18} />}
          </button>
          <div className="ml-auto flex items-center gap-1">
            <MicButton compact disabled={!!busy} onError={setError} onText={(t) => setText((cur) => (cur.trim() ? `${cur.trim()} ${t}` : t))} />
            <button
              onClick={() => send()}
              disabled={!!busy || !text.trim()}
              aria-label="Send message"
              title="Send (Enter)"
              className="grid h-9 w-9 place-items-center rounded-full bg-accent text-ink-950 transition hover:brightness-110 disabled:bg-ink-700 disabled:text-ink-400"
            >
              <Icon d={I.up} size={18} />
            </button>
          </div>
        </div>
      </div>
      <p className="px-2 pb-1 pt-2 text-center text-[11px] text-ink-400">
        Read-only assistant: it can't place orders, and it can be wrong, so check numbers that matter. Shift+Enter for a new line.
      </p>
    </div>
  );

  return (
    <div
      className="relative mx-auto flex min-h-0 w-full max-w-[1600px] flex-1 overflow-hidden border-x border-ink-800 bg-ink-950"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes("Files")) {
          e.preventDefault();
          setDragging(true);
        }
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setDragging(false);
      }}
      onDrop={onDrop}
    >
      {dragging && (
        <div className="pointer-events-none absolute inset-0 z-40 grid place-items-center bg-ink-950/85">
          <div className="rounded-2xl border-2 border-dashed border-accent px-10 py-8 text-center">
            <div className="text-sm font-semibold text-accent">Drop files to attach</div>
            <div className="mt-1 text-xs text-ink-300">PDF, Word, Excel, text and images</div>
          </div>
        </div>
      )}

      {/* ---- conversations ---- */}
      {sidebar && (
        <aside className="flex w-64 shrink-0 flex-col border-r border-ink-800 bg-ink-900 max-md:absolute max-md:inset-y-0 max-md:left-0 max-md:z-30 max-md:shadow-2xl">
          <div className="p-3">
            <button onClick={() => open(null)} className="flex w-full items-center gap-2 rounded-lg border border-ink-700 px-3 py-2 text-sm text-ink-100 hover:bg-ink-800">
              <Icon d={I.plus} /> New chat
            </button>
          </div>
          <nav className="flex-1 overflow-y-auto px-2 pb-3" aria-label="Conversations">
            {convs.length === 0 && <p className="px-2 py-3 text-xs text-ink-400">Your chats will appear here.</p>}
            {convs.map((c, i) => (
              <div key={c.id}>
                {(i === 0 || groupLabel(convs[i - 1].updated_at) !== groupLabel(c.updated_at)) && (
                  <div className="px-2 pb-1 pt-3 text-[11px] font-medium uppercase tracking-wider text-ink-400">{groupLabel(c.updated_at)}</div>
                )}
                <div className={`group flex items-center rounded-lg ${active === c.id ? "bg-ink-800" : "hover:bg-ink-850"}`}>
                  <button onClick={() => open(c.id)} title={c.title} className="min-w-0 flex-1 truncate px-2.5 py-2 text-left text-[13px] text-ink-100">
                    {c.title}
                  </button>
                  <button onClick={() => removeConv(c.id)} aria-label="Delete chat" className="mr-1 hidden rounded p-1.5 text-ink-400 hover:bg-ink-700 hover:text-down group-hover:block">
                    <Icon d={I.trash} size={14} />
                  </button>
                </div>
              </div>
            ))}
          </nav>
        </aside>
      )}

      {/* ---- chat ---- */}
      <section className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-ink-800 px-3 py-2">
          <button onClick={() => setSidebar((s) => !s)} aria-label="Toggle conversations" title="Conversations" className="rounded-lg p-2 text-ink-300 hover:bg-ink-800 hover:text-ink-100">
            <Icon d={I.sidebar} />
          </button>
          <h1 className="min-w-0 flex-1 truncate text-sm font-medium text-ink-100">{title}</h1>
          <button onClick={() => setDrawer("memory")} className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-2.5 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-100">
            <Icon d={I.brain} size={14} /> Memory
            {notes.length > 0 && <span className="rounded-full bg-ink-700 px-1.5 text-[10px]">{notes.length}</span>}
          </button>
          <button onClick={() => setDrawer("files")} className="flex items-center gap-1.5 rounded-lg border border-ink-700 px-2.5 py-1.5 text-xs text-ink-300 hover:bg-ink-800 hover:text-ink-100">
            <Icon d={I.file} size={14} /> Files
            {docs.length > 0 && <span className="rounded-full bg-ink-700 px-1.5 text-[10px]">{docs.length}</span>}
          </button>
        </div>

        {empty ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-6 overflow-y-auto py-6">
            <div className="text-center">
              <LogoMark size={52} className="mx-auto mb-4" />
              <h2 className="text-2xl font-semibold tracking-tight text-ink-100">What can I help with?</h2>
              <p className="mx-auto mt-2 max-w-md text-sm text-ink-400">
                Ask about your strategies, backtests, paper trades and uploaded notes. Paste or drop screenshots and files.
              </p>
            </div>
            {composer}
            <div className="grid w-full max-w-3xl grid-cols-1 gap-2 px-4 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button key={s.title} onClick={() => send(s.text)} className="rounded-xl border border-ink-800 bg-ink-900 px-4 py-3 text-left hover:border-ink-600 hover:bg-ink-850">
                  <div className="text-[13px] font-medium text-ink-100">{s.title}</div>
                  <div className="mt-0.5 line-clamp-2 text-xs text-ink-400">{s.text}</div>
                </button>
              ))}
            </div>
          </div>
        ) : (
          <>
            <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto">
              <ol className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 py-6">
                {msgs.map((m) => {
                  const files = m.doc_ids.map((id) => docs.find((d) => d.id === id)?.filename ?? "file");
                  return m.role === "user" ? (
                    <li key={m.id} className="flex flex-col items-end gap-1.5">
                      {files.length > 0 && (
                        <div className="flex flex-wrap justify-end gap-1.5">
                          {files.map((f, i) => (
                            <span key={i} className="flex items-center gap-1.5 rounded-lg border border-ink-700 bg-ink-900 px-2 py-1 text-[12px] text-ink-300">
                              <Icon d={I.file} size={13} /> {f}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-ink-800 px-4 py-2.5 text-[15px] leading-relaxed text-ink-100">{m.content}</div>
                    </li>
                  ) : (
                    <li key={m.id} className="flex gap-3">
                      <Avatar />
                      <div className="min-w-0 flex-1">
                        <div className="text-[15px] leading-7 text-ink-100">
                          <ReactMarkdown remarkPlugins={[remarkGfm]} components={md}>{m.content}</ReactMarkdown>
                        </div>
                        {m.cards.map((c, i) => {
                          const key = `${m.id}-${i}`;
                          return (
                            <div key={key} className="mt-3 overflow-hidden rounded-xl border border-ink-700 bg-ink-900">
                              <div className="flex items-center justify-between border-b border-ink-800 px-4 py-2">
                                <div className="text-[13px] font-semibold text-ink-100">
                                  <span className="mr-2 rounded bg-accent/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-accent">Draft</span>
                                  {c.name}
                                </div>
                                {saved[key] ? (
                                  <span className="flex items-center gap-1 text-xs text-up"><Icon d={I.check} size={13} /> Saved</span>
                                ) : (
                                  <button onClick={() => saveDraft(key, c)} className="rounded-lg bg-accent px-3 py-1 text-xs font-semibold text-ink-950 hover:brightness-110">
                                    Save strategy
                                  </button>
                                )}
                              </div>
                              <pre className="max-h-64 overflow-auto whitespace-pre-wrap px-4 py-3 font-sans text-[13px] leading-relaxed text-ink-300">{c.english}</pre>
                              {c.assumptions.length > 0 && <p className="border-t border-ink-800 px-4 py-2 text-[11px] text-ink-400">Assumptions: {c.assumptions.join("; ")}</p>}
                              {saved[key] && <p className="border-t border-ink-800 px-4 py-2 text-[11px] text-up">{saved[key]}</p>}
                            </div>
                          );
                        })}
                        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-ink-400">
                          <CopyButton text={m.content} />
                          {m.tools_used && m.tools_used.length > 0 && <span>Looked at: {[...new Set(m.tools_used)].join(", ")}</span>}
                          {m.remembered && m.remembered.length > 0 && (
                            <button onClick={() => setDrawer("memory")} className="text-accent hover:underline">
                              Remembered {m.remembered.length} note{m.remembered.length > 1 ? "s" : ""}
                            </button>
                          )}
                          {m.model && <span className="num">{m.model}</span>}
                        </div>
                      </div>
                    </li>
                  );
                })}
                {busy === "send" && (
                  <li className="flex items-center gap-3" aria-live="polite">
                    <Avatar />
                    <div className="flex gap-1" aria-label="Thinking">
                      {[0, 150, 300].map((d) => (
                        <span key={d} className="h-2 w-2 animate-bounce rounded-full bg-ink-400" style={{ animationDelay: `${d}ms` }} />
                      ))}
                    </div>
                  </li>
                )}
              </ol>
            </div>
            <div className="pb-2 pt-1">{composer}</div>
          </>
        )}
      </section>

      {/* ---- memory & files drawer ---- */}
      {drawer && (
        <div className="absolute inset-0 z-30 flex justify-end bg-black/50" onClick={() => setDrawer(null)}>
          <aside className="flex h-full w-full max-w-sm flex-col border-l border-ink-700 bg-ink-900 shadow-2xl" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Memory and files">
            <div className="flex items-center gap-1 border-b border-ink-800 px-3 py-2">
              {(["memory", "files"] as const).map((t) => (
                <button key={t} onClick={() => setDrawer(t)} className={`rounded-lg px-3 py-1.5 text-sm capitalize ${drawer === t ? "bg-ink-800 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}>
                  {t}
                </button>
              ))}
              <button onClick={() => setDrawer(null)} aria-label="Close" className="ml-auto rounded-lg p-2 text-ink-400 hover:bg-ink-800 hover:text-ink-100">
                <Icon d={I.x} />
              </button>
            </div>

            {drawer === "memory" ? (
              <div className="flex flex-1 flex-col overflow-hidden">
                <p className="px-4 pt-3 text-xs text-ink-400">What the assistant remembers about how you trade. It uses these in every chat. Remove anything wrong.</p>
                <ul className="flex-1 space-y-2 overflow-y-auto p-4">
                  {notes.map((n) => (
                    <li key={n.id} className="flex items-start gap-2 rounded-lg border border-ink-800 bg-ink-850 px-3 py-2 text-[13px] text-ink-100">
                      <span className="flex-1">{n.text}</span>
                      <button onClick={async () => { await del(`/chat/memory/${n.id}`); loadSide(); }} aria-label="Forget this" className="rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-down">
                        <Icon d={I.trash} size={14} />
                      </button>
                    </li>
                  ))}
                  {notes.length === 0 && <li className="text-sm text-ink-400">Nothing yet. Tell it how you trade, e.g. "I only trade NIFTY weekly options".</li>}
                </ul>
                <form className="flex gap-2 border-t border-ink-800 p-3" onSubmit={async (e) => {
                  e.preventDefault();
                  if (newNote.trim().length < 3) return;
                  await postJSON("/chat/memory", { text: newNote.trim() }).catch((er) => setError((er as Error).message));
                  setNewNote("");
                  loadSide();
                }}>
                  <input value={newNote} onChange={(e) => setNewNote(e.target.value)} placeholder="Add something to remember…" className="min-w-0 flex-1 rounded-lg border border-ink-700 bg-ink-850 px-3 py-2 text-sm text-ink-100 outline-none focus:border-accent" />
                  <button className="rounded-lg bg-ink-700 px-3 text-sm text-ink-100 hover:bg-ink-600">Add</button>
                </form>
              </div>
            ) : (
              <div className="flex flex-1 flex-col overflow-hidden">
                <p className="px-4 pt-3 text-xs text-ink-400">Files you attached. The assistant searches them when you ask. Only the extracted text is kept.</p>
                <ul className="flex-1 space-y-2 overflow-y-auto p-4">
                  {docs.map((d) => (
                    <li key={d.id} className="rounded-lg border border-ink-800 bg-ink-850 px-3 py-2">
                      <div className="flex items-center gap-2">
                        <Icon d={I.file} size={14} />
                        <span className="min-w-0 flex-1 truncate text-[13px] text-ink-100" title={d.filename}>{d.filename}</span>
                        <button onClick={async () => { if (window.confirm(`Remove ${d.filename}?`)) { await del(`/knowledge/docs/${d.id}`); loadSide(); } }} aria-label="Remove file" className="rounded p-1 text-ink-400 hover:bg-ink-700 hover:text-down">
                          <Icon d={I.trash} size={14} />
                        </button>
                      </div>
                      <div className="mt-1 text-[11px] text-ink-400">
                        {d.kind} · {d.status === "ready" ? d.summary || `${d.chars} characters` : d.status === "ocr" ? "reading scanned pages in the background…" : d.status === "no_vision" ? "no image model available to read this" : "no text found (scanned document?)"}
                      </div>
                    </li>
                  ))}
                  {docs.length === 0 && <li className="text-sm text-ink-400">No files yet. Attach, paste or drop strategy docs, journals or screenshots.</li>}
                </ul>
                <div className="border-t border-ink-800 p-3">
                  <button onClick={() => { setDrawer(null); fileInput.current?.click(); }} className="w-full rounded-lg border border-ink-700 px-3 py-2 text-sm text-ink-100 hover:bg-ink-800">Upload files</button>
                </div>
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}
