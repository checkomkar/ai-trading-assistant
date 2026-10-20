import { useEffect, useState } from "react";
import { getJSON, type Coverage } from "../../api";
import { fmtInt } from "../../format";

export interface FormState {
  underlying: "NIFTY" | "BANKNIFTY";
  start: string;
  end: string;
  timeframe: string;
  source: "auto" | "real" | "simulated";
  pricing: "synthetic" | "real";
  stop_loss_pct: number;
  target_pct: number;
  rsi_long: number;
  rsi_short: number;
  adx_min: number;
  max_trades_per_day: number;
  use_vwap: boolean;
  entry_start: string;
  entry_end: string;
  lots: number;
  max_daily_loss: number;
  trail_activate_pct: number | null;
  trail_pct: number | null;
  spread_pct: number;
  slippage_ticks: number;
}

const iso = (d: Date) => d.toISOString().slice(0, 10);

export const DEFAULT_FORM: FormState = {
  underlying: "NIFTY",
  start: iso(new Date(Date.now() - 180 * 864e5)),
  end: iso(new Date()),
  timeframe: "5m",
  source: "auto",
  pricing: "synthetic",
  stop_loss_pct: 25,
  target_pct: 50,
  rsi_long: 55,
  rsi_short: 45,
  adx_min: 18,
  max_trades_per_day: 3,
  use_vwap: true,
  entry_start: "09:30",
  entry_end: "14:45",
  lots: 1,
  max_daily_loss: 5000,
  trail_activate_pct: 30,
  trail_pct: 15,
  spread_pct: 0.3,
  slippage_ticks: 1,
};

export function toRequest(f: FormState) {
  return {
    underlying: f.underlying,
    start: f.start,
    end: f.end,
    timeframe: f.timeframe,
    source: f.source,
    pricing: f.pricing,
    strategy: "momentum",
    strategy_params: {
      stop_loss_pct: f.stop_loss_pct,
      target_pct: f.target_pct,
      rsi_long: f.rsi_long,
      rsi_short: f.rsi_short,
      adx_min: f.adx_min,
      max_trades_per_day: f.max_trades_per_day,
      use_vwap: f.use_vwap,
      entry_start: f.entry_start,
      entry_end: f.entry_end,
    },
    engine_params: {
      lots: f.lots,
      max_daily_loss: f.max_daily_loss,
      trail_activate_pct: f.trail_activate_pct,
      trail_pct: f.trail_pct,
    },
    execution_params: { spread_pct: f.spread_pct, slippage_ticks: f.slippage_ticks },
  };
}

interface Props {
  form: FormState;
  onChange: (f: FormState) => void;
  onRun: () => void;
  running: boolean;
}

