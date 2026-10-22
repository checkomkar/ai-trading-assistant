import { useCallback, useEffect, useRef, useState } from "react";
import { del, getJSON, postJSON, type BacktestMetrics, type BacktestTrade } from "../api";
import { useAuth } from "../auth";
import { fmtInt, fmtPrice, trendClass } from "../format";
import { EquityChart } from "../components/backtest/charts";

interface Universe {
  universes: Record<string, string[]>;
  coverage: { symbols: number; candles: number; from: string | null; to: string | null };
  latest_day: string | null;
}
interface Saved {
  id: string;
  name: string;
  version: number;
  spec: Record<string, unknown>;
}
interface PortfolioRow {
  id: string;
  name: string;
  capital: number;
  start_day: string;
  equity?: number;
  return_pct?: number;
  days_running?: number;
  positions?: number;
  trades?: number;
  error?: string;
}
interface Position {
  symbol: string;
  quantity: number;
  entry_day: string;
  entry_price: number;
  last: number;
  value: number;
  pnl: number;
  pnl_pct: number;
  stop: number;
  target: number;
  days_held: number;
  exit_planned: string | null;
}
interface Portfolio {
  id: string;
  name: string;
  capital: number;
  start_day: string;
  last_data_day: string | null;
  english: string;
  equity: number;
  cash: number;
  return_pct: number;
  days_running: number;
  positions: Position[];
  orders: { symbol: string; side: "BUY" | "SELL"; note: string }[];
  metrics: BacktestMetrics;
  trades: BacktestTrade[];
  warnings: string[];
  max_positions: number;
}
interface Refresh {
  running: boolean;
  candles?: number;
  failed?: Record<string, string>;
  error?: string;
}

const rupee = (v: number | null | undefined) => (v == null ? "—" : `${v < 0 ? "−" : ""}₹${fmtPrice(Math.abs(v))}`);
const inp = "rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-ink-100 outline-none focus:border-accent";

