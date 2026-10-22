import { useEffect, useMemo, useRef, useState } from "react";
import {
  getJSON,
  postJSON,
  uploadFile,
  type BacktestResult,
  type SavedStrategy,
  type Translation,
  type TranslatorInfo,
  type Validation,
} from "../api";
import { Results } from "../components/backtest/Results";
import { QuestionDialog } from "../components/QuestionDialog";
import { MicButton } from "../components/MicButton";
import { StrategyLibrary } from "../components/StrategyLibrary";
import { ReadyMade } from "../components/ReadyMade";

const EXAMPLES = [
  "NIFTY 5 min: buy the ATM call when futures are above VWAP, EMA 9 crosses above EMA 21 and RSI(14) is above 60. Mirror it for puts. Only 9:45 to 14:30, skip if ADX(14) is below 20. Stop 20%, target 40%, trail 10% after 25% profit, max 2 trades a day.",
  "BANKNIFTY 15 min opening-range breakout: buy calls when the close breaks above the opening-range high and RSI(14) > 55; puts on a break below the opening-range low with RSI < 45. Stop 25%, target 60%, exit after 12 candles, 1 trade a day.",
];

const iso = (d: Date) => d.toISOString().slice(0, 10);

export function StrategiesPage() {
  const [text, setText] = useState("");
  const [refine, setRefine] = useState("");
  const [json, setJson] = useState("");
  const [validation, setValidation] = useState<Validation | null>(null);
  const [translation, setTranslation] = useState<Translation | null>(null);
  const [saved, setSaved] = useState<SavedStrategy[]>([]);
  const [current, setCurrent] = useState<SavedStrategy | null>(null);
  const [versions, setVersions] = useState<SavedStrategy[]>([]);
  const [ai, setAi] = useState<TranslatorInfo | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [bt, setBt] = useState({ underlying: "NIFTY", start: iso(new Date(Date.now() - 365 * 864e5)), end: iso(new Date()), source: "auto", pricing: "synthetic", lots: 1, max_daily_loss: 5000, capital: 100000, quantity: "" });
  const timer = useRef<ReturnType<typeof setTimeout>>();
  // The AI asked questions instead of building: shown as a dialog with tappable answers.
  const [ask, setAsk] = useState<{ input: string; base: Record<string, unknown> | null; t: Translation } | null>(null);

  const refreshSaved = () => getJSON<SavedStrategy[]>("/strategies").then(setSaved).catch(() => {});
  useEffect(() => {
    getJSON<TranslatorInfo>("/strategies/translator").then(setAi).catch(() => setAi(null));
  }, []);

  useEffect(() => {
    refreshSaved();
  }, []);

  const parsed = useMemo(() => {
    try {
      return json.trim() ? (JSON.parse(json) as Record<string, unknown>) : null;
    } catch {
      return undefined; // invalid JSON
    }
  }, [json]);

  // Live validation (debounced) whenever the JSON changes.
  useEffect(() => {
    clearTimeout(timer.current);
    if (parsed === undefined) {
      setValidation({ valid: false, errors: ["Not valid JSON"], english: null, spec_hash: null });
      return;
    }
    if (parsed === null) {
      setValidation(null);
      return;
    }
    timer.current = setTimeout(() => {
      postJSON<Validation>("/strategies/validate", { spec: parsed }).then(setValidation).catch((e) => setError(e.message));
    }, 350);
    return () => clearTimeout(timer.current);
  }, [parsed]);

  const setSpec = (spec: Record<string, unknown>) => setJson(JSON.stringify(spec, null, 2));

  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const translate = (input: string, base?: Record<string, unknown> | null) =>
    run("translate", async () => {
      const t = await postJSON<Translation>("/strategies/translate", { text: input, base_spec: base ?? null });
      setTranslation(t);
      if (!t.spec && t.questions.length > 0) setAsk({ input, base: base ?? null, t });
      if (t.spec) {
        setSpec(t.spec);
        setCurrent(null);
        setRefine("");
      }
    });

  const answerQuestions = (answers: (string | null)[]) => {
    if (!ask) return;
    const NL = String.fromCharCode(10);
    const lines = ask.t.questions.flatMap((q, i) => (answers[i] ? [`- ${q} -> ${answers[i]}`] : []));
    const skipped = answers.some((a) => !a);
    const next = [
      ask.input,
      "",
      "My answers to your questions:",
      lines.length ? lines.join(NL) : "(none)",
      ...(skipped ? ["For anything I did not answer, choose sensible defaults and list them under assumptions."] : []),
    ].join(NL);
    const base = ask.base;
    setAsk(null);
    translate(next, base);
  };

  const askAssistant = () => {
    if (!ask) return;
    const NL = String.fromCharCode(10);
    const qs = ask.t.questions.map((q, i) => `${i + 1}. ${q}`).join(NL);
    try {
      sessionStorage.setItem(
        "assistant-prefill",
        [
          `I am building a strategy and the strategy builder needs more detail. My description: "${ask.input}"`,
          "",
          "It asked:",
          qs,
          "",
          "Please go through these with me one at a time, suggest sensible answers, then draft the strategy.",
        ].join(NL),
      );
    } catch {
      /* storage unavailable: the Assistant just opens empty */
    }
    setAsk(null);
    location.hash = "#/assistant";
  };

  const loadTemplate = () =>
    run("template", async () => {
      const t = await getJSON<{ spec: Record<string, unknown> }>("/strategies/template");
      setSpec(t.spec);
      setTranslation(null);
      setCurrent(null);
    });

  const open = (s: SavedStrategy) => {
    setSpec(s.spec);
    setCurrent(s);
    setTranslation(null);
    setText(s.source_text ?? "");
    setResult(null);
    getJSON<SavedStrategy[]>(`/strategies/${s.id}/versions`).then(setVersions).catch(() => setVersions([]));
    const unds = (s.spec.underlyings as string[] | undefined) ?? ["NIFTY"];
    setBt((b) => ({ ...b, underlying: unds.includes(b.underlying) ? b.underlying : unds[0] }));
  };

  const save = () =>
    run("save", async () => {
      const s = await postJSON<SavedStrategy>("/strategies", {
        spec: parsed,
        source_text: text || null,
        assumptions: translation?.assumptions ?? current?.assumptions ?? [],
        translator_model: translation?.model ?? null,
      });
      open(s);
      refreshSaved();
    });

  const backtest = () =>
    run("backtest", async () => {
      // Always backtest a saved version, so every run is traceable to an immutable spec.
      const s = await postJSON<SavedStrategy>("/strategies", {
        spec: parsed,
        source_text: text || null,
        assumptions: translation?.assumptions ?? current?.assumptions ?? [],
        translator_model: translation?.model ?? null,
      });
      open(s);
      refreshSaved();
      const r = await postJSON<BacktestResult>("/backtests", {
        underlying: bt.underlying,
        start: bt.start,
        end: bt.end,
        timeframe: (s.spec.timeframe as string) ?? "5m",
        source: bt.source,
        pricing: bt.pricing,
        strategy_id: s.id,
        engine_params: isEquity ? { capital: bt.capital } : { lots: bt.lots, max_daily_loss: bt.max_daily_loss },
        ...(isEquity && Number(bt.quantity) >= 1 ? { quantity: Math.floor(Number(bt.quantity)) } : {}),
      });
      setResult(r);
    });

  const isEquity = (parsed as Record<string, unknown> | null | undefined)?.market === "equity";
  const dirty = current && validation?.spec_hash && validation.spec_hash !== current.spec_hash;
  const underlyings = ((parsed as Record<string, unknown> | null | undefined)?.underlyings as string[] | undefined) ?? ["NIFTY", "BANKNIFTY"];

  return (
    <main className="mx-auto grid w-full max-w-[1600px] flex-1 grid-cols-1 gap-4 p-4 sm:p-6 lg:grid-cols-[340px_minmax(0,1fr)]">
      {ask && (
        <QuestionDialog
          questions={ask.t.questions}
          choices={ask.t.choices ?? []}
          busy={busy === "translate"}
          onSubmit={answerQuestions}
          onAssistant={askAssistant}
          onClose={() => setAsk(null)}
        />
      )}
      <aside className="flex flex-col gap-4">
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h1 className="mb-1 text-sm font-semibold">Describe a strategy</h1>
          <p className="mb-3 text-[11px] leading-relaxed text-ink-400">
            AI translates your description into rules. It never runs or trades anything: you review the rules, then
            the deterministic engine backtests them.
          </p>
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={7}
            placeholder="e.g. Buy NIFTY calls when the 9 EMA crosses above the 21 EMA and RSI is above 60…"
            className="w-full resize-y rounded border border-ink-700 bg-ink-850 p-2 text-xs leading-relaxed text-ink-100 outline-none focus:border-accent"
          />
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              onClick={() => translate(text)}
              disabled={!!busy || text.trim().length < 5}
              className="rounded-md bg-accent px-3 py-1.5 font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
            >
              {busy === "translate" ? "Translating…" : "Translate with AI"}
            </button>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.json,.png,.jpg,.jpeg,.webp" className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                setBusy("attach");
                setError(null);
                try {
                  const r = (await uploadFile(f, false)) as unknown as { text: string; status: string };
                  if (!r.text.trim()) throw new Error(r.status === "no_text" ? "No text found in that file (is it a scan?)." : "Nothing readable in that file.");
                  setText((cur) => (cur.trim() ? `${cur.trim()}

${r.text.trim()}` : r.text.trim()));
                } catch (er) {
                  setError((er as Error).message);
                } finally {
                  setBusy(null);
                  e.target.value = "";
                }
              }} />
            <button onClick={() => fileRef.current?.click()} disabled={!!busy} title="Attach a PDF, Word, Excel, text or image file describing the strategy"
              className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-300 hover:text-ink-100 disabled:opacity-50">
              {busy === "attach" ? "Reading…" : "Attach file"}
            </button>
            <MicButton disabled={!!busy} onError={setError}
              onText={(t) => setText((cur) => (cur.trim() ? `${cur.trim()} ${t}` : t))} />
            <button onClick={loadTemplate} disabled={!!busy} className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-300 hover:text-ink-100">
              Start from Momentum #1
            </button>
          </div>
          {ai && (
            <p className={`num mt-2 text-[10px] ${ai.configured ? "text-ink-400" : "text-down"}`}>
              {ai.provider
                ? `AI: ${ai.provider} · ${ai.model}${ai.fallbacks?.length ? ` · then ${ai.fallbacks.join(", ")}` : ""}${ai.note ? ` — ${ai.note}` : ""}`
                : ai.configured ? "" : "AI translation is not available right now."}
            </p>
          )}
          <details className="mt-3 text-[11px] text-ink-400">
            <summary className="cursor-pointer hover:text-ink-100">Example descriptions</summary>
            <ul className="mt-2 flex flex-col gap-2">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button onClick={() => setText(ex)} className="text-left leading-relaxed text-ink-300 hover:text-ink-100">
                    “{ex}”
                  </button>
                </li>
              ))}
            </ul>
          </details>
        </section>

        <ReadyMade onPick={(_name, spec) => { setSpec(spec); setTranslation(null); setCurrent(null); setText(""); setResult(null); }} />

        <StrategyLibrary saved={saved} current={current} onOpen={open} onChanged={refreshSaved} />
      </aside>

      <div className="flex min-w-0 flex-col gap-4">
        {error && (
          <div className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down" role="alert">
            {error}
          </div>
        )}

        {translation && <TranslationNotes t={translation} />}

        {json === "" ? (
          <div className="grid min-h-[300px] place-items-center rounded-lg border border-dashed border-ink-700 p-8 text-center text-sm text-ink-400">
            <div>
              <p className="mb-1 text-ink-300">No strategy loaded</p>
              <p className="text-xs">Describe one, pick a ready-made strategy (Momentum, Trend, Scalp, Sideways), or open a saved strategy.</p>
            </div>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
              <section className="rounded-lg border border-ink-800 bg-ink-900">
                <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2">
                  <h2 className="text-sm font-semibold">What will run</h2>
                  {current && (
                    <span className="num rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-ink-300">
                      {current.name} v{current.version}
                      {dirty ? " · edited" : ""}
                    </span>
                  )}
                  <span className={`ml-auto text-[11px] ${validation?.valid ? "text-up" : "text-down"}`}>
                    {validation == null ? "" : validation.valid ? "✓ valid" : `✗ ${validation.errors.length} problem${validation.errors.length === 1 ? "" : "s"}`}
                  </span>
                </div>
                {validation?.valid && validation.english ? (
                  <pre className="max-h-[440px] overflow-auto whitespace-pre-wrap px-3 py-3 font-sans text-xs leading-relaxed text-ink-100">{validation.english}</pre>
                ) : (
                  <ul className="flex flex-col gap-1 px-3 py-3 text-xs text-down">
                    {(validation?.errors ?? []).map((e) => (
                      <li key={e}>• {e}</li>
                    ))}
                  </ul>
                )}
                <p className="border-t border-ink-800 px-3 py-2 text-[10px] text-ink-400">
                  Generated by code from the rules, not by the AI, so it is exactly what the engine executes.
                </p>
              </section>

              <section className="flex flex-col rounded-lg border border-ink-800 bg-ink-900">
                <div className="flex items-center gap-2 border-b border-ink-800 px-3 py-2">
                  <h2 className="text-sm font-semibold">Rules (JSON)</h2>
                  <span className="text-[11px] text-ink-400">edit directly; validated as you type</span>
                </div>
                <textarea
                  value={json}
                  onChange={(e) => setJson(e.target.value)}
                  spellCheck={false}
                  aria-label="Strategy spec JSON"
                  className="num min-h-[360px] flex-1 resize-y bg-ink-950/60 p-3 text-[11px] leading-relaxed text-ink-100 outline-none"
                />
              </section>
            </div>

            <section className="rounded-lg border border-ink-800 bg-ink-900 p-3 text-xs">
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex min-w-[240px] flex-1 flex-col gap-1">
                  <span className="text-ink-400">Refine in plain English</span>
                  <input
                    value={refine}
                    onChange={(e) => setRefine(e.target.value)}
                    placeholder="e.g. make the stop 15% and only trade after 10:00"
                    className="rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-ink-100 outline-none focus:border-accent"
                  />
                </label>
                <button
                  onClick={() => translate(refine, parsed ?? null)}
                  disabled={!!busy || refine.trim().length < 5 || !parsed}
                  className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-100 hover:border-accent disabled:opacity-50"
                >
                  {busy === "translate" ? "Updating…" : "Apply change"}
                </button>
                <button
                  onClick={save}
                  disabled={!!busy || !validation?.valid}
                  className="rounded-md border border-ink-700 px-3 py-1.5 text-ink-100 hover:border-accent disabled:opacity-50"
                >
                  {busy === "save" ? "Saving…" : "Save version"}
                </button>
              </div>

              <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-ink-800 pt-3">
                {!isEquity && (
                  <Field label="Underlying">
                    <select value={bt.underlying} onChange={(e) => setBt({ ...bt, underlying: e.target.value })} className={inp}>
                      {underlyings.map((u) => (
                        <option key={u}>{u}</option>
                      ))}
                    </select>
                  </Field>
                )}
                <Field label="From">
                  <input type="date" value={bt.start} onChange={(e) => setBt({ ...bt, start: e.target.value })} className={inp} />
                </Field>
                <Field label="To">
                  <input type="date" value={bt.end} onChange={(e) => setBt({ ...bt, end: e.target.value })} className={inp} />
                </Field>
                {isEquity ? (
                  <Field label="Capital ₹">
                    <input type="number" min={10000} step={10000} value={bt.capital} onChange={(e) => setBt({ ...bt, capital: Number(e.target.value) || 100000 })} className={`${inp} w-28`} />
                  </Field>
                ) : null}
                {isEquity ? (
                  <Field label="Shares per trade">
                    <input
                      type="number"
                      min={1}
                      step={1}
                      value={bt.quantity}
                      placeholder="auto"
                      title="Leave empty to size each trade from capital and the strategy's risk settings"
                      onChange={(e) => setBt({ ...bt, quantity: e.target.value })}
                      className={`${inp} w-24`}
                    />
                  </Field>
                ) : (
                  <>
                    <Field label="Data">
                      <select value={bt.source} onChange={(e) => setBt({ ...bt, source: e.target.value })} className={inp}>
                        <option value="auto">Auto</option>
                        <option value="real">Real</option>
                        <option value="simulated">Simulated</option>
                      </select>
                    </Field>
                    <Field label="Option prices">
                      <select value={bt.pricing} onChange={(e) => setBt({ ...bt, pricing: e.target.value })} className={inp}>
                        <option value="synthetic">Synthetic</option>
                        <option value="real">Real candles</option>
                      </select>
                    </Field>
                    <Field label="Lots">
                      <input type="number" min={1} max={50} value={bt.lots} onChange={(e) => setBt({ ...bt, lots: Number(e.target.value) || 1 })} className={`${inp} w-16`} />
                    </Field>
                  </>
                )}
                <button
                  onClick={backtest}
                  disabled={!!busy || !validation?.valid}
                  className="ml-auto rounded-md bg-accent px-4 py-1.5 font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
                >
                  {busy === "backtest" ? "Running…" : "Save & backtest"}
                </button>
              </div>
              {versions.length > 1 && (
                <p className="mt-2 text-[11px] text-ink-400">
                  Versions:{" "}
                  {versions.map((v, i) => (
                    <span key={v.id}>
                      {i > 0 && " · "}
                      <button onClick={() => open(v)} className={`num hover:text-ink-100 ${current?.id === v.id ? "text-ink-100" : ""}`}>
                        v{v.version}
                      </button>
                    </span>
                  ))}
                </p>
              )}
            </section>
          </>
        )}

        {result && <Results r={result} />}
      </div>
    </main>
  );
}

