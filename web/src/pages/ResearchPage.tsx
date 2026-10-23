import { useEffect, useMemo, useRef, useState } from "react";
import { getJSON, postJSON, type BacktestMetrics, type SavedStrategy } from "../api";
import { EquityChart } from "../components/backtest/charts";
import { fmtPrice } from "../format";

/** Research (PRD §24.6): sweeps, walk-forward and the holdout check, run as background jobs. */

interface Tunable {
  path: string;
  label: string;
  value: number;
}

interface Job {
  id: string;
  kind: "sweep" | "walk_forward" | "holdout" | string;
  status: "queued" | "running" | "done" | "failed" | "cancelled";
  done: number;
  total: number;
  note: string;
  params: Record<string, unknown>;
  error: string | null;
  created_at: string | null;
  result?: Record<string, any> | null; // eslint-disable-line @typescript-eslint/no-explicit-any
}

interface Range {
  on: boolean;
  min: number;
  max: number;
  step: number;
}

type Mode = "sweep" | "walk_forward" | "holdout";

const iso = (d: Date) => d.toISOString().slice(0, 10);
const OBJECTIVES = [
  ["profit_factor", "Profit factor"],
  ["total_net_pnl", "Net P&L"],
  ["sharpe", "Sharpe"],
  ["expectancy", "Expectancy per trade"],
] as const;
const KIND: Record<string, string> = { sweep: "Sweep", walk_forward: "Walk-forward", holdout: "Holdout check", ai_research: "AI research" };

function suggestRange(v: number): Range {
  if (Number.isInteger(v) && Math.abs(v) >= 2) {
    const step = Math.max(1, Math.round(Math.abs(v) * 0.1));
    return { on: false, min: v - 2 * step, max: v + 2 * step, step };
  }
  const step = Math.abs(v) * 0.2 || 0.01;
  return { on: false, min: +(v - 2 * step).toFixed(4), max: +(v + 2 * step).toFixed(4), step: +step.toFixed(4) };
}

function count(r: Range) {
  return r.step > 0 && r.max >= r.min ? Math.floor((r.max - r.min) / r.step + 1e-9) + 1 : 0;
}

/** Green above break-even (profit factor 1, otherwise 0), red below; the shade grows with distance from it. */
const cellColor = (v: number | null, lo: number, hi: number, pivot: number) => {
  if (v == null) return "transparent";
  if (v >= pivot) return `rgba(34,197,94,${0.18 + 0.6 * (hi > pivot ? (v - pivot) / (hi - pivot) : 1)})`;
  return `rgba(244,63,94,${0.18 + 0.6 * (lo < pivot ? (pivot - v) / (pivot - lo) : 1)})`;
};

