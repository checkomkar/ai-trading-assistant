import { useEffect, useMemo, useRef, useState } from "react";
import {
  ApiError,
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
import { StrategyChat } from "../components/StrategyChat";
import { StrategyLibrary } from "../components/StrategyLibrary";
import { ReadyMade } from "../components/ReadyMade";
import { CandleLoader, Spinner, toast } from "../ui/feedback";
import { Drawer, Modal } from "../ui/overlay";
import { PromptBox } from "../ui/PromptBox";
import { fmtPrice } from "../format";

const EXAMPLES = [
  "NIFTY 5 min: buy the ATM call when futures are above VWAP, EMA 9 crosses above EMA 21 and RSI(14) is above 60. Mirror it for puts. Only 9:45 to 14:30, skip if ADX(14) is below 20. Stop 20%, target 40%, trail 10% after 25% profit, max 2 trades a day.",
  "BANKNIFTY 15 min opening-range breakout: buy calls when the close breaks above the opening-range high and RSI(14) > 55; puts on a break below the opening-range low with RSI < 45. Stop 25%, target 60%, exit after 12 candles, 1 trade a day.",
];

const iso = (d: Date) => d.toISOString().slice(0, 10);
const rupee = (v: number) => `${v < 0 ? "−" : ""}₹${fmtPrice(Math.abs(v))}`;

type Spec = Record<string, unknown>;

/** Build, refine and backtest a strategy on one screen: saved strategies on the left, the strategy and its backtest
 *  settings in the middle, results on the right. Describing a new strategy, editing the raw rules and the strategy
 *  chat open over the page instead of below it. */
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
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [bt, setBt] = useState({ underlying: "NIFTY", start: iso(new Date(Date.now() - 365 * 864e5)), end: iso(new Date()), source: "auto", pricing: "hybrid", lots: 1, max_daily_loss: 5000, capital: 100000, quantity: "", calibrate: true });
  const timer = useRef<ReturnType<typeof setTimeout>>();
  // The AI asked questions instead of building: shown as a dialog with tappable answers.
  const [ask, setAsk] = useState<{ input: string; base: Spec | null; t: Translation } | null>(null);
  const [overlay, setOverlay] = useState<"describe" | "rules" | "chat" | "library" | null>(null);
  const [side, setSide] = useState<"saved" | "ready">("saved");
  // Both side panels fold to a thin rail so the results get the width; remembered in this browser.
  const [fold, setFoldState] = useState<{ lib: boolean; mid: boolean }>(() => {
    try {
      return { lib: false, mid: false, ...JSON.parse(localStorage.getItem(FOLD_KEY) ?? "{}") };
    } catch {
      return { lib: false, mid: false };
    }
  });
  const setFold = (f: { lib: boolean; mid: boolean }) => {
    setFoldState(f);
    try {
      localStorage.setItem(FOLD_KEY, JSON.stringify(f));
    } catch {
      /* storage blocked: the layout just won't be remembered */
    }
  };
  const focused = fold.lib && fold.mid;

  const refreshSaved = () => getJSON<SavedStrategy[]>("/strategies").then(setSaved).catch(() => {});
  useEffect(() => {
    getJSON<TranslatorInfo>("/strategies/translator").then(setAi).catch(() => setAi(null));
    refreshSaved();
  }, []);

  const parsed = useMemo(() => {
    try {
      return json.trim() ? (JSON.parse(json) as Spec) : null;
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
      postJSON<Validation>("/strategies/validate", { spec: parsed }, undefined, { quiet: true })
        .then(setValidation)
        .catch((e) => setValidation({ valid: false, errors: [(e as Error).message], english: null, spec_hash: null }));
    }, 350);
    return () => clearTimeout(timer.current);
  }, [parsed]);

  const setSpec = (spec: Spec) => setJson(JSON.stringify(spec, null, 2));

  /** Run an action with a busy label. API failures already show a toast (api.ts); anything else is toasted here. */
  const run = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    try {
      await fn();
    } catch (e) {
      if (!(e instanceof ApiError)) toast.error((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const translate = (input: string, base?: Spec | null) =>
    run("translate", async () => {
      const t = await postJSON<Translation>("/strategies/translate", { text: input, base_spec: base ?? null });
      setTranslation(t);
      if (!t.spec && t.questions.length > 0) {
        setOverlay(null); // close "Describe a strategy" so the questions are on top
        setAsk({ input, base: base ?? null, t });
      }
      if (t.spec) {
        setSpec(t.spec);
        setCurrent(null);
        setRefine("");
        setOverlay(null);
        toast.success(base ? "Change applied" : "Rules ready", t.assumptions.length ? `${t.assumptions.length} assumption${t.assumptions.length === 1 ? "" : "s"} to check` : "Review them, then backtest");
      } else if (!t.questions.length) {
        toast.error("The AI could not build rules from that", t.unsupported[0] ?? t.errors[0]);
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
      const t = await getJSON<{ spec: Spec }>("/strategies/template");
      setSpec(t.spec);
      setTranslation(null);
      setCurrent(null);
      setOverlay(null);
      toast.info("Momentum #1 loaded");
    });

  const open = (s: SavedStrategy) => {
    setSpec(s.spec);
    setCurrent(s);
    setTranslation(null);
    setText(s.source_text ?? "");
    setResult(null);
    if (overlay === "library") setOverlay(null);
    getJSON<SavedStrategy[]>(`/strategies/${s.id}/versions`).then(setVersions).catch(() => setVersions([]));
    const unds = (s.spec.underlyings as string[] | undefined) ?? ["NIFTY"];
    setBt((b) => ({ ...b, underlying: unds.includes(b.underlying) ? b.underlying : unds[0] }));
  };

  const pickReady = (name: string, spec: Spec) => {
    setSpec(spec);
    setTranslation(null);
    setCurrent(null);
    setText("");
    setResult(null);
    if (overlay === "library") setOverlay(null);
    toast.info(`${name} loaded`, "A starting point: backtest it before trusting it");
  };

  const saveVersion = async () => {
    const s = await postJSON<SavedStrategy>("/strategies", {
      spec: parsed,
      source_text: text || null,
      assumptions: translation?.assumptions ?? current?.assumptions ?? [],
      translator_model: translation?.model ?? null,
    });
    open(s);
    refreshSaved();
    return s;
  };

  const save = () =>
    run("save", async () => {
      const s = await saveVersion();
      toast.success(`Saved ${s.name} v${s.version}`);
    });

  const backtest = (force = false) =>
    run("backtest", async () => {
      // Always backtest a saved version, so every run is traceable to an immutable spec (saving an unchanged spec
      // returns the existing version).
      const s = await saveVersion();
      const r = await postJSON<BacktestResult>("/backtests", {
        underlying: bt.underlying,
        start: bt.start,
        end: bt.end,
        timeframe: (s.spec.timeframe as string) ?? "5m",
        source: bt.source,
        pricing: bt.pricing,
        strategy_id: s.id,
        calibrate: bt.calibrate,
        force,
        engine_params: isEquity ? { capital: bt.capital } : { lots: bt.lots, max_daily_loss: bt.max_daily_loss },
        ...(isEquity && Number(bt.quantity) >= 1 ? { quantity: Math.floor(Number(bt.quantity)) } : {}),
      });
      setResult(r);
      if (r.cached) toast.info("Loaded the stored result", "Same inputs and unchanged data, so it was not run again");
      else toast.success(`Backtest done · ${r.metrics.trades} trades`, `Net ${rupee(r.metrics.total_net_pnl ?? 0)} over ${r.days} days`);
    });

  const spec = parsed as Spec | null | undefined;
  const isEquity = spec?.market === "equity";
  const dirty = current && validation?.spec_hash && validation.spec_hash !== current.spec_hash;
  const underlyings = (spec?.underlyings as string[] | undefined) ?? ["NIFTY", "BANKNIFTY"];
  const maxTrades = ((spec?.risk as Spec | undefined)?.max_trades_per_day as number | undefined) ?? 3;
  const setMaxTrades = (n: number) => spec && setSpec({ ...spec, risk: { ...((spec.risk as Spec | undefined) ?? {}), max_trades_per_day: n } });
  const canRun = !busy && !!validation?.valid;

  const library = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div role="tablist" aria-label="Strategy library" className="m-2 flex gap-1 rounded-lg bg-ink-850 p-1 text-xs">
        {(["saved", "ready"] as const).map((k) => (
          <button key={k} role="tab" aria-selected={side === k} onClick={() => setSide(k)}
            className={`flex-1 rounded-md px-2 py-1 font-semibold ${side === k ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}>
            {k === "saved" ? `Saved${saved.length ? ` (${new Set(saved.map((s) => s.name)).size})` : ""}` : "Ready-made"}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-2 [&>section]:border-0 [&>section]:bg-transparent">
        {side === "saved" ? <StrategyLibrary saved={saved} current={current} onOpen={open} onChanged={refreshSaved} /> : <ReadyMade onPick={pickReady} />}
      </div>
    </div>
  );

  return (
    <main
      style={{ "--lib": fold.lib ? "44px" : "260px", "--mid": fold.mid ? "44px" : "400px" } as React.CSSProperties}
      className="mx-auto grid w-full max-w-[1920px] min-h-0 flex-1 grid-cols-1 gap-3 overflow-y-auto p-3 transition-[grid-template-columns] duration-300 ease-out lg:grid-cols-[var(--mid)_minmax(0,1fr)] lg:overflow-hidden xl:grid-cols-[var(--lib)_var(--mid)_minmax(0,1fr)]"
    >
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

      {/* ---- library (its own scroll; a drawer below xl) ---- */}
      <aside className="hidden min-h-0 flex-col overflow-hidden rounded-xl border border-ink-800 bg-ink-900 xl:flex" aria-label="Strategies">
        {fold.lib ? (
          <Rail label="Strategies" onOpen={() => setFold({ ...fold, lib: false })} openTitle="Show the strategy library" />
        ) : (
          <>
            <div className="flex justify-end px-2 pt-2">
              <FoldButton onClick={() => setFold({ ...fold, lib: true })} title="Hide the library" />
            </div>
            {library}
          </>
        )}
      </aside>

      {/* ---- the strategy and how to test it ---- */}
      <section className="flex min-h-0 flex-col overflow-hidden rounded-xl border border-ink-800 bg-ink-900" aria-label="Strategy">
        {fold.mid && (
          <Rail
            className="hidden lg:flex"
            label={current ? `${current.name} v${current.version}` : String(spec?.name ?? "Strategy")}
            onOpen={() => setFold({ ...fold, mid: false })}
            openTitle="Show the strategy and its settings"
          >
            <button onClick={() => backtest()} disabled={!canRun} title="Save & backtest" aria-label="Save & backtest"
              className="grid h-8 w-8 place-items-center rounded-md bg-accent text-ink-950 hover:brightness-110 disabled:opacity-40">
              {busy === "backtest" ? <Spinner size={13} /> : <Icon d="M5 3l14 9-14 9z" size={12} />}
            </button>
            <button onClick={() => setOverlay("rules")} disabled={json === ""} title="Rules (JSON)" aria-label="Rules" className={railBtn}>
              <Icon d="M8 6l-6 6 6 6M16 6l6 6-6 6" />
            </button>
            <button onClick={() => setOverlay("chat")} disabled={!spec || !validation?.valid} title="Ask about this strategy" aria-label="Ask" className={railBtn}>
              <Icon d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z" />
            </button>
          </Rail>
        )}
        <div className={`flex min-h-0 flex-1 flex-col ${fold.mid ? "lg:hidden" : ""}`}>
        <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2.5">
          <button onClick={() => setOverlay("library")} className={`${btn} xl:hidden`} title="Saved and ready-made strategies">
            <Icon d="M4 6h16M4 12h16M4 18h10" /> Library
          </button>
          <button onClick={() => setOverlay("describe")} className={`${btn} border-accent/60 text-accent hover:bg-accent/10`}>
            <Icon d="M12 5v14M5 12h14" /> New
          </button>
          <div className="ml-auto flex items-center gap-1.5">
            <button onClick={() => setOverlay("rules")} disabled={json === ""} className={btn} title="View and edit the rules as JSON">
              <Icon d="M8 6l-6 6 6 6M16 6l6 6-6 6" /> Rules
            </button>
            <button onClick={() => setOverlay("chat")} disabled={!spec || !validation?.valid} className={btn} title="Ask about this strategy and its results">
              <Icon d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.4A8 8 0 1 1 21 12z" /> Ask
            </button>
            <span className="hidden lg:inline-flex">
              <FoldButton onClick={() => setFold({ ...fold, mid: true })} title="Hide this panel" />
            </span>
          </div>
        </div>

        {json === "" ? (
          <div className="grid flex-1 place-items-center p-6 text-center">
            <div className="max-w-xs">
              <div className="mx-auto mb-3 grid h-12 w-12 place-items-center rounded-full bg-accent/10 text-accent">
                <Icon d="M13 2L3 14h9l-1 8 10-12h-9z" size={20} />
              </div>
              <p className="text-sm font-medium text-ink-100">No strategy loaded</p>
              <p className="mt-1 text-xs text-ink-400">Describe one in plain English, or pick a saved or ready-made strategy.</p>
              <div className="mt-4 flex justify-center gap-2">
                <button onClick={() => setOverlay("describe")} className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110">
                  Describe a strategy
                </button>
                <button onClick={() => { setSide("ready"); setOverlay("library"); }} className={`${btn} xl:hidden`}>Ready-made</button>
              </div>
            </div>
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="flex items-center gap-2 px-3 pt-3">
              <h1 className="min-w-0 truncate text-sm font-semibold" title={String(spec?.name ?? "")}>
                {current ? current.name : String(spec?.name ?? "Unsaved strategy")}
              </h1>
              {current && (
                <span className="num shrink-0 rounded bg-ink-800 px-1.5 py-0.5 text-[10px] text-ink-300">
                  v{current.version}
                  {dirty ? " · edited" : ""}
                </span>
              )}
              <span className={`ml-auto shrink-0 text-[11px] ${validation?.valid ? "text-up" : "text-down"}`}>
                {validation == null ? "" : validation.valid ? "✓ valid" : `✗ ${validation.errors.length} problem${validation.errors.length === 1 ? "" : "s"}`}
              </span>
            </div>
            {versions.length > 1 && (
              <div className="flex flex-wrap gap-1 px-3 pt-1.5 text-[11px] text-ink-400">
                Versions:
                {versions.map((v) => (
                  <button key={v.id} onClick={() => open(v)} className={`num rounded px-1 hover:text-ink-100 ${current?.id === v.id ? "bg-ink-800 text-ink-100" : ""}`}>
                    v{v.version}
                  </button>
                ))}
              </div>
            )}

            {translation && <TranslationNotes t={translation} />}

            <div className="mx-3 mt-3 rounded-lg border border-ink-800 bg-ink-950/40">
              <div className="flex items-center justify-between border-b border-ink-800 px-3 py-1.5">
                <span className="text-[11px] font-medium text-ink-300">What will run</span>
                <span className="text-[10px] text-ink-500">written by code from the rules</span>
              </div>
              {validation?.valid && validation.english ? (
                <pre className="max-h-64 overflow-auto whitespace-pre-wrap px-3 py-2.5 font-sans text-xs leading-relaxed text-ink-100">{validation.english}</pre>
              ) : (
                <ul className="flex flex-col gap-1 px-3 py-2.5 text-xs text-down">
                  {(validation?.errors ?? []).map((e) => (
                    <li key={e}>• {e}</li>
                  ))}
                  {validation == null && <li className="text-ink-400">Checking…</li>}
                </ul>
              )}
            </div>

            <div className="px-3 pt-3">
              <p className="mb-1 text-[11px] font-medium text-ink-300">Refine in plain English</p>
              <PromptBox
                value={refine}
                onChange={setRefine}
                onSubmit={() => refine.trim().length >= 5 && parsed && translate(refine, parsed)}
                label="Refine in plain English"
                placeholder="e.g. make the stop 15% and only trade after 10:00"
                disabled={busy === "translate"}
                maxRows={8}
                actions={
                  <button
                    onClick={() => translate(refine, parsed ?? null)}
                    disabled={!!busy || refine.trim().length < 5 || !parsed}
                    className="flex items-center gap-1.5 rounded-md border border-ink-700 px-2.5 py-1 text-xs text-ink-100 hover:border-accent disabled:opacity-50"
                  >
                    {busy === "translate" && <Spinner size={12} />}
                    {busy === "translate" ? "Updating…" : "Apply change"}
                  </button>
                }
              />
            </div>

            <div className="mt-3 border-t border-ink-800 px-3 pt-3 pb-3">
              <p className="mb-2 text-[11px] font-medium text-ink-300">Backtest settings</p>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {!isEquity && (
                  <Field label="Underlying">
                    <select value={bt.underlying} onChange={(e) => setBt({ ...bt, underlying: e.target.value })} className={inp}>
                      {underlyings.map((u) => (
                        <option key={u}>{u}</option>
                      ))}
                    </select>
                  </Field>
                )}
                {!isEquity && (
                  <Field label="Lots">
                    <input type="number" min={1} max={50} value={bt.lots} onChange={(e) => setBt({ ...bt, lots: Number(e.target.value) || 1 })} className={inp} />
                  </Field>
                )}
                <Field label="From">
                  <input type="date" value={bt.start} onChange={(e) => setBt({ ...bt, start: e.target.value })} className={inp} />
                </Field>
                <Field label="To">
                  <input type="date" value={bt.end} onChange={(e) => setBt({ ...bt, end: e.target.value })} className={inp} />
                </Field>
                {isEquity ? (
                  <>
                    <Field label="Capital ₹">
                      <input type="number" min={10000} step={10000} value={bt.capital} onChange={(e) => setBt({ ...bt, capital: Number(e.target.value) || 100000 })} className={inp} />
                    </Field>
                    <Field label="Shares per trade">
                      <input
                        type="number"
                        min={1}
                        step={1}
                        value={bt.quantity}
                        placeholder="auto"
                        title="Leave empty to size each trade from capital and the strategy's risk settings"
                        onChange={(e) => setBt({ ...bt, quantity: e.target.value })}
                        className={inp}
                      />
                    </Field>
                  </>
                ) : (
                  <>
                    <Field label="Data">
                      <select value={bt.source} onChange={(e) => setBt({ ...bt, source: e.target.value })} className={inp}>
                        <option value="auto">Auto</option>
                        <option value="real">Real</option>
                        <option value="simulated">Simulated</option>
                      </select>
                    </Field>
                    <Field label="Trades per day" hint="0 = no limit">
                      <input
                        type="number"
                        min={0}
                        max={400}
                        value={maxTrades}
                        onChange={(e) => setMaxTrades(Math.max(0, Math.min(400, Math.floor(Number(e.target.value) || 0))))}
                        title="The most entries the strategy may take in a day (0 = every signal, one position at a time). Saved with the strategy."
                        className={inp}
                      />
                    </Field>
                    <Field label="Option prices" wide>
                      <select value={bt.pricing} onChange={(e) => setBt({ ...bt, pricing: e.target.value })} className={inp}>
                        <option value="synthetic">Modelled (Black-Scholes)</option>
                        <option value="hybrid">Real where stored, else modelled</option>
                        <option value="real">Real candles only (skips the rest)</option>
                      </select>
                    </Field>
                    <label className={`col-span-2 flex items-center gap-2.5 rounded-md border border-ink-800 px-2.5 py-2 ${bt.pricing === "real" ? "opacity-50" : "cursor-pointer hover:border-ink-700"}`}>
                      <Switch on={bt.calibrate && bt.pricing !== "real"} disabled={bt.pricing === "real"} onChange={(v) => setBt({ ...bt, calibrate: v })} />
                      <span className="min-w-0">
                        <span className="block text-ink-100">Calibrate modelled prices</span>
                        <span className="block text-[10px] text-ink-400">
                          {bt.pricing === "real" ? "Not used: real candles only" : "Use the IV fit from stored real options, when one exists"}
                        </span>
                      </span>
                    </label>
                  </>
                )}
              </div>
            </div>
          </div>
        )}

        {json !== "" && (
          <div className="flex gap-2 border-t border-ink-800 bg-ink-900 px-3 py-2.5">
            <button onClick={save} disabled={!canRun} className="flex items-center justify-center gap-1.5 rounded-md border border-ink-700 px-3 py-2 text-xs text-ink-100 hover:border-accent disabled:opacity-50">
              {busy === "save" && <Spinner size={12} />}
              Save version
            </button>
            <button
              onClick={() => backtest()}
              disabled={!canRun}
              className="flex flex-1 items-center justify-center gap-2 rounded-md bg-accent px-4 py-2 text-xs font-semibold whitespace-nowrap text-ink-950 hover:brightness-110 disabled:opacity-50"
            >
              {busy === "backtest" ? <Spinner size={13} /> : <Icon d="M5 3l14 9-14 9z" size={12} />}
              {busy === "backtest" ? "Running backtest…" : "Save & backtest"}
            </button>
          </div>
        )}
        </div>
      </section>

      {/* ---- results (tabbed, its own scroll) ---- */}
      <section className="relative flex min-h-[520px] flex-col overflow-hidden rounded-xl border border-ink-800 bg-ink-900 lg:min-h-0" aria-label="Backtest results">
        {result ? (
          <Results
            r={result}
            onRerun={() => backtest(true)}
            actions={
              <button
                onClick={() => setFold(focused ? { lib: false, mid: false } : { lib: true, mid: true })}
                title={focused ? "Show the side panels again" : "Hide the side panels to give the results the full width"}
                className="hidden items-center gap-1.5 rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-300 hover:bg-ink-800 hover:text-ink-100 lg:flex"
              >
                <Icon d={focused ? "M4 14h6v6M20 10h-6V4M14 10l7-7M3 21l7-7" : "M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"} size={12} />
                {focused ? "Show panels" : "Focus results"}
              </button>
            }
          />
        ) : busy === "backtest" ? (
          <div className="grid flex-1 place-items-center">
            <CandleLoader title="Running backtest…" detail={`${bt.underlying} · ${bt.start} → ${bt.end}. Long ranges on 1m data take a little while.`} />
          </div>
        ) : (
          <div className="grid flex-1 place-items-center p-8 text-center">
            <div className="max-w-sm">
              <svg viewBox="0 0 120 48" className="mx-auto mb-4 h-12 w-32 text-ink-700" aria-hidden>
                <polyline points="0,40 18,32 30,36 46,20 60,26 76,10 92,18 120,4" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinejoin="round" />
              </svg>
              <p className="text-sm font-medium text-ink-100">Results appear here</p>
              <p className="mt-1 text-xs text-ink-400">
                Load a strategy and press <b className="text-ink-200">Save &amp; backtest</b>. Running the same settings on unchanged data
                loads the stored result instantly.
              </p>
            </div>
          </div>
        )}
        {result && busy === "backtest" && (
          <div className="absolute inset-0 z-10 grid place-items-center bg-ink-950/70 backdrop-blur-[2px]">
            <CandleLoader title="Running backtest…" detail="The previous result stays until the new one is ready." />
          </div>
        )}
      </section>

      {/* ---- overlays ---- */}
      <Drawer open={overlay === "library"} onClose={() => setOverlay(null)} title="Strategies" subtitle="Your saved strategies and ready-made starting points" width="max-w-sm">
        {library}
      </Drawer>

      <Modal
        open={overlay === "describe"}
        onClose={() => setOverlay(null)}
        title="Describe a strategy"
        subtitle="The AI turns your words into rules. It never runs or trades anything: you review the rules, then the engine backtests them."
        footer={
          <>
            <input ref={fileRef} type="file" accept=".pdf,.docx,.xlsx,.csv,.txt,.md,.json,.png,.jpg,.jpeg,.webp" className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                await run("attach", async () => {
                  const r = (await uploadFile(f, false)) as unknown as { text: string; status: string };
                  if (!r.text.trim()) throw new Error(r.status === "no_text" ? "No text found in that file (is it a scan?)." : "Nothing readable in that file.");
                  setText((cur) => (cur.trim() ? `${cur.trim()}\n\n${r.text.trim()}` : r.text.trim()));
                  toast.success(`Read ${f.name}`);
                });
                e.target.value = "";
              }} />
            <button onClick={() => fileRef.current?.click()} disabled={!!busy} title="Attach a PDF, Word, Excel, text or image file describing the strategy" className={btn}>
              {busy === "attach" ? <Spinner size={12} /> : <Icon d="M21 12l-8.5 8.5a5 5 0 0 1-7-7L14 5a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 8" />}
              Attach file
            </button>
            <button onClick={loadTemplate} disabled={!!busy} className={btn}>Start from Momentum #1</button>
            <button
              onClick={() => translate(text)}
              disabled={!!busy || text.trim().length < 5}
              className="ml-auto flex items-center gap-1.5 rounded-md bg-accent px-4 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
            >
              {busy === "translate" && <Spinner size={12} />}
              {busy === "translate" ? "Translating…" : "Translate with AI"}
            </button>
          </>
        }
      >
        <div className="flex flex-col gap-3 p-4 text-xs">
          <PromptBox
            value={text}
            onChange={setText}
            label="Strategy description"
            placeholder="e.g. Buy NIFTY calls when the 9 EMA crosses above the 21 EMA and RSI is above 60…"
            minRows={6}
            maxRows={16}
            disabled={busy === "translate"}
          />
          {ai && (
            <p className={`num text-[10px] ${ai.configured ? "text-ink-400" : "text-down"}`}>
              {ai.provider
                ? `AI: ${ai.provider} · ${ai.model}${ai.fallbacks?.length ? ` · then ${ai.fallbacks.join(", ")}` : ""}${ai.note ? ` — ${ai.note}` : ""}`
                : ai.configured ? "" : "AI translation is not available right now."}
            </p>
          )}
          <div>
            <p className="mb-1.5 text-[11px] text-ink-400">Examples</p>
            <ul className="flex flex-col gap-1.5">
              {EXAMPLES.map((ex) => (
                <li key={ex}>
                  <button onClick={() => setText(ex)} className="w-full rounded-md border border-ink-800 px-3 py-2 text-left leading-relaxed text-ink-300 hover:border-ink-700 hover:text-ink-100">
                    “{ex}”
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </Modal>

      <Modal
        open={overlay === "rules"}
        onClose={() => setOverlay(null)}
        wide
        title="Rules (JSON)"
        subtitle="Edit directly; checked as you type. The summary on the right is generated by code, so it is exactly what the engine runs."
        footer={
          <>
            <span className={`text-[11px] ${validation?.valid ? "text-up" : "text-down"}`}>
              {validation == null ? "" : validation.valid ? "✓ valid" : `✗ ${validation.errors.length} problem${validation.errors.length === 1 ? "" : "s"}`}
            </span>
            <button onClick={() => setOverlay(null)} className="ml-auto rounded-md bg-accent px-4 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110">
              Done
            </button>
          </>
        }
      >
        <div className="grid min-h-[60dvh] grid-cols-1 lg:grid-cols-2">
          <textarea
            value={json}
            onChange={(e) => setJson(e.target.value)}
            spellCheck={false}
            aria-label="Strategy spec JSON"
            className="num min-h-[50dvh] resize-none border-ink-800 bg-ink-950/60 p-3 text-[11px] leading-relaxed text-ink-100 outline-none lg:border-r"
          />
          <div className="overflow-y-auto p-3">
            {validation?.valid && validation.english ? (
              <pre className="whitespace-pre-wrap font-sans text-xs leading-relaxed text-ink-100">{validation.english}</pre>
            ) : (
              <ul className="flex flex-col gap-1 text-xs text-down">
                {(validation?.errors ?? []).map((e) => (
                  <li key={e}>• {e}</li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Modal>

      <Drawer open={overlay === "chat"} onClose={() => setOverlay(null)} title="Talk to this strategy" subtitle={spec?.name ? String(spec.name) : undefined}>
        {spec && validation?.valid && (
          <StrategyChat key={String(spec.name ?? "draft")} spec={spec} backtestId={result?.id ?? null} onLoadSpec={(s) => { setSpec(s); setOverlay(null); toast.success("Suggested version loaded", "Review it, then backtest"); }} />
        )}
      </Drawer>
    </main>
  );
}

const FOLD_KEY = "strategies-fold";
const railBtn = "grid h-8 w-8 place-items-center rounded-md border border-ink-700 text-ink-300 hover:bg-ink-800 hover:text-ink-100 disabled:opacity-40";

function FoldButton({ onClick, title }: { onClick: () => void; title: string }) {
  return (
    <button onClick={onClick} title={title} aria-label={title}
      className="grid h-7 w-7 place-items-center rounded-md text-ink-400 hover:bg-ink-800 hover:text-ink-100">
      <Icon d="M15 18l-6-6 6-6" />
    </button>
  );
}

/** A folded panel: an expand button, its name written vertically, and optional quick actions. */
function Rail({ label, onOpen, openTitle, children, className = "flex" }: { label: string; onOpen: () => void; openTitle: string; children?: React.ReactNode; className?: string }) {
  return (
    <div className={`${className} fade-in h-full min-h-0 flex-col items-center gap-2 py-2`}>
      <button onClick={onOpen} title={openTitle} aria-label={openTitle} className={railBtn}>
        <Icon d="M9 18l6-6-6-6" />
      </button>
      {children}
      <button onClick={onOpen} className="mt-1 min-h-0 flex-1 overflow-hidden text-[11px] font-medium tracking-wide text-ink-400 hover:text-ink-100 [writing-mode:vertical-rl]" title={openTitle}>
        {label}
      </button>
    </div>
  );
}

const inp = "num w-full rounded-md border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-100 outline-none focus:border-accent [color-scheme:dark]";
const btn = "flex items-center gap-1.5 rounded-md border border-ink-700 px-2.5 py-1.5 text-xs text-ink-200 hover:bg-ink-800 hover:text-ink-100 disabled:opacity-40";

function Icon({ d, size = 13 }: { d: string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function Field({ label, hint, wide, children }: { label: string; hint?: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <label className={`flex min-w-0 flex-col gap-1 ${wide ? "col-span-2" : ""}`}>
      <span className="flex items-baseline justify-between gap-1 text-[11px] text-ink-400">
        {label}
        {hint && <span className="text-[10px] text-ink-500">{hint}</span>}
      </span>
      {children}
    </label>
  );
}

function Switch({ on, onChange, disabled }: { on: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-5 w-9 shrink-0 rounded-full transition-colors ${on ? "bg-accent" : "bg-ink-700"}`}
    >
      <span className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-ink-950 transition-transform ${on ? "translate-x-4" : ""}`} />
    </button>
  );
}

function TranslationNotes({ t }: { t: Translation }) {
  const blocks: [string, string[], string][] = [
    ["The AI needs an answer before it can build the rules", t.questions, "border-accent/40 bg-accent/5"],
    ["Not supported", t.unsupported, "border-down/40 bg-down/10"],
    ["Still invalid after one repair attempt", t.errors, "border-down/40 bg-down/10"],
    ["Assumptions the AI made, check these", t.assumptions, "border-ink-700 bg-ink-850"],
  ];
  const shown = blocks.filter(([, items]) => items.length > 0);
  if (!shown.length) return null;
  return (
    <div className="mx-3 mt-3 flex flex-col gap-2">
      {shown.map(([title, items, cls]) => (
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
