import { useEffect, useState } from "react";
import { getJSON, postJSON, type BacktestResult, type BacktestRunSummary } from "../api";
import { BacktestForm, DEFAULT_FORM, toRequest, type FormState } from "../components/backtest/BacktestForm";
import { Results } from "../components/backtest/Results";
import { fmtPrice, trendClass } from "../format";

const FORM_KEY = "bt.form.v1";

function loadForm(): FormState {
  try {
    const raw = localStorage.getItem(FORM_KEY);
    return raw ? { ...DEFAULT_FORM, ...JSON.parse(raw) } : DEFAULT_FORM;
  } catch {
    return DEFAULT_FORM;
  }
}

export function BacktestPage() {
  const [form, setForm] = useState<FormState>(loadForm);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [runs, setRuns] = useState<BacktestRunSummary[]>([]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshRuns = () => getJSON<BacktestRunSummary[]>("/backtests?limit=15").then(setRuns).catch(() => {});
  useEffect(() => {
    refreshRuns();
  }, []);
  useEffect(() => {
    try {
      localStorage.setItem(FORM_KEY, JSON.stringify(form));
    } catch {
      /* storage unavailable */
    }
  }, [form]);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      const r = await postJSON<BacktestResult>("/backtests", toRequest(form));
      setResult(r);
      refreshRuns();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const open = async (id: string) => {
    setError(null);
    try {
      setResult(await getJSON<BacktestResult>(`/backtests/${id}`));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <main className="mx-auto grid w-full max-w-[1600px] flex-1 grid-cols-1 gap-4 p-4 sm:p-6 lg:grid-cols-[320px_minmax(0,1fr)]">
      <aside className="flex flex-col gap-4">
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4">
          <h1 className="mb-1 text-sm font-semibold">Backtest</h1>
          <p className="mb-4 text-[11px] leading-relaxed text-ink-400">
            Momentum strategy #1 on historical candles. Signals on bar close, fills at the next bar's open, costs per the
            current NSE / Fyers schedule.
          </p>
          <BacktestForm form={form} onChange={setForm} onRun={run} running={running} />
        </section>
        {runs.length > 0 && (
          <section className="rounded-lg border border-ink-800 bg-ink-900 text-xs">
            <h2 className="border-b border-ink-800 px-3 py-2 text-sm font-semibold">Recent runs</h2>
            <ul>
              {runs.map((x) => (
                <li key={x.id}>
                  <button
                    onClick={() => open(x.id)}
                    className={`flex w-full items-center gap-2 border-b border-ink-800/60 px-3 py-2 text-left hover:bg-ink-850 ${
                      result?.id === x.id ? "bg-ink-850" : ""
                    }`}
                  >
                    <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${x.data_source === "real" ? "bg-up" : "bg-sim"}`} title={x.data_source} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate">
                        {x.underlying} {x.start} → {x.end}
                      </span>
                      <span className="num text-[10px] text-ink-400">
                        {x.trades} trades · {x.data_source} · {x.pricing_mode}
                      </span>
                    </span>
                    <span className={`num ${trendClass(x.total_net_pnl)}`}>
                      {x.total_net_pnl == null ? "—" : `${x.total_net_pnl < 0 ? "−" : ""}₹${fmtPrice(Math.abs(x.total_net_pnl))}`}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </aside>

      <div className="min-w-0">
        {error && (
          <div className="mb-4 rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down" role="alert">
            {error}
          </div>
        )}
        {result ? (
          <Results r={result} />
        ) : (
          <div className="grid h-full min-h-[300px] place-items-center rounded-lg border border-dashed border-ink-700 p-8 text-center text-sm text-ink-400">
            <div>
              <p className="mb-1 text-ink-300">No backtest selected</p>
              <p className="text-xs">Set the parameters and run, or open a recent run.</p>
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