export function StocksPage() {
  const { me } = useAuth();
  const [uni, setUni] = useState<Universe | null>(null);
  const [strategies, setStrategies] = useState<Saved[]>([]);
  const [rows, setRows] = useState<PortfolioRow[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Portfolio | null>(null);
  const [pick, setPick] = useState("");
  const [capital, setCapital] = useState(100000);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refresh, setRefresh] = useState<Refresh | null>(null);
  const poll = useRef<ReturnType<typeof setTimeout>>();

  const load = useCallback(() => {
    getJSON<Universe>("/equity/universe").then(setUni).catch(() => {});
    getJSON<PortfolioRow[]>("/equity/paper").then(setRows).catch((e: Error) => setError(e.message));
  }, []);
  useEffect(() => {
    load();
    getJSON<Saved[]>("/strategies")
      .then((all) => {
        const stock = all.filter((s) => s.spec.market === "equity");
        setStrategies(stock);
        setPick((p) => p || stock[0]?.id || "");
      })
      .catch(() => {});
  }, [load]);
  useEffect(() => {
    if (!open) return setDetail(null);
    getJSON<Portfolio>(`/equity/paper/${open}`).then(setDetail).catch((e: Error) => setError(e.message));
  }, [open, rows]);

  const act = async (label: string, fn: () => Promise<void>) => {
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

  const create = () =>
    act("create", async () => {
      const p = await postJSON<Portfolio>("/equity/paper", { strategy_id: pick, capital });
      load();
      setOpen(p.id);
    });
  const remove = (id: string) =>
    act("remove", async () => {
      await del(`/equity/paper/${id}`);
      setOpen(null);
      load();
    });

  const pollRefresh = useCallback(() => {
    getJSON<Refresh>("/equity/refresh")
      .then((r) => {
        setRefresh(r);
        if (r.running) poll.current = setTimeout(pollRefresh, 3000);
        else load();
      })
      .catch(() => {});
  }, [load]);
  useEffect(() => () => clearTimeout(poll.current), []);
  const startRefresh = () =>
    act("refresh", async () => {
      setRefresh(await postJSON<Refresh>("/equity/refresh", {}));
      poll.current = setTimeout(pollRefresh, 3000);
    });

  const cov = uni?.coverage;
  return (
    <main className="mx-auto flex w-full max-w-[1200px] flex-1 flex-col gap-4 p-4 sm:p-6">
      <header>
        <h1 className="text-lg font-semibold">Stocks · paper portfolios</h1>
        <p className="mt-1 max-w-3xl text-xs leading-relaxed text-ink-400">
          Forward-test a daily-chart stock strategy on the NIFTY 50. Decisions use each day's closing candle and the paper
          orders fill at the next day's open, with delivery charges. Paper only: nothing here places a real order. Build and
          backtest stock strategies on the Strategies page (ready-made ones are under "Stocks").
        </p>
      </header>

      {error && (
        <div className="rounded-lg border border-down/40 bg-down/10 px-3 py-2 text-xs text-down" role="alert">
          {error}
        </div>
      )}

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <div>
            <div className="text-[11px] text-ink-400">Daily candles stored</div>
            <div className="num text-sm text-ink-100">
              {cov?.symbols ? `${cov.symbols} stocks · ${fmtInt(cov.candles)} candles · ${cov.from} → ${cov.to}` : "none yet"}
            </div>
          </div>
          {me?.is_admin && (
            <button
              onClick={startRefresh}
              disabled={busy === "refresh" || refresh?.running}
              className="ml-auto rounded-md border border-ink-700 px-3 py-1.5 text-ink-100 hover:border-accent disabled:opacity-50"
            >
              {refresh?.running ? "Fetching from the broker…" : "Fetch latest daily candles"}
            </button>
          )}
        </div>
        {!cov?.symbols && (
          <p className="mt-2 text-ink-400">
            No stock history is loaded yet, so backtests and portfolios cannot run. The operator loads it from the broker
            (a Fyers sign-in is needed), after market close each day.
          </p>
        )}
        {refresh && !refresh.running && (refresh.candles != null || refresh.error) && (
          <p className={`mt-2 ${refresh.error ? "text-down" : "text-ink-300"}`}>
            {refresh.error ?? `Stored ${fmtInt(refresh.candles)} candles.`}
            {refresh.failed && Object.keys(refresh.failed).length > 0 &&
              ` Failed: ${Object.keys(refresh.failed).join(", ")}.`}
          </p>
        )}
      </section>

      <section className="rounded-lg border border-ink-800 bg-ink-900 p-3 text-xs">
        <h2 className="mb-2 text-sm font-semibold">Start a paper portfolio</h2>
        {strategies.length === 0 ? (
          <p className="text-ink-400">
            Save a stock strategy first: open the Strategies page, pick one from Ready-made → Stocks (or describe your own),
            then "Save version".
          </p>
        ) : (
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className="text-ink-400">Strategy</span>
              <select value={pick} onChange={(e) => setPick(e.target.value)} className={`${inp} min-w-[260px]`}>
                {strategies.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name} · v{s.version}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-ink-400">Capital ₹</span>
              <input type="number" min={10000} step={10000} value={capital} onChange={(e) => setCapital(Number(e.target.value) || 100000)} className={`${inp} w-32`} />
            </label>
            <button
              onClick={create}
              disabled={!!busy || !pick || !cov?.symbols}
              className="rounded-md bg-accent px-4 py-1.5 font-semibold text-ink-950 hover:brightness-110 disabled:opacity-50"
            >
              {busy === "create" ? "Starting…" : "Start from the latest close"}
            </button>
          </div>
        )}
      </section>

      <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {rows.map((r) => (
          <button
            key={r.id}
            onClick={() => setOpen(r.id)}
            aria-pressed={open === r.id}
            className={`rounded-lg border bg-ink-900 p-3 text-left text-xs hover:border-accent ${open === r.id ? "border-accent" : "border-ink-800"}`}
          >
            <div className="truncate text-sm font-semibold text-ink-100">{r.name}</div>
            {r.error ? (
              <div className="mt-1 text-down">{r.error}</div>
            ) : (
              <>
                <div className={`num mt-1 text-lg font-semibold ${trendClass(r.return_pct)}`}>
                  {rupee(r.equity)} <span className="text-xs">({(r.return_pct ?? 0) > 0 ? "+" : ""}{r.return_pct}%)</span>
                </div>
                <div className="num mt-0.5 text-[11px] text-ink-400">
                  since {r.start_day} · {r.positions} open · {r.trades} closed
                </div>
              </>
            )}
          </button>
        ))}
        {rows.length === 0 && <p className="text-xs text-ink-400">No portfolios yet.</p>}
      </section>

      {detail && <PortfolioView p={detail} onRemove={() => remove(detail.id)} removing={busy === "remove"} />}
    </main>
  );
}

function PortfolioView({ p, onRemove, removing }: { p: Portfolio; onRemove: () => void; removing: boolean }) {
  const m = p.metrics;
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">{p.name}</h2>
        <span className="num text-[11px] text-ink-400">
          started {p.start_day} · data to {p.last_data_day ?? "—"} · {p.days_running} trading days
        </span>
        <button onClick={onRemove} disabled={removing} className="ml-auto rounded-md border border-ink-700 px-3 py-1 text-xs text-ink-300 hover:border-down hover:text-down disabled:opacity-50">
          Remove portfolio
        </button>
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-ink-800 bg-ink-800 sm:grid-cols-4">
        <Tile label="Value" value={rupee(p.equity)} sub={`${p.return_pct > 0 ? "+" : ""}${p.return_pct}% on ${rupee(p.capital)}`} tone={trendClass(p.return_pct)} />
        <Tile label="Cash" value={rupee(p.cash)} sub={`${p.positions.length} of ${p.max_positions} positions`} />
        <Tile label="Closed trades" value={fmtInt(m.trades)} sub={m.win_rate == null ? "" : `${m.win_rate}% winners`} />
        <Tile label="Max drawdown" value={rupee(m.max_drawdown)} sub={`${m.max_drawdown_pct ?? "—"}% from peak`} />
      </div>

      {p.orders.length > 0 && (
        <section className="rounded-lg border border-accent/30 bg-accent/5 p-3 text-xs">
          <h3 className="mb-1 text-sm font-semibold">Orders for the next open (paper)</h3>
          <ul className="flex flex-col gap-0.5">
            {p.orders.map((o) => (
              <li key={`${o.symbol}${o.side}`}>
                <span className={o.side === "BUY" ? "text-up" : "text-down"}>{o.side}</span> <b>{o.symbol}</b>{" "}
                <span className="text-ink-300">{o.note}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-lg border border-ink-800 bg-ink-900">
        <h3 className="border-b border-ink-800 px-3 py-2 text-sm font-semibold">Open positions</h3>
        <div className="overflow-x-auto">
          <table className="num w-full min-w-[720px] text-xs">
            <thead className="bg-ink-850 text-[10px] uppercase tracking-wider text-ink-400">
              <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:font-medium">
                <th className="text-left">Stock</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Entry ₹</th>
                <th className="text-right">Last ₹</th>
                <th className="text-right">P&L</th>
                <th className="text-right">Stop / Target</th>
                <th className="text-right">Days</th>
                <th className="text-left">Plan</th>
              </tr>
            </thead>
            <tbody>
              {p.positions.map((x) => (
                <tr key={x.symbol} className="border-t border-ink-800/70">
                  <td className="px-2 py-1.5 font-sans font-medium">{x.symbol}</td>
                  <td className="px-2 py-1.5 text-right">{x.quantity}</td>
                  <td className="px-2 py-1.5 text-right">{fmtPrice(x.entry_price)}</td>
                  <td className="px-2 py-1.5 text-right">{fmtPrice(x.last)}</td>
                  <td className={`px-2 py-1.5 text-right ${trendClass(x.pnl)}`}>
                    {rupee(x.pnl)} <span className="text-[10px]">({x.pnl_pct}%)</span>
                  </td>
                  <td className="px-2 py-1.5 text-right text-ink-300">
                    {fmtPrice(x.stop)} / {fmtPrice(x.target)}
                  </td>
                  <td className="px-2 py-1.5 text-right">{x.days_held}</td>
                  <td className="px-2 py-1.5 font-sans text-ink-300">{x.exit_planned ?? "hold"}</td>
                </tr>
              ))}
              {p.positions.length === 0 && (
                <tr>
                  <td colSpan={8} className="py-6 text-center text-ink-400">
                    Nothing held. Entries happen at an open after a day's rules were met.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <EquityChart metrics={m} capital={p.capital} />

      <section className="rounded-lg border border-ink-800 bg-ink-900">
        <h3 className="border-b border-ink-800 px-3 py-2 text-sm font-semibold">Closed trades</h3>
        <div className="overflow-x-auto">
          <table className="num w-full min-w-[640px] text-xs">
            <thead className="bg-ink-850 text-[10px] uppercase tracking-wider text-ink-400">
              <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:font-medium">
                <th className="text-left">Entry</th>
                <th className="text-left">Stock</th>
                <th className="text-right">Qty</th>
                <th className="text-right">Entry ₹</th>
                <th className="text-right">Exit ₹</th>
                <th className="text-left">Exit</th>
                <th className="text-right">Net P&L</th>
              </tr>
            </thead>
            <tbody>
              {[...p.trades].reverse().slice(0, 100).map((t) => (
                <tr key={t.trade_id} className="border-t border-ink-800/70">
                  <td className="px-2 py-1.5">{t.entry_time.slice(0, 10)}</td>
                  <td className="px-2 py-1.5 font-sans">{t.contract}</td>
                  <td className="px-2 py-1.5 text-right">{t.quantity}</td>
                  <td className="px-2 py-1.5 text-right">{fmtPrice(t.entry_price)}</td>
                  <td className="px-2 py-1.5 text-right">{fmtPrice(t.exit_price)}</td>
                  <td className="px-2 py-1.5 font-sans text-ink-300">{t.exit_reason}</td>
                  <td className={`px-2 py-1.5 text-right font-medium ${trendClass(t.net_pnl)}`}>{rupee(t.net_pnl)}</td>
                </tr>
              ))}
              {p.trades.length === 0 && (
                <tr>
                  <td colSpan={7} className="py-6 text-center text-ink-400">No closed trades yet.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <details className="rounded-lg border border-ink-800 bg-ink-900 p-3 text-xs text-ink-300">
        <summary className="cursor-pointer text-ink-100">The rules this portfolio follows</summary>
        <pre className="mt-2 whitespace-pre-wrap font-sans leading-relaxed">{p.english}</pre>
      </details>
    </section>
  );
}

function Tile({ label, value, sub, tone = "" }: { label: string; value: string; sub?: string; tone?: string }) {
  return (
    <div className="bg-ink-900 px-3 py-2.5">
      <div className="text-[11px] text-ink-400">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold tracking-tight ${tone}`}>{value}</div>
      {sub && <div className="num mt-0.5 truncate text-[10px] text-ink-400" title={sub}>{sub}</div>}
    </div>
  );
}