const inp = "num rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-100 outline-none focus:border-accent [color-scheme:dark]";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-ink-400">{label}</span>
      {children}
    </label>
  );
}

function TranslationNotes({ t }: { t: Translation }) {
  const blocks: [string, string[], string][] = [
    ["The AI needs an answer before it can build the rules", t.questions, "border-accent/40 bg-accent/5"],
    ["Not supported", t.unsupported, "border-down/40 bg-down/10"],
    ["Still invalid after one repair attempt", t.errors, "border-down/40 bg-down/10"],
    ["Assumptions the AI made, check these", t.assumptions, "border-ink-700 bg-ink-900"],
  ];
  return (
    <div className="flex flex-col gap-2">
      {blocks
        .filter(([, items]) => items.length > 0)
        .map(([title, items, cls]) => (
          <section key={title} className={`rounded-lg border px-3 py-2 text-xs ${cls}`}>
            <h3 className="mb-1 font-semibold">{title}</h3>
            <ul className="flex flex-col gap-0.5 text-ink-100">
              {items.map((x) => (
                <li key={x}>• {x}</li>
              ))}
            </ul>
          </section>
        ))}
      <p className="text-[10px] text-ink-400">
        Translated by {t.model}
        {t.repaired ? " · corrected once after failing validation" : ""}
      </p>
    </div>
  );
}