export function BacktestForm({ form, onChange, onRun, running }: Props) {
  const [cov, setCov] = useState<Coverage | null>(null);
  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => onChange({ ...form, [k]: v });

  useEffect(() => {
    getJSON<Coverage>(`/backtests/coverage?underlying=${form.underlying}&timeframe=${form.timeframe}`)
      .then(setCov)
      .catch(() => setCov(null));
  }, [form.underlying, form.timeframe, running]);

  const effective = form.source === "auto" ? (cov?.real.candles ? "real" : "simulated") : form.source;
  const range = cov ? cov[effective === "real" ? "real" : "simulated"] : null;
  const realOptions = cov?.real.option_contracts_with_candles ?? 0;

  return (
    <form
      className="flex flex-col gap-4 text-xs"
      onSubmit={(e) => {
        e.preventDefault();
        onRun();
      }}
    >
      <Section title="Market & data">
        <Segmented value={form.underlying} options={["NIFTY", "BANKNIFTY"]} onChange={(v) => set("underlying", v as FormState["underlying"])} />
        <div className="grid grid-cols-2 gap-2">
          <Field label="From">
            <input type="date" value={form.start} max={form.end} onChange={(e) => set("start", e.target.value)} className={input} />
          </Field>
          <Field label="To">
            <input type="date" value={form.end} min={form.start} onChange={(e) => set("end", e.target.value)} className={input} />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <Field label="Data source">
            <select value={form.source} onChange={(e) => set("source", e.target.value as FormState["source"])} className={input}>
              <option value="auto">Auto (real if available)</option>
              <option value="real">Real (broker history)</option>
              <option value="simulated">Simulated</option>
            </select>
          </Field>
          <Field label="Timeframe">
            <select value={form.timeframe} onChange={(e) => set("timeframe", e.target.value)} className={input}>
              {["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m", "60m"].map((tf) => (
                <option key={tf} value={tf}>
                  {tf === "60m" ? "1 hour" : `${tf.slice(0, -1)} minute`}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <div className="rounded-md bg-ink-850 px-2.5 py-2 text-[11px] leading-relaxed text-ink-300">
          {cov ? (
            <>
              <div>
                Real: <span className="num text-ink-100">{fmtInt(cov.real.candles)}</span> candles
                {cov.real.from && <span className="num"> · {cov.real.from} → {cov.real.to}</span>}
              </div>
              <div>
                Simulated: <span className="num text-ink-100">{fmtInt(cov.simulated.candles)}</span> candles
                {cov.simulated.from && <span className="num"> · {cov.simulated.from} → {cov.simulated.to}</span>}
              </div>
              {range?.from && (
                <button type="button" className="mt-1 text-accent hover:underline" onClick={() => onChange({ ...form, start: range.from!, end: range.to! })}>
                  Use full {effective} range
                </button>
              )}
              {(() => {
                const c = cov[effective === "real" ? "real" : "simulated"];
                return c?.built_from && c.built_from !== form.timeframe ? (
                  <div className="mt-1 text-ink-400">
                    {form.timeframe} isn't stored; it will be built from {c.built_from} candles.
                  </div>
                ) : !c.built_from && c.stored_timeframes?.length ? (
                  <div className="mt-1 text-down">
                    {form.timeframe} can't be built from stored {c.stored_timeframes.join(", ")} candles.
                  </div>
                ) : null;
              })()}
              {!cov.real.candles && (
                <div className="mt-1 text-ink-400">
                  No real history yet — log in to Fyers and run <code className="num text-ink-300">backfill_history</code>.
                </div>
              )}
            </>
          ) : (
            "Checking stored data…"
          )}
        </div>
      </Section>

      <Section title="Option pricing">
        <Segmented
          value={form.pricing}
          options={["synthetic", "real"]}
          labels={{ synthetic: "Synthetic (BS + VIX)", real: `Real candles (${realOptions})` }}
          disabled={{ real: realOptions === 0 }}
          onChange={(v) => set("pricing", v as FormState["pricing"])}
        />
        <p className="text-[11px] leading-relaxed text-ink-400">
          {form.pricing === "synthetic"
            ? "ATM premium priced with Black-Scholes, IV from INDIA VIX. Fast and available everywhere, but optimistic on fills."
            : "Uses stored option candles; contracts without data are skipped, never estimated."}
        </p>
      </Section>

      <Section title="Momentum strategy">
        <div className="grid grid-cols-2 gap-2">
          <Num label="Stop loss %" value={form.stop_loss_pct} onChange={(v) => set("stop_loss_pct", v)} min={1} max={99} />
          <Num label="Target %" value={form.target_pct} onChange={(v) => set("target_pct", v)} min={1} max={500} />
          <Num label="RSI long >" value={form.rsi_long} onChange={(v) => set("rsi_long", v)} min={50} max={90} />
          <Num label="RSI short <" value={form.rsi_short} onChange={(v) => set("rsi_short", v)} min={10} max={50} />
          <Num label="Min ADX" value={form.adx_min} onChange={(v) => set("adx_min", v)} min={0} max={60} />
          <Num label="Max trades / day" value={form.max_trades_per_day} onChange={(v) => set("max_trades_per_day", v)} min={1} max={20} step={1} />
          <Field label="Entries from">
            <input type="time" value={form.entry_start} onChange={(e) => set("entry_start", e.target.value)} className={input} />
          </Field>
          <Field label="Entries until">
            <input type="time" value={form.entry_end} onChange={(e) => set("entry_end", e.target.value)} className={input} />
          </Field>
        </div>
        <label className="flex items-center gap-2 text-ink-300">
          <input type="checkbox" checked={form.use_vwap} onChange={(e) => set("use_vwap", e.target.checked)} className="accent-[var(--color-accent)]" />
          Require futures above / below VWAP
        </label>
      </Section>

      <Section title="Risk & execution">
        <div className="grid grid-cols-2 gap-2">
          <Num label="Lots" value={form.lots} onChange={(v) => set("lots", v)} min={1} max={50} step={1} />
          <Num label="Max daily loss ₹" value={form.max_daily_loss} onChange={(v) => set("max_daily_loss", v)} min={500} step={500} />
          <Num label="Trail after +%" value={form.trail_activate_pct ?? 0} onChange={(v) => set("trail_activate_pct", v || null)} min={0} />
          <Num label="Trail by %" value={form.trail_pct ?? 0} onChange={(v) => set("trail_pct", v || null)} min={0} max={99} />
          <Num label="Spread %" value={form.spread_pct} onChange={(v) => set("spread_pct", v)} min={0} max={10} step={0.05} />
          <Num label="Slippage ticks" value={form.slippage_ticks} onChange={(v) => set("slippage_ticks", v)} min={0} max={20} step={1} />
        </div>
      </Section>

      <button
        type="submit"
        disabled={running}
        className="rounded-md bg-accent px-4 py-2.5 text-sm font-semibold text-ink-950 transition hover:brightness-110 disabled:cursor-wait disabled:opacity-60"
      >
        {running ? "Running backtest…" : "Run backtest"}
      </button>
    </form>
  );
}

const input =
  "num w-full rounded border border-ink-700 bg-ink-850 px-2 py-1.5 text-xs text-ink-100 outline-none focus:border-accent [color-scheme:dark]";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-2.5">
      <legend className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-400">{title}</legend>
      {children}
    </fieldset>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-ink-400">{label}</span>
      {children}
    </label>
  );
}

function Num(p: { label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number }) {
  return (
    <Field label={p.label}>
      <input
        type="number"
        value={p.value}
        min={p.min}
        max={p.max}
        step={p.step ?? "any"}
        onChange={(e) => p.onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        className={input}
      />
    </Field>
  );
}

function Segmented(p: {
  value: string;
  options: string[];
  labels?: Record<string, string>;
  disabled?: Record<string, boolean>;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex rounded-md bg-ink-850 p-0.5">
      {p.options.map((o) => (
        <button
          key={o}
          type="button"
          disabled={p.disabled?.[o]}
          onClick={() => p.onChange(o)}
          className={`flex-1 rounded px-2 py-1.5 font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
            p.value === o ? "bg-ink-700 text-ink-100" : "text-ink-400 hover:text-ink-100"
          }`}
        >
          {p.labels?.[o] ?? o}
        </button>
      ))}
    </div>
  );
}
