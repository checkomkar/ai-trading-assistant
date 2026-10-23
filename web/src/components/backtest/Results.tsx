import { useEffect, useMemo, useRef, useState } from "react";
import type { BacktestResult, BacktestTrade } from "../../api";
import { fmtInt, fmtPrice, trendClass } from "../../format";
import { DailyPnlChart, EquityChart } from "./charts";
import { TradeChart } from "./TradeChart";

const rupee = (v: number | null | undefined) =>
  v == null ? "—" : `${v < 0 ? "−" : ""}₹${fmtPrice(Math.abs(v))}`;
const num = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));

export function Results({ r }: { r: BacktestResult }) {
  const m = r.metrics;
  const [selected, setSelected] = useState<BacktestTrade | null>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  useEffect(() => setSelected(null), [r.id]);
  const select = (t: BacktestTrade | null) => {
    setSelected(t);
    if (t) chartRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  const capital = Number((r.params.engine as { capital?: number })?.capital ?? 100000);
  const daily = r.timeframe === "1d"; // a stock run
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-base font-semibold">
          {r.underlying} · {r.start} → {r.end}
        </h2>
        <Badge tone={r.data_source === "real" ? "up" : "sim"}>{r.data_source === "real" ? "REAL DATA" : "SIMULATED DATA"}</Badge>
        {daily ? (
          <Badge tone="accent">STOCKS · DAILY · DELIVERY COSTS</Badge>
        ) : (
          <Badge tone={r.pricing_mode === "real" ? "up" : "accent"}>
            {r.pricing_mode === "real" ? "REAL OPTION PRICES" : r.pricing_mode === "hybrid" ? "REAL + MODELLED OPTION PRICES" : "SYNTHETIC OPTION PRICES"}
          </Badge>
        )}
        <span className="num ml-auto text-[11px] text-ink-400">
          run {r.id} · {r.timeframe}{r.built_from && r.built_from !== r.timeframe ? ` (from ${r.built_from})` : ""} · {fmtInt(r.bars)} bars · {r.days} days · {r.signals} signals
        </span>
      </div>

      {r.warnings.length > 0 && (
        <ul className="flex flex-col gap-1 rounded-lg border border-accent/30 bg-accent/5 px-3 py-2 text-xs text-ink-100" role="note">
          {r.warnings.map((w) => (
            <li key={w} className="flex gap-2">
              <span aria-hidden className="text-accent">!</span>
              {w}
            </li>
          ))}
        </ul>
      )}

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-ink-800 bg-ink-800 sm:grid-cols-4">
        <Tile label="Net P&L" value={rupee(m.total_net_pnl)} tone={trendClass(m.total_net_pnl)} sub={`${num(m.return_pct)}% of capital`} />
        <Tile label="Trades" value={fmtInt(m.trades)} sub={`${m.days_traded} of ${m.trading_days} days traded`} />
        <Tile label="Win rate" value={m.win_rate == null ? "—" : `${num(m.win_rate, 1)}%`} sub={`avg win ${rupee(m.average_win)} · avg loss ${rupee(m.average_loss)}`} />
        <Tile label="Profit factor" value={num(m.profit_factor)} sub={`expectancy ${rupee(m.expectancy)} / trade`} />
        <Tile label="Max drawdown" value={rupee(m.max_drawdown)} sub={`${num(m.max_drawdown_pct)}% from peak`} />
        <Tile label="Sharpe · Sortino" value={`${num(m.sharpe)} · ${num(m.sortino)}`} sub="daily, annualised" />
        <Tile label="Charges" value={rupee(m.total_charges)} sub={`gross ${rupee(m.total_gross_pnl)}`} />
        <Tile
          label="Avg hold · MAE / MFE"
          value={daily ? `${num(m.average_holding_days ?? null, 1)} days` : `${num(m.average_holding_minutes, 0)} min`}
          sub={`${num(m.average_mae_pct, 1)}% / +${num(m.average_mfe_pct, 1)}%${daily && m.exposure_pct != null ? ` · invested ${num(m.exposure_pct, 0)}%` : ""}`}
        />
      </div>

      <div ref={chartRef} className="scroll-mt-4">
        <TradeChart runId={r.id} trades={r.trades} selected={selected} onSelect={setSelected} daily={daily} />
      </div>

      <div className="grid grid-cols-1 gap-4 2xl:grid-cols-2">
        <EquityChart metrics={m} capital={capital} />
        <DailyPnlChart metrics={m} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Breakdown title="Exit reasons" data={m.exit_reasons} />
        <Breakdown title="No-trade reasons (bars)" data={r.no_trade_reasons} />
        <section className="rounded-lg border border-ink-800 bg-ink-900 p-3 text-xs">
          <h3 className="mb-2 text-sm font-semibold">{daily ? "By stock" : "By direction"}</h3>
          <table className="num w-full">
            <thead className="text-[10px] uppercase tracking-wider text-ink-400">
              <tr>
                <th className="text-left font-medium">{daily ? "Stock" : "Side"}</th>
                <th className="text-right font-medium">Trades</th>
                <th className="text-right font-medium">Win %</th>
                <th className="text-right font-medium">Net P&L</th>
              </tr>
            </thead>
            <tbody>
              {daily
                ? (m.by_symbol ?? []).map((v) => (
                    <tr key={v.symbol} className="border-t border-ink-800">
                      <td className="py-1">{v.symbol}</td>
                      <td className="text-right">{v.trades}</td>
                      <td className="text-right">{num(v.win_rate, 1)}</td>
                      <td className={`text-right ${trendClass(v.net_pnl)}`}>{rupee(v.net_pnl)}</td>
                    </tr>
                  ))
                : Object.entries(m.by_direction).map(([k, v]) => (
                    <tr key={k} className="border-t border-ink-800">
                      <td className="py-1">{k === "LONG" ? "Long (CE)" : "Short (PE)"}</td>
                      <td className="text-right">{v.trades}</td>
                      <td className="text-right">{num(v.win_rate, 1)}</td>
                      <td className={`text-right ${trendClass(v.net_pnl)}`}>{rupee(v.net_pnl)}</td>
                    </tr>
                  ))}
            </tbody>
          </table>
          {m.monthly_pnl.length > 0 && (
            <>
              <h3 className="mb-1 mt-3 text-sm font-semibold">By month</h3>
              <table className="num w-full">
                <tbody>
                  {m.monthly_pnl.map((x) => (
                    <tr key={x.month} className="border-t border-ink-800">
                      <td className="py-1">{x.month}</td>
                      <td className={`text-right ${trendClass(x.pnl)}`}>{rupee(x.pnl)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </section>
      </div>

      <TradesTable trades={r.trades} selectedId={selected?.trade_id ?? null} onSelect={select} daily={daily} />
    </div>
  );
}

function Badge({ tone, children }: { tone: "up" | "sim" | "accent"; children: React.ReactNode }) {
  const cls = { up: "text-up ring-up/40 bg-up/10", sim: "text-sim ring-sim/40 bg-sim/10", accent: "text-accent ring-accent/40 bg-accent/10" }[tone];
  return <span className={`rounded px-2 py-0.5 text-[10px] font-semibold tracking-widest ring-1 ${cls}`}>{children}</span>;
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

function Breakdown({ title, data }: { title: string; data: Record<string, number> }) {
  const rows = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...rows.map(([, v]) => v));
  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900 p-3 text-xs">
      <h3 className="mb-2 text-sm font-semibold">{title}</h3>
      {rows.length === 0 && <p className="text-ink-400">None</p>}
      <ul className="flex flex-col gap-1.5">
        {rows.map(([k, v]) => (
          <li key={k} title={`${k}: ${v}`}>
            <div className="flex justify-between gap-2">
              <span className="truncate text-ink-300">{k}</span>
              <span className="num text-ink-100">{fmtInt(v)}</span>
            </div>
            <div className="mt-0.5 h-1.5 rounded-sm bg-ink-850">
              <div className="h-full rounded-sm bg-ink-400" style={{ width: `${(v / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

const PAGE = 50;

function TradesTable({ trades, selectedId, onSelect, daily }: { trades: BacktestTrade[]; selectedId: string | null; onSelect: (t: BacktestTrade) => void; daily: boolean }) {
  const [page, setPage] = useState(0);
  const [filter, setFilter] = useState<"all" | "win" | "loss">("all");
  const rows = useMemo(
    () => trades.filter((t) => filter === "all" || (filter === "win" ? t.net_pnl > 0 : t.net_pnl <= 0)),
    [trades, filter],
  );
  const pages = Math.max(1, Math.ceil(rows.length / PAGE));
  const shown = rows.slice(page * PAGE, page * PAGE + PAGE);
  const ist = (s: string) =>
    new Date(s).toLocaleString("en-IN", daily
      ? { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "2-digit" }
      : { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });

  return (
    <section className="rounded-lg border border-ink-800 bg-ink-900">
      <div className="flex flex-wrap items-center gap-2 border-b border-ink-800 px-3 py-2">
        <h3 className="text-sm font-semibold">Trades</h3>
        <span className="num text-[11px] text-ink-400">{rows.length}</span>
        <div className="ml-auto flex gap-1 rounded-md bg-ink-850 p-0.5 text-xs">
          {(["all", "win", "loss"] as const).map((f) => (
            <button
              key={f}
              onClick={() => {
                setFilter(f);
                setPage(0);
              }}
              className={`rounded px-2 py-0.5 ${filter === f ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"}`}
            >
              {f === "all" ? "All" : f === "win" ? "Winners" : "Losers"}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="num w-full min-w-[860px] text-xs">
          <thead className="bg-ink-850 text-[10px] uppercase tracking-wider text-ink-400">
            <tr className="[&>th]:px-2 [&>th]:py-1.5 [&>th]:font-medium">
              <th className="text-left">{daily ? "Entry" : "Entry (IST)"}</th>
              <th className="text-left">{daily ? "Stock" : "Contract"}</th>
              {daily && <th className="text-right">Qty</th>}
              <th className="text-right">Entry ₹</th>
              <th className="text-right">Exit ₹</th>
              <th className="text-left">Exit</th>
              <th className="text-right">Held</th>
              <th className="text-right">MAE / MFE</th>
              <th className="text-right">Charges</th>
              <th className="text-right">Net P&L</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <tr
                key={t.trade_id}
                onClick={() => onSelect(t)}
                aria-selected={selectedId === t.trade_id}
                className={`cursor-pointer border-t border-ink-800/70 hover:bg-ink-800/50 ${selectedId === t.trade_id ? "bg-ink-800" : ""}`}
                title={`${t.signal_reason} — click to show on chart`}
              >
                <td className="px-2 py-1.5">{ist(t.entry_time)}</td>
                <td className="px-2 py-1.5 font-sans">
                  <span className={t.direction === "LONG" ? "text-up" : "text-down"} aria-hidden>
                    {t.direction === "LONG" ? "▲ " : "▼ "}
                  </span>
                  {t.contract}
                </td>
                {daily && <td className="px-2 py-1.5 text-right">{t.quantity}</td>}
                <td className="px-2 py-1.5 text-right">{fmtPrice(t.entry_price)}</td>
                <td className="px-2 py-1.5 text-right">{fmtPrice(t.exit_price)}</td>
                <td className="px-2 py-1.5 font-sans text-ink-300">{t.exit_reason}</td>
                <td className="px-2 py-1.5 text-right">{daily ? `${Math.round(t.minutes_held / 375)}d` : `${Math.round(t.minutes_held)}m`}</td>
                <td className="px-2 py-1.5 text-right text-ink-300">
                  {t.mae_pct.toFixed(0)}% / +{t.mfe_pct.toFixed(0)}%
                </td>
                <td className="px-2 py-1.5 text-right text-ink-300">{fmtPrice(t.charges)}</td>
                <td className={`px-2 py-1.5 text-right font-medium ${trendClass(t.net_pnl)}`}>{rupee(t.net_pnl)}</td>
              </tr>
            ))}
            {shown.length === 0 && (
              <tr>
                <td colSpan={daily ? 10 : 9} className="py-8 text-center text-ink-400">No trades</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex items-center justify-end gap-2 border-t border-ink-800 px-3 py-2 text-xs">
          <button disabled={page === 0} onClick={() => setPage(page - 1)} className="rounded px-2 py-0.5 text-ink-300 hover:text-ink-100 disabled:opacity-40">
            ← Prev
          </button>
          <span className="num text-ink-400">
            {page + 1} / {pages}
          </span>
          <button disabled={page >= pages - 1} onClick={() => setPage(page + 1)} className="rounded px-2 py-0.5 text-ink-300 hover:text-ink-100 disabled:opacity-40">
            Next →
          </button>
        </div>
      )}
    </section>
  );
}
