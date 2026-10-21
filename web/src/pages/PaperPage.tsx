import { useCallback, useEffect, useState } from "react";
import {
  getJSON,
  postJSON,
  type PaperDecision,
  type PaperEvent,
  type PaperPosition,
  type PaperStatus,
  type PaperSummary,
  type PaperTrade,
  type RiskStatus,
} from "../api";
import { fmtPrice, istTime, trendClass } from "../format";
import { PaperChart } from "../components/PaperChart";

const rupee = (v: number | null | undefined) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}₹${fmtPrice(Math.abs(v))}`;

const OUTCOME: Record<string, { label: string; cls: string }> = {
  pending: { label: "Checking risk", cls: "text-ink-300" },
  ordered: { label: "Order sent", cls: "text-accent" },
  filled: { label: "Filled", cls: "text-up" },
  skipped: { label: "Skipped", cls: "text-ink-300" },
  risk_rejected: { label: "Blocked by risk", cls: "text-down" },
  order_rejected: { label: "Order rejected", cls: "text-down" },
};

const SOURCE_NOTE: Record<string, string> = {
  replay: "REPLAY · synthetic option quotes",
  simulated: "SIMULATED data",
  fyers: "LIVE DATA · SHADOW (no real orders)",
};

export function PaperPage() {
  const [status, setStatus] = useState<PaperStatus | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [events, setEvents] = useState<PaperEvent[]>([]);
  const [chartDay, setChartDay] = useState("");
  const [chartTrades, setChartTrades] = useState<PaperTrade[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [s, p, e] = await Promise.all([
        getJSON<PaperStatus>("/paper/status"),
        getJSON<{ positions: PaperPosition[] }>("/paper/positions"),
        getJSON<{ events: PaperEvent[] }>("/paper/events?limit=25"),
      ]);
      setStatus(s);
      setPositions(p.positions);
      setEvents(e.events);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  // The chart's trades follow its own day picker (blank = today), refreshed whenever a trade closes.
  const realized = status?.realized_pnl ?? 0;
  useEffect(() => {
    const q = chartDay ? `?day=${chartDay}` : "";
    getJSON<{ trades: PaperTrade[] }>(`/paper/journal${q}`)
      .then((r) => setChartTrades(r.trades))
      .catch(() => setChartTrades([]));
  }, [chartDay, realized]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 2000);
    return () => clearInterval(t);
  }, [refresh]);

  const act = async (path: string, body: unknown = {}, confirmMsg?: string) => {
    if (confirmMsg && !window.confirm(confirmMsg)) return;
    setBusy(true);
    try {
      await postJSON(path, body);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      refresh();
    }
  };

  const killed = status?.risk.kill_switch.active ?? false;
  const totalPnl = status ? status.realized_pnl + status.unrealized_pnl : 0;

  return (
    <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-4 p-4 sm:p-6">
      {error && (
        <div className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down" role="alert">
          {error}
        </div>
      )}
      {status?.last_error && (
        <div className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down" role="alert">
          Runner error: {status.last_error} ({status.errors} total)
        </div>
      )}

      <section className="flex flex-wrap items-center gap-3 rounded-lg border border-ink-800 bg-ink-900 px-4 py-3">
        <div>
          <h1 className="text-sm font-semibold">Paper trading</h1>
          <p className="text-[11px] text-ink-400">
            Virtual money. Orders come only from strategies, through the risk engine.
            On live data, each entry also logs the exact order a broker would receive (Events: shadow.order). It is never sent.
          </p>
        </div>
        {status && (
          <>
            <span
              className={`rounded px-2 py-0.5 text-[11px] font-semibold tracking-widest ring-1 ${
                status.running ? "bg-up/15 text-up ring-up/30" : "bg-ink-700/60 text-ink-300 ring-ink-600"
              }`}
            >
              {status.running ? "RUNNING" : "STOPPED"}
            </span>
            <span className="rounded px-2 py-0.5 text-[11px] font-semibold tracking-widest text-sim ring-1 ring-sim/40 bg-sim/10">
              {SOURCE_NOTE[status.data_source] ?? status.data_source.toUpperCase()}
            </span>
            <span className="num text-[11px] text-ink-400">market time {istTime(status.now)} IST</span>
          </>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {status && (
            <div className="num mr-2 text-right text-xs">
              <div className={`text-base font-semibold ${trendClass(totalPnl)}`}>{rupee(totalPnl)}</div>
              <div className="text-[10px] text-ink-400">
                realised {rupee(status.realized_pnl)} · open {rupee(status.unrealized_pnl)} · cash {rupee(status.cash)}
              </div>
            </div>
          )}
          {status?.running ? (
            <button
              disabled={busy}
              onClick={() => act("/paper/stop")}
              className="rounded-md border border-ink-600 px-3 py-1.5 text-xs font-medium hover:bg-ink-800 disabled:opacity-50"
            >
              Stop runner
            </button>
          ) : (
            <StartControl disabled={busy || !status} onStart={(body) => act("/paper/start", body)} />
          )}
          {killed ? (
            <button
              disabled={busy}
              onClick={() => act("/paper/kill/reset", {}, "Re-enable new entries?")}
              className="rounded-md border border-down px-3 py-1.5 text-xs font-semibold text-down hover:bg-down/10"
            >
              Reset kill switch
            </button>
          ) : (
            <button
              disabled={busy}
              onClick={() =>
                act("/paper/kill", { reason: "manual STOP from dashboard", flatten: true },
                  "EMERGENCY STOP: block all new entries and close open paper positions at market?")
              }
              className="rounded-md bg-down px-4 py-1.5 text-xs font-bold tracking-wider text-white hover:brightness-110 disabled:opacity-50"
            >
              KILL SWITCH
            </button>
          )}
        </div>
      </section>

      {killed && status && (
        <div className="rounded-lg border border-down/50 bg-down/10 px-3 py-2 text-xs text-down" role="alert">
          <span className="font-semibold">STOP active:</span> {status.risk.kill_switch.reason}. No new entries will be
          taken; open positions are being closed.
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-4">
          <SignalPanel status={status} />
          <Positions positions={positions} />
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          {status && <RiskPanel risk={status.risk} />}
          <EventLog events={events} />
        </div>
      </div>

      <PaperChart trades={chartTrades} positions={positions} day={chartDay} onDay={setChartDay} />

      <Journal refreshKey={status?.realized_pnl ?? 0} />
    </main>
  );
}

// ---- strategy picker: run the built-in momentum strategy or any saved one ---------------------
interface SavedStrategy {
  id: string;
  name: string;
  version: number;
  spec: { timeframe: string; underlyings: string[] };
}
const LIVE_TIMEFRAMES = ["1m", "5m"]; // the collector only builds these candles live

function StartControl({ disabled, onStart }: { disabled: boolean; onStart: (body: Record<string, unknown>) => void }) {
  const [saved, setSaved] = useState<SavedStrategy[]>([]);
  const [pick, setPick] = useState("");
  const [unders, setUnders] = useState<string[]>(["NIFTY"]);

  useEffect(() => {
    getJSON<SavedStrategy[]>("/strategies").then(setSaved).catch(() => setSaved([]));
  }, []);

  const chosen = saved.find((s) => s.id === pick);
  const blocked = chosen && !LIVE_TIMEFRAMES.includes(chosen.spec.timeframe);
  const toggle = (u: string) => setUnders((cur) => (cur.includes(u) ? cur.filter((x) => x !== u) : [...cur, u]));

  return (
    <div className="flex flex-wrap items-center gap-2">
      <select value={pick} onChange={(e) => {
        setPick(e.target.value);
        const s = saved.find((x) => x.id === e.target.value);
        if (s) setUnders(s.spec.underlyings.length ? s.spec.underlyings : ["NIFTY"]);
      }}
        className="max-w-[240px] rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-100" aria-label="Strategy to run">
        <option value="">Built-in momentum</option>
        {saved.map((s) => (
          <option key={s.id} value={s.id} disabled={!LIVE_TIMEFRAMES.includes(s.spec.timeframe)}>
            {s.name} · v{s.version} · {s.spec.timeframe}{LIVE_TIMEFRAMES.includes(s.spec.timeframe) ? "" : " (not live yet)"}
          </option>
        ))}
      </select>
      {["NIFTY", "BANKNIFTY"].map((u) => (
        <label key={u} className="flex items-center gap-1 text-[11px] text-ink-300">
          <input type="checkbox" checked={unders.includes(u)} onChange={() => toggle(u)} /> {u}
        </label>
      ))}
      <button
        disabled={disabled || unders.length === 0 || !!blocked}
        onClick={() => onStart(pick ? { strategy_id: pick, underlyings: unders } : { underlyings: unders })}
        className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
      >
        Start runner
      </button>
    </div>
  );
}

// ---- signal panel (PRD §11.4) ---------------------------------------------------------------
function SignalPanel({ status }: { status: PaperStatus | null }) {
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <h2 className="border-b border-ink-800 px-3 py-2 text-sm font-semibold">Signals</h2>
      {!status || status.lanes.length === 0 ? (
        <p className="p-4 text-xs text-ink-400">
          No strategy running. Start the runner to evaluate each closed bar.
        </p>
      ) : (
        status.lanes.map((l) => (
          <div key={l.underlying + l.strategy} className="border-b border-ink-800 p-3 last:border-0">
            <div className="mb-2 flex flex-wrap items-baseline gap-2">
              <span className="text-sm font-semibold">{l.underlying}</span>
              <span className="text-[11px] text-ink-400">
                {l.strategy} · {l.timeframe} · {l.warmed_bars} warm-up bars
              </span>
              <span className="num ml-auto text-[11px] text-ink-400">
                last bar {l.last_bar ? istTime(l.last_bar) : "—"}
              </span>
            </div>
            <Decision d={l.last_decision} />
            {l.signals.length > 1 && (
              <ul className="mt-2 flex flex-col gap-1 text-[11px] text-ink-300">
                {l.signals.slice(1, 5).map((s) => (
                  <li key={s.correlation_id} className="flex gap-2">
                    <span className="num text-ink-400">{istTime(s.ts)}</span>
                    <span className={s.direction === "LONG" ? "text-up" : "text-down"}>{s.direction}</span>
                    <span className={OUTCOME[s.outcome ?? "pending"]?.cls}>{OUTCOME[s.outcome ?? "pending"]?.label}</span>
                    {s.detail && <span className="truncate text-ink-400" title={s.detail}>{s.detail}</span>}
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))
      )}
    </section>
  );
}

function Decision({ d }: { d: PaperDecision | null }) {
  if (!d) return <p className="text-xs text-ink-400">Waiting for the first closed bar…</p>;
  if (d.kind === "no_trade") return <p className="text-xs text-ink-300">No trade: {d.reason}</p>;
  if (d.kind === "none") return <p className="text-xs text-ink-300">No setup on the last bar.</p>;
  if (d.kind === "disabled") return <p className="text-xs text-ink-300">Strategy disabled.</p>;
  const out = OUTCOME[d.outcome ?? "pending"];
  const long = d.direction === "LONG";
  return (
    <div className="rounded-md border border-ink-700 bg-ink-850 p-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`text-sm font-bold ${long ? "text-up" : "text-down"}`}>{d.direction}</span>
        <span className="text-ink-300">{d.strategy}</span>
        <span className="num text-ink-400">confidence {d.confidence != null ? `${Math.round(d.confidence * 100)}%` : "—"}</span>
        <span className={`ml-auto font-semibold ${out?.cls}`}>{out?.label}</span>
      </div>
      <p className="mt-1 text-ink-300">{d.reason}</p>
      {d.option && (
        <div className="num mt-2 grid grid-cols-4 gap-2 text-[11px]">
          <div><div className="text-ink-400">Option</div>{d.option}</div>
          <div><div className="text-ink-400">Entry (ask)</div>{fmtPrice(d.entry)}</div>
          <div><div className="text-ink-400">Stop</div><span className="text-down">{fmtPrice(d.stop)}</span></div>
          <div><div className="text-ink-400">Target</div><span className="text-up">{fmtPrice(d.target)}</span></div>
        </div>
      )}
      {d.detail && <p className={`mt-2 ${out?.cls}`}>{d.detail}</p>}
    </div>
  );
}

// ---- positions (PRD §11.5) --------------------------------------------------------------------
function Positions({ positions }: { positions: PaperPosition[] }) {
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <h2 className="border-b border-ink-800 px-3 py-2 text-sm font-semibold">Open positions</h2>
      {positions.length === 0 ? (
        <p className="p-4 text-xs text-ink-400">Flat.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="num w-full text-xs">
            <thead className="text-[10px] uppercase tracking-wider text-ink-400">
              <tr>
                {["Contract", "State", "Qty", "Entry time", "Entry", "Now", "Stop", "Target", "P&L", "Held"].map((h) => (
                  <th key={h} className="px-3 py-1.5 text-right font-medium first:text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.correlation_id} className="border-t border-ink-800">
                  <td className="px-3 py-1.5">
                    <span className={p.direction === "LONG" ? "text-up" : "text-down"}>{p.direction === "LONG" ? "CE" : "PE"}</span>{" "}
                    {p.symbol}
                  </td>
                  <td className="px-3 text-right">{p.state === "EXIT" ? `exiting: ${p.exit_reason ?? ""}` : p.state.toLowerCase()}</td>
                  <td className="px-3 text-right">{p.quantity}</td>
                  <td className="px-3 text-right">{p.entry_time ? istTime(p.entry_time) : "—"}</td>
                  <td className="px-3 text-right">{fmtPrice(p.entry_price)}</td>
                  <td className="px-3 text-right">{fmtPrice(p.current_price)}</td>
                  <td className="px-3 text-right text-down">{fmtPrice(p.stop)}</td>
                  <td className="px-3 text-right text-up">{fmtPrice(p.target)}</td>
                  <td className={`px-3 text-right ${trendClass(p.unrealized_pnl)}`}>{rupee(p.unrealized_pnl)}</td>
                  <td className="px-3 text-right">{p.minutes_held.toFixed(0)}m</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

// ---- risk limit status (PRD M4) ---------------------------------------------------------------
function Meter({ label, used, max, money = false, invert = false }: { label: string; used: number; max: number; money?: boolean; invert?: boolean }) {
  const pct = max > 0 ? Math.min(Math.max(used / max, 0), 1) : 0;
  const hot = pct >= 0.8;
  const fmt = (v: number) => (money ? rupee(v) : String(v));
  return (
    <div>
      <div className="flex justify-between text-[11px]">
        <span className="text-ink-300">{label}</span>
        <span className={`num ${hot ? "text-down" : "text-ink-100"}`}>
          {fmt(invert ? Math.max(-used, 0) : used)} / {fmt(max)}
        </span>
      </div>
      <div className="mt-1 h-1.5 overflow-hidden rounded bg-ink-800" role="meter" aria-label={label} aria-valuenow={Math.round(pct * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`h-full ${hot ? "bg-down" : "bg-accent"}`} style={{ width: `${pct * 100}%` }} />
      </div>
    </div>
  );
}

function RiskPanel({ risk }: { risk: RiskStatus }) {
  const { limits: L, usage: U } = risk;
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-3">
      <div className="mb-3 flex items-center gap-2">
        <h2 className="text-sm font-semibold">Risk limits</h2>
        <span className={`rounded px-2 py-0.5 text-[10px] font-semibold tracking-widest ring-1 ${
          risk.kill_switch.active ? "bg-down/15 text-down ring-down/40" : "bg-up/15 text-up ring-up/30"}`}>
          {risk.kill_switch.active ? "STOPPED" : "ENTRIES ALLOWED"}
        </span>
        <span className="num ml-auto text-[10px] text-ink-400">enforced by the engine, not by strategies</span>
      </div>
      <div className="flex flex-col gap-2.5">
        <Meter label="Daily loss" used={U.day_pnl} max={L.max_daily_loss} money invert />
        <Meter label="Trades today" used={U.trades_today} max={L.max_trades_per_day} />
        <Meter label="Consecutive losses" used={U.consecutive_losses} max={L.max_consecutive_losses} />
        <Meter label="Open positions" used={U.open_positions} max={L.max_open_positions} />
        <Meter label="Premium exposure" used={U.premium_exposure} max={L.max_premium_exposure} money />
        {Object.entries(U.underlying_exposure).map(([u, v]) => (
          <Meter key={u} label={`${u} exposure`} used={v} max={L.max_underlying_exposure} money />
        ))}
      </div>
      <p className="num mt-3 text-[10px] text-ink-400">
        per trade: risk ≤ {rupee(L.max_risk_per_trade)} · ≤ {L.max_lots_per_trade} lot(s) · quotes fresher than {L.max_data_age_seconds}s
      </p>
    </section>
  );
}

function EventLog({ events }: { events: PaperEvent[] }) {
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <h2 className="border-b border-ink-800 px-3 py-2 text-sm font-semibold">Audit trail (latest)</h2>
      {events.length === 0 ? (
        <p className="p-4 text-xs text-ink-400">Nothing yet.</p>
      ) : (
        <ul className="max-h-64 overflow-y-auto text-[11px]">
          {events.map((e, i) => (
            <li key={i} className="flex gap-2 border-b border-ink-800/60 px-3 py-1">
              <span className="num shrink-0 text-ink-400">{istTime(e.ts)}</span>
              <span className={`shrink-0 ${e.event_type.startsWith("risk.rejected") || e.event_type.startsWith("alert") || e.event_type.startsWith("kill") ? "text-down" : "text-ink-100"}`}>
                {e.event_type}
              </span>
              <span className="num truncate text-ink-400">{e.correlation_id ?? ""}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ---- journal (PRD §11.6) ----------------------------------------------------------------------
interface Filters {
  day: string;
  underlying: string;
  direction: string;
  result: string;
  strategy: string;
  data_source: string;
}
const NO_FILTERS: Filters = { day: "", underlying: "", direction: "", result: "", strategy: "", data_source: "" };

function Journal({ refreshKey }: { refreshKey: number }) {
  const [f, setF] = useState<Filters>(NO_FILTERS);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [summary, setSummary] = useState<PaperSummary | null>(null);
  const [trace, setTrace] = useState<{ id: string; events: PaperEvent[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v));
    getJSON<{ trades: PaperTrade[]; summary: PaperSummary }>(`/paper/journal?${q}`)
      .then((r) => {
        setTrades(r.trades);
        setSummary(r.summary);
        setError(null);
      })
      .catch((e) => setError((e as Error).message));
  }, [f, refreshKey]);

  const open = async (id: string) => {
    try {
      const r = await getJSON<{ events: { ts: string; event_type: string; payload: Record<string, unknown> }[] }>(`/paper/trace/${id}`);
      setTrace({ id, events: r.events.map((e) => ({ ...e, correlation_id: id })) });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const sel = (key: keyof Filters, label: string, options: [string, string][]) => (
    <label className="flex flex-col gap-0.5 text-[10px] uppercase tracking-wider text-ink-400">
      {label}
      <select
        value={f[key]}
        onChange={(e) => setF({ ...f, [key]: e.target.value })}
        className="rounded border border-ink-700 bg-ink-850 px-2 py-1 text-xs normal-case tracking-normal text-ink-100"
      >
        <option value="">All</option>
        {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
      </select>
    </label>
  );

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <div className="flex flex-wrap items-end gap-3 border-b border-ink-800 px-3 py-2">
        <h2 className="mr-2 text-sm font-semibold">Journal</h2>
        <label className="flex flex-col gap-0.5 text-[10px] uppercase tracking-wider text-ink-400">
          Date
          <input type="date" value={f.day} onChange={(e) => setF({ ...f, day: e.target.value })}
            className="rounded border border-ink-700 bg-ink-850 px-2 py-1 text-xs text-ink-100" />
        </label>
        {sel("underlying", "Symbol", [["NIFTY", "NIFTY"], ["BANKNIFTY", "BANKNIFTY"]])}
        {sel("strategy", "Strategy", Object.keys(summary?.by_strategy ?? {}).map((k) => [k, k]))}
        {sel("direction", "Direction", [["LONG", "Long (CE)"], ["SHORT", "Short (PE)"]])}
        {sel("result", "Result", [["win", "Wins"], ["loss", "Losses"]])}
        {sel("data_source", "Data", [["fyers", "Live"], ["replay", "Replay"], ["simulated", "Simulated"]])}
        {Object.values(f).some(Boolean) && (
          <button onClick={() => setF(NO_FILTERS)} className="pb-1 text-xs text-accent hover:underline">Clear</button>
        )}
      </div>

      {error && <p className="px-3 py-2 text-xs text-down" role="alert">{error}</p>}

      {summary && (
        <div className="grid grid-cols-2 gap-px border-b border-ink-800 bg-ink-800 sm:grid-cols-6">
          <Stat label="Trades" value={`${summary.trades}`} sub={`${summary.wins}W / ${summary.losses}L`} />
          <Stat label="Net P&L" value={rupee(summary.net_pnl)} tone={trendClass(summary.net_pnl)} />
          <Stat label="Gross P&L" value={rupee(summary.gross_pnl)} sub={`charges ${rupee(summary.charges)}`} />
          <Stat label="Win rate" value={summary.win_rate == null ? "—" : `${(summary.win_rate * 100).toFixed(0)}%`} />
          <Stat label="Max drawdown" value={rupee(summary.max_drawdown)} />
          <Stat label="Profit factor" value={summary.profit_factor == null ? "—" : summary.profit_factor.toFixed(2)} />
        </div>
      )}

      {trades.length === 0 ? (
        <p className="p-4 text-xs text-ink-400">No trades match.</p>
      ) : (
        <div className="max-h-[420px] overflow-auto">
          <table className="num w-full text-xs">
            <thead className="sticky top-0 bg-ink-900 text-[10px] uppercase tracking-wider text-ink-400">
              <tr>
                {["Day", "Entry time", "Exit time", "Contract", "Strategy", "Entry", "Exit", "Reason", "Net P&L", "Data"].map((h) => (
                  <th key={h} className="px-3 py-1.5 text-right font-medium first:text-left">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id} onClick={() => open(t.correlation_id)} title="Show the audit trail for this trade"
                  className="cursor-pointer border-t border-ink-800 hover:bg-ink-850">
                  <td className="px-3 py-1.5">{t.trading_day}</td>
                  <td className="px-3 text-right">{istTime(t.entry_time)}</td>
                  <td className="px-3 text-right">{istTime(t.exit_time)}</td>
                  <td className="px-3 text-right">
                    <span className={t.direction === "LONG" ? "text-up" : "text-down"}>{t.direction === "LONG" ? "CE" : "PE"}</span> {t.symbol}
                  </td>
                  <td className="px-3 text-right">{t.strategy}</td>
                  <td className="px-3 text-right">{fmtPrice(t.entry_price)}</td>
                  <td className="px-3 text-right">{fmtPrice(t.exit_price)}</td>
                  <td className="px-3 text-right">{t.exit_reason}</td>
                  <td className={`px-3 text-right font-medium ${trendClass(t.net_pnl)}`}>{rupee(t.net_pnl)}</td>
                  <td className="px-3 text-right text-ink-400" title={t.option_pricing}>{t.data_source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {trace && (
        <div className="fixed inset-0 z-20 grid place-items-center bg-black/60 p-4" onClick={() => setTrace(null)}>
          <div className="max-h-[80vh] w-full max-w-3xl overflow-auto rounded-lg border border-ink-700 bg-ink-900 p-4 text-xs"
            role="dialog" aria-label="Trade audit trail" onClick={(e) => e.stopPropagation()}>
            <div className="mb-3 flex items-center">
              <h3 className="text-sm font-semibold">Audit trail <span className="num text-ink-400">{trace.id}</span></h3>
              <button onClick={() => setTrace(null)} className="ml-auto text-ink-300 hover:text-ink-100">Close</button>
            </div>
            <ol className="flex flex-col gap-2">
              {trace.events.map((e, i) => (
                <li key={i} className="rounded border border-ink-800 bg-ink-850 p-2">
                  <div className="flex gap-2"><span className="num text-ink-400">{istTime(e.ts)}</span><span className="font-semibold">{e.event_type}</span></div>
                  <pre className="num mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words text-[10px] text-ink-300">
                    {JSON.stringify(e.payload, null, 1)}
                  </pre>
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </section>
  );
}

function Stat({ label, value, sub, tone = "" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="bg-ink-900 px-3 py-2">
      <div className="text-[11px] text-ink-400">{label}</div>
      <div className={`num mt-0.5 text-base font-semibold ${tone}`}>{value}</div>
      {sub && <div className="num text-[10px] text-ink-400">{sub}</div>}
    </div>
  );
}