export function ResearchPage() {
  const [saved, setSaved] = useState<SavedStrategy[]>([]);
  const [strategyId, setStrategyId] = useState<string>(""); // "" = Momentum #1
  const [mode, setMode] = useState<Mode>("sweep");
  const [base, setBase] = useState({
    underlying: "NIFTY",
    start: iso(new Date(Date.now() - 2 * 365 * 864e5)),
    end: iso(new Date()),
    timeframe: "5m",
    pricing: "synthetic",
  });
  const [tunables, setTunables] = useState<Tunable[]>([]);
  const [ranges, setRanges] = useState<Record<string, Range>>({});
  const [opts, setOpts] = useState({ objective: "profit_factor", min_trades: 30, holdout_days: 90, train_days: 180, test_days: 60, anchored: false, embargo_days: 1 });
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const poll = useRef<number | null>(null);

  const refreshJobs = () => getJSON<Job[]>("/research/jobs").then(setJobs).catch(() => undefined);
  useEffect(() => {
    getJSON<SavedStrategy[]>("/strategies").then((s) => setSaved(s.filter((x) => (x.spec as Record<string, unknown>).market !== "equity"))).catch(() => undefined);
    refreshJobs();
  }, []);

  useEffect(() => {
    postJSON<Tunable[]>("/research/tunables", strategyId ? { strategy_id: strategyId } : {})
      .then((t) => {
        setTunables(t);
        setRanges(Object.fromEntries(t.map((x) => [x.path, suggestRange(x.value)])));
      })
      .catch((e: Error) => setError(e.message));
  }, [strategyId]);

  // Poll the open job until it finishes.
  useEffect(() => {
    if (!open || !["queued", "running"].includes(open.status)) return;
    poll.current = window.setInterval(async () => {
      try {
        const j = await getJSON<Job>(`/research/jobs/${open.id}`);
        setOpen(j);
        if (!["queued", "running"].includes(j.status)) refreshJobs();
      } catch {
        /* keep polling */
      }
    }, 1000);
    return () => {
      if (poll.current) window.clearInterval(poll.current);
    };
  }, [open?.id, open?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const chosen = tunables.filter((t) => ranges[t.path]?.on);
  const combos = chosen.reduce((n, t) => n * Math.max(1, count(ranges[t.path])), 1);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      const body: Record<string, unknown> = {
        ...base,
        ...(strategyId ? { strategy_id: strategyId } : {}),
        objective: opts.objective,
        min_trades: opts.min_trades,
        holdout_days: opts.holdout_days,
      };
      const params = chosen.map((t) => ({ path: t.path, min: ranges[t.path].min, max: ranges[t.path].max, step: ranges[t.path].step }));
      let path = "/research/sweep";
      if (mode === "sweep") body.params = params;
      if (mode === "walk_forward") {
        path = "/research/walk-forward";
        Object.assign(body, { params, train_days: opts.train_days, test_days: opts.test_days, anchored: opts.anchored, embargo_days: opts.embargo_days });
      }
      if (mode === "holdout") {
        path = "/research/holdout";
        body.assign = Object.fromEntries(chosen.map((t) => [t.path, ranges[t.path].min]));
      }
      const r = await postJSON<{ job_id: string; runs: number }>(path, body);
      const j = await getJSON<Job>(`/research/jobs/${r.job_id}`);
      setOpen(j);
      refreshJobs();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!open) return;
    await postJSON(`/research/jobs/${open.id}/cancel`, {}).catch(() => undefined);
  };

  const input = "rounded border border-ink-700 bg-ink-850 px-2 py-1 text-xs text-ink-100";
  const label = "flex flex-col gap-1 text-[11px] text-ink-400";

  return (
    <main className="mx-auto grid w-full max-w-[1600px] flex-1 grid-cols-1 gap-4 p-4 sm:p-6 lg:grid-cols-[380px_minmax(0,1fr)]">
      <aside className="flex flex-col gap-4">
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h1 className="mb-1 text-sm font-semibold">Research</h1>
          <p className="mb-3 text-[11px] leading-relaxed text-ink-400">
            Test whether a strategy's edge is real: sweep its settings and look for stable regions, then walk forward (choose on
            past days, test on the next ones). The most recent days can be held back for one final check.
          </p>
          <div role="tablist" aria-label="Research mode" className="mb-3 flex overflow-hidden rounded-md border border-ink-700">
            {(["sweep", "walk_forward", "holdout"] as Mode[]).map((m) => (
              <button key={m} role="tab" aria-selected={mode === m} onClick={() => setMode(m)}
                className={`flex-1 px-2 py-1.5 text-[11px] ${mode === m ? "bg-accent font-semibold text-ink-950" : "text-ink-300 hover:bg-ink-800"}`}>
                {KIND[m]}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <label className={`${label} col-span-2`}>
              Strategy
              <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)} className={input}>
                <option value="">Momentum #1 (built in)</option>
                {saved.map((s) => (
                  <option key={s.id} value={s.id}>{s.name} v{s.version}</option>
                ))}
              </select>
            </label>
            <label className={label}>Underlying
              <select value={base.underlying} onChange={(e) => setBase({ ...base, underlying: e.target.value })} className={input}>
                <option>NIFTY</option><option>BANKNIFTY</option>
              </select>
            </label>
            <label className={label}>Option prices
              <select value={base.pricing} onChange={(e) => setBase({ ...base, pricing: e.target.value })} className={input}>
                <option value="synthetic">Modelled</option><option value="hybrid">Real where stored</option><option value="real">Real only</option>
              </select>
            </label>
            <label className={label}>From<input type="date" value={base.start} onChange={(e) => setBase({ ...base, start: e.target.value })} className={input} /></label>
            <label className={label}>To<input type="date" value={base.end} onChange={(e) => setBase({ ...base, end: e.target.value })} className={input} /></label>
            {!strategyId && (
              <label className={label}>Timeframe
                <select value={base.timeframe} onChange={(e) => setBase({ ...base, timeframe: e.target.value })} className={input}>
                  {["1m", "3m", "5m", "15m"].map((x) => <option key={x}>{x}</option>)}
                </select>
              </label>
            )}
            <label className={label}>Hold back last (days)
              <input type="number" min={mode === "holdout" ? 5 : 0} max={730} value={opts.holdout_days}
                onChange={(e) => setOpts({ ...opts, holdout_days: Number(e.target.value) || 0 })} className={input} />
            </label>
          </div>
        </section>

        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h2 className="mb-1 text-sm font-semibold">{mode === "holdout" ? "Setting to check" : "Settings to vary"}</h2>
          <p className="mb-2 text-[11px] text-ink-400">
            {mode === "holdout"
              ? "Tick the settings you chose; each is checked at its 'from' value. A setting can be checked on the holdout only once."
              : "Tick up to four and give each a range. Fewer, coarser steps are better evidence than many fine ones."}
          </p>
          <ul className="max-h-80 space-y-1.5 overflow-y-auto pr-1">
            {tunables.map((t) => {
              const r = ranges[t.path];
              if (!r) return null;
              const set = (p: Partial<Range>) => setRanges({ ...ranges, [t.path]: { ...r, ...p } });
              return (
                <li key={t.path} className={`rounded border px-2 py-1.5 ${r.on ? "border-accent/50" : "border-ink-800"}`}>
                  <label className="flex items-start gap-2">
                    <input type="checkbox" checked={r.on} disabled={!r.on && chosen.length >= 4} onChange={(e) => set({ on: e.target.checked })} className="mt-0.5" />
                    <span className="min-w-0 flex-1 text-ink-200">{t.label} <span className="text-ink-500">(now {t.value})</span></span>
                  </label>
                  {r.on && (
                    <div className="mt-1.5 flex gap-2 pl-5">
                      {(["min", "max", "step"] as const).filter((k) => mode !== "holdout" || k === "min").map((k) => (
                        <label key={k} className="flex items-center gap-1 text-ink-400">
                          {mode === "holdout" ? "value" : k}
                          <input type="number" value={r[k]} step="any" onChange={(e) => set({ [k]: Number(e.target.value) } as Partial<Range>)}
                            className="num w-16 rounded border border-ink-700 bg-ink-850 px-1 py-0.5 text-right text-ink-100" />
                        </label>
                      ))}
                      {mode !== "holdout" && <span className="self-center whitespace-nowrap text-ink-500">×{count(r)}</span>}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          {mode !== "holdout" && (
            <div className="mt-3 grid grid-cols-2 gap-2">
              <label className={label}>Choose by
                <select value={opts.objective} onChange={(e) => setOpts({ ...opts, objective: e.target.value })} className={input}>
                  {OBJECTIVES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </label>
              <label className={label}>Min trades to count
                <input type="number" min={1} value={opts.min_trades} onChange={(e) => setOpts({ ...opts, min_trades: Number(e.target.value) || 1 })} className={input} />
              </label>
            </div>
          )}
          {mode === "walk_forward" && (
            <div className="mt-2 grid grid-cols-2 gap-2">
              <label className={label}>Train window (days)
                <input type="number" min={10} value={opts.train_days} onChange={(e) => setOpts({ ...opts, train_days: Number(e.target.value) || 10 })} className={input} />
              </label>
              <label className={label}>Test window (days)
                <input type="number" min={5} value={opts.test_days} onChange={(e) => setOpts({ ...opts, test_days: Number(e.target.value) || 5 })} className={input} />
              </label>
              <label className="col-span-2 flex items-center gap-2 text-[11px] text-ink-300">
                <input type="checkbox" checked={opts.anchored} onChange={(e) => setOpts({ ...opts, anchored: e.target.checked })} />
                Anchored (training always starts at the first day)
              </label>
            </div>
          )}
          <div className="mt-3 flex items-center justify-between gap-2">
            <span className="text-[11px] text-ink-400">
              {mode === "holdout" ? "1 backtest" : `${combos} combination${combos === 1 ? "" : "s"}${mode === "walk_forward" ? " per fold" : ""}`}
            </span>
            <button onClick={submit} disabled={busy || (mode !== "walk_forward" && mode !== "holdout" && chosen.length === 0) || combos > 500}
              className="rounded-md bg-accent px-3 py-1.5 font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50">
              {busy ? "Starting…" : mode === "sweep" ? "Run sweep" : mode === "walk_forward" ? "Run walk-forward" : "Check holdout"}
            </button>
          </div>
          {combos > 500 && <p className="mt-1 text-[11px] text-down">At most 500 combinations: use fewer values.</p>}
          {error && <p className="mt-2 text-[11px] text-down" role="alert">{error}</p>}
        </section>

        <section className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h2 className="mb-2 text-sm font-semibold">Recent jobs</h2>
          {!jobs.length && <p className="text-[11px] text-ink-400">None yet.</p>}
          <ul className="space-y-1">
            {jobs.map((j) => (
              <li key={j.id}>
                <button onClick={() => getJSON<Job>(`/research/jobs/${j.id}`).then(setOpen)}
                  className={`flex w-full items-center justify-between gap-2 rounded px-2 py-1 text-left hover:bg-ink-800 ${open?.id === j.id ? "bg-ink-800" : ""}`}>
                  <span className="text-ink-200">{KIND[j.kind] ?? j.kind}</span>
                  <span className={`text-[11px] ${j.status === "done" ? "text-up" : j.status === "failed" ? "text-down" : "text-ink-400"}`}>
                    {j.status}{j.status === "running" ? ` ${j.done}/${j.total}` : ""}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      </aside>

      <section className="min-w-0">
        {!open ? (
          <div className="grid min-h-[300px] place-items-center rounded-lg border border-dashed border-ink-700 p-8 text-center text-sm text-ink-400">
            Pick settings and run a sweep or a walk-forward. Results appear here.
          </div>
        ) : (
          <JobView job={open} onCancel={cancel} />
        )}
      </section>
    </main>
  );
}

function JobView({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const r = job.result;
  const running = job.status === "queued" || job.status === "running";
  return (
    <div className="flex flex-col gap-4">
      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">{KIND[job.kind] ?? job.kind} · {job.status}</h2>
          {running && <button onClick={onCancel} className="rounded-md border border-ink-700 px-2 py-1 text-[11px] text-ink-300 hover:bg-ink-800">Cancel</button>}
        </div>
        {running && (
          <div className="mt-2">
            <div className="h-1.5 overflow-hidden rounded bg-ink-800" role="progressbar" aria-valuenow={job.done} aria-valuemax={job.total}>
              <div className="h-full bg-accent transition-all" style={{ width: `${job.total ? (100 * job.done) / job.total : 3}%` }} />
            </div>
            <p className="mt-1 text-[11px] text-ink-400">{job.done} of {job.total} backtests {job.note ? `· ${job.note}` : ""}</p>
          </div>
        )}
        {job.error && <p className="mt-2 text-xs text-down">{job.error}</p>}
        {r?.verdict && (
          <p className={`mt-2 rounded-md border px-3 py-2 text-xs ${/^(No edge|Not enough)/.test(r.verdict) ? "border-down/40 bg-down/10 text-down" : "border-ink-700 bg-ink-850 text-ink-100"}`}>
            {r.verdict}
          </p>
        )}
        {r?.holdout && job.kind !== "holdout" && (
          <p className="mt-2 text-[11px] text-ink-400">Held back and never used here: {r.holdout.from} to {r.holdout.to}.</p>
        )}
      </div>
      {r && job.kind === "sweep" && <SweepResult r={r} />}
      {r && job.kind === "walk_forward" && <WalkForwardResult r={r} />}
      {r && job.kind === "holdout" && (
        <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h3 className="mb-2 text-sm font-semibold">Holdout {r.holdout.from} to {r.holdout.to}</h3>
          <Metrics m={r.metrics} />
          {r.equity_curve && <div className="mt-3"><EquityChart metrics={{ equity_curve: r.equity_curve } as unknown as BacktestMetrics} capital={100000} /></div>}
        </div>
      )}
    </div>
  );
}

function Metrics({ m }: { m: Record<string, number | null> }) {
  const items: [string, string][] = [
    ["Trades", String(m.trades ?? "—")],
    ["Net P&L", `₹${fmtPrice(m.total_net_pnl)}`],
    ["Profit factor", m.profit_factor == null ? "—" : String(m.profit_factor)],
    ["Win rate", m.win_rate == null ? "—" : `${m.win_rate}%`],
    ["Sharpe", m.sharpe == null ? "—" : String(m.sharpe)],
    ["Max drawdown", `₹${fmtPrice(m.max_drawdown)}`],
  ];
  return (
    <dl className="grid grid-cols-3 gap-2 sm:grid-cols-6">
      {items.map(([k, v]) => (
        <div key={k} className="rounded border border-ink-800 px-2 py-1.5">
          <dt className="text-[10px] text-ink-500">{k}</dt>
          <dd className="num text-ink-100">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

const short = (p: string) => p.replace(/^strategy_params\.|^engine_params\.|^spec\./, "");

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function SweepResult({ r }: { r: Record<string, any> }) {
  const runs: any[] = r.runs ?? []; // eslint-disable-line @typescript-eslint/no-explicit-any
  const sorted = useMemo(() => [...runs].sort((a, b) => (b.score ?? -Infinity) - (a.score ?? -Infinity)), [runs]);
  const hm = r.heatmap;
  const flat: number[] = hm ? hm.z.flat().filter((v: number | null) => v != null) : [];
  const lo = Math.min(...flat), hi = Math.max(...flat);
  const pivot = r.objective === "profit_factor" ? 1 : 0;
  return (
    <>
      {r.best && (
        <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h3 className="mb-1 text-sm font-semibold">Best setting (in-sample)</h3>
          <p className="mb-2 text-[11px] text-ink-400">
            {Object.entries(r.best.params).map(([k, v]) => `${short(k)} = ${v}`).join(" · ")}
            {r.best.fragile && <span className="ml-2 text-down">fragile: a neighbouring setting does much worse</span>}
          </p>
          <Metrics m={r.best.metrics} />
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <Stat title="Deflated Sharpe ratio" value={r.deflated_sharpe?.dsr} hint={`Probability the edge is real after ${r.trials} settings tried (≥ 0.95 to trust). Annualised Sharpe ${r.deflated_sharpe?.sharpe_annualised}.`} />
            <Bootstrap b={r.bootstrap} />
          </div>
        </div>
      )}
      {hm && (
        <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
          <h3 className="mb-1 text-sm font-semibold">Where it works: {r.objective} by {short(hm.x)} × {short(hm.y)}</h3>
          <p className="mb-2 text-[11px] text-ink-400">Green = better than break-even. A broad green region is evidence; a lone green cell is luck.{hm.note ? ` Cells show the ${hm.note}.` : ""}</p>
          <div className="overflow-x-auto">
            <table className="num border-separate border-spacing-0.5 text-[11px]">
              <thead>
                <tr><th className="px-1 text-left text-ink-500">{short(hm.y)} \ {short(hm.x)}</th>{hm.xs.map((x: number) => <th key={x} className="px-2 text-ink-400">{x}</th>)}</tr>
              </thead>
              <tbody>
                {hm.ys.map((y: number, i: number) => (
                  <tr key={y}>
                    <th className="px-1 text-left text-ink-400">{y}</th>
                    {hm.z[i].map((v: number | null, j: number) => (
                      <td key={j} className="min-w-[44px] rounded px-2 py-1 text-center text-ink-100" style={{ background: cellColor(v, lo, hi, pivot) }}>
                        {v == null ? "·" : v}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
        <h3 className="mb-2 text-sm font-semibold">All {runs.length} settings</h3>
        <div className="max-h-96 overflow-auto">
          <table className="num w-full text-[11px]">
            <thead className="sticky top-0 bg-ink-900 text-ink-400">
              <tr>
                {(r.params ?? []).map((p: { path: string }) => <th key={p.path} className="px-2 py-1 text-left font-medium">{short(p.path)}</th>)}
                <th className="px-2 py-1 text-right font-medium">Score</th><th className="px-2 py-1 text-right font-medium">Trades</th>
                <th className="px-2 py-1 text-right font-medium">Net P&L</th><th className="px-2 py-1 text-right font-medium">Worst neighbour</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((x, i) => (
                <tr key={i} className="border-t border-ink-800">
                  {(r.params ?? []).map((p: { path: string }) => <td key={p.path} className="px-2 py-1">{x.params[p.path]}</td>)}
                  <td className="px-2 py-1 text-right">{x.score ?? <span className="text-ink-500">{x.error ? "error" : "too few trades"}</span>}{x.fragile && <span className="ml-1 text-down" title="fragile">!</span>}</td>
                  <td className="px-2 py-1 text-right">{x.metrics?.trades ?? "—"}</td>
                  <td className="px-2 py-1 text-right">{x.metrics ? fmtPrice(x.metrics.total_net_pnl) : "—"}</td>
                  <td className="px-2 py-1 text-right">{x.worst_neighbour ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {r.best_equity_curve && <EquityChart metrics={{ equity_curve: r.best_equity_curve } as unknown as BacktestMetrics} capital={100000} />}
    </>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function WalkForwardResult({ r }: { r: Record<string, any> }) {
  const oos = r.out_of_sample ?? {};
  return (
    <>
      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
        <h3 className="mb-1 text-sm font-semibold">Out-of-sample (stitched test windows)</h3>
        <p className="mb-2 text-[11px] text-ink-400">
          {r.layout?.anchored ? "Anchored" : "Rolling"} {r.layout?.train_days}-day training, {r.layout?.test_days}-day tests, {r.settings_per_fold} settings per fold.
          In-sample average profit factor {r.in_sample_average?.profit_factor}; walk-forward efficiency {r.walk_forward_efficiency ?? "—"}.
        </p>
        <Metrics m={oos} />
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <Stat title="Deflated Sharpe ratio" value={r.deflated_sharpe?.dsr} hint="Out-of-sample returns, deflated by the settings tried per fold (≥ 0.95 to trust)." />
          <Bootstrap b={r.bootstrap} />
        </div>
      </div>
      {oos.equity_curve && <EquityChart metrics={oos as BacktestMetrics} capital={100000} />}
      <div className="rounded-lg border border-ink-800 bg-ink-900 p-4 text-xs">
        <h3 className="mb-2 text-sm font-semibold">Folds</h3>
        <div className="overflow-x-auto">
          <table className="num w-full text-[11px]">
            <thead className="text-ink-400">
              <tr><th className="px-2 py-1 text-left font-medium">Train</th><th className="px-2 py-1 text-left font-medium">Test</th><th className="px-2 py-1 text-left font-medium">Chosen</th>
                <th className="px-2 py-1 text-right font-medium">Train PF</th><th className="px-2 py-1 text-right font-medium">Test PF</th><th className="px-2 py-1 text-right font-medium">Test P&L</th></tr>
            </thead>
            <tbody>
              {(r.folds ?? []).map((f: any, i: number) => ( // eslint-disable-line @typescript-eslint/no-explicit-any
                <tr key={i} className="border-t border-ink-800">
                  <td className="px-2 py-1">{f.train[0]} → {f.train[1]}</td>
                  <td className="px-2 py-1">{f.test[0]} → {f.test[1]}</td>
                  <td className="px-2 py-1">{f.params ? Object.entries(f.params).map(([k, v]) => `${short(k)}=${v}`).join(", ") || "as is" : <span className="text-ink-500">{f.note}</span>}</td>
                  <td className="px-2 py-1 text-right">{f.train_metrics?.profit_factor ?? "—"}</td>
                  <td className="px-2 py-1 text-right">{f.test_metrics?.profit_factor ?? "—"}</td>
                  <td className={`px-2 py-1 text-right ${(f.test_metrics?.total_net_pnl ?? 0) >= 0 ? "text-up" : "text-down"}`}>{f.test_metrics ? fmtPrice(f.test_metrics.total_net_pnl) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}

function Stat({ title, value, hint }: { title: string; value: number | undefined; hint: string }) {
  return (
    <div className="rounded border border-ink-800 p-3">
      <p className="text-[11px] text-ink-400">{title}</p>
      <p className={`num text-lg ${value != null && value >= 0.95 ? "text-up" : "text-ink-100"}`}>{value ?? "—"}</p>
      <p className="mt-1 text-[11px] text-ink-500">{hint}</p>
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function Bootstrap({ b }: { b: any }) {
  if (!b?.samples) return null;
  return (
    <div className="rounded border border-ink-800 p-3">
      <p className="text-[11px] text-ink-400">If the same days came in another order ({b.samples} reshuffles)</p>
      <p className="num mt-1 text-xs text-ink-100">Net P&L ₹{fmtPrice(b.net_pnl.p5)} … ₹{fmtPrice(b.net_pnl.p95)} (5th–95th pct)</p>
      <p className="num text-xs text-ink-100">Max drawdown up to ₹{fmtPrice(b.max_drawdown.p95)}</p>
      <p className="num text-xs text-ink-100">Chance of a loss: {(100 * b.prob_loss).toFixed(0)}%</p>
    </div>
  );
}
