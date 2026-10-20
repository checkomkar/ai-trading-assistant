export interface Quote {
  symbol: string;
  token: string;
  ltp: number;
  change: number | null;
  change_pct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  prev_close: number | null;
  ts: string;
}

export interface FeedStatus {
  connected: boolean;
  ticks_per_second: number;
  reconnects: number;
  stale: number;
  error: string | null;
}

export interface MarketMessage {
  type: "market";
  server_time: string;
  session: string;
  simulated: boolean;
  provider: string;
  feed: FeedStatus;
  quotes: Record<string, Quote | null>;
}

export interface CandleData {
  ts: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closed: boolean;
}

export interface CandleMessage {
  type: "candle";
  symbol: string;
  timeframe: string;
  candle: CandleData;
}

export interface OptionLeg {
  token: string;
  symbol: string;
  ltp: number | null;
  bid: number | null;
  ask: number | null;
  volume: number | null;
  oi: number | null;
  oi_change: number | null;
  iv: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface ChainRow {
  strike: number;
  ce: OptionLeg | null;
  pe: OptionLeg | null;
}

export interface ChainSummary {
  total_call_oi: number;
  total_put_oi: number;
  total_call_oi_change: number;
  total_put_oi_change: number;
  pcr: number | null;
  max_call_oi_strike: number | null;
  max_put_oi_strike: number | null;
  max_call_oi_change_strike: number | null;
  max_put_oi_change_strike: number | null;
  atm_iv: number | null;
}

export interface OptionChain {
  underlying: string;
  expiry: string;
  spot: number | null;
  atm_strike: number | null;
  timestamp: string;
  rows: ChainRow[];
  summary: ChainSummary;
}

export interface Expiries {
  underlying: string;
  listed: string[];
  cycle: "WEEKLY" | "MONTHLY";
  lot_size: number;
  strike_interval: number;
}

export interface FeedHealth {
  provider: string;
  simulated: boolean;
  connected: boolean;
  reconnects: number;
  last_error: string | null;
  ticks_received: number;
  ticks_per_second: number;
  subscribed: number;
  last_tick_age_seconds: Record<string, number | null>;
  session: string;
  writes: { ticks: number; candles: number; option_snapshots: number; errors: number };
}

export interface DataHealth {
  accepted: number;
  rejected: number;
  issues: Record<string, number>;
  stale_symbols: string[];
}

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}: ${path}`);
  return res.json() as Promise<T>;
}

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${location.host}${path}`;
}

// ---- backtests (M3) ---------------------------------------------------------------
export interface BacktestTrade {
  trade_id: string;
  correlation_id: string;
  contract: string;
  direction: "LONG" | "SHORT";
  signal_time: string;
  signal_reason: string;
  confidence: number;
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_bar_time?: string;
  exit_price: number;
  exit_reason: string;
  quantity: number;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  mae_pct: number;
  mfe_pct: number;
  minutes_held: number;
  underlying_entry: number;
  underlying_exit: number;
}

export interface BacktestMetrics {
  trades: number;
  total_net_pnl: number | null;
  total_gross_pnl: number | null;
  total_charges: number | null;
  win_rate: number | null;
  profit_factor: number | null;
  average_win: number | null;
  average_loss: number | null;
  expectancy: number | null;
  max_drawdown: number | null;
  max_drawdown_pct: number | null;
  sharpe: number | null;
  sortino: number | null;
  return_pct: number | null;
  average_holding_minutes: number | null;
  average_mae_pct: number | null;
  average_mfe_pct: number | null;
  exit_reasons: Record<string, number>;
  by_direction: Record<string, { trades: number; win_rate: number | null; net_pnl: number | null }>;
  trading_days: number;
  days_traded: number;
  daily_pnl: { date: string; pnl: number }[];
  monthly_pnl: { month: string; pnl: number }[];
  equity_curve: { ts: string | null; equity: number; drawdown?: number }[];
}

export interface BacktestResult {
  id: string;
  underlying: string;
  start: string;
  end: string;
  timeframe: string;
  strategy: string;
  data_source: "real" | "simulated";
  built_from?: string | null;
  pricing_mode: "synthetic" | "real";
  bars: number;
  days: number;
  signals: number;
  no_trade_reasons: Record<string, number>;
  skipped_entries: Record<string, number>;
  warnings: string[];
  metrics: BacktestMetrics;
  trades: BacktestTrade[];
  params: Record<string, Record<string, unknown>>;
}

export interface BacktestRunSummary {
  id: string;
  created_at: string;
  underlying: string;
  start: string;
  end: string;
  data_source: string;
  pricing_mode: string;
  trades: number;
  total_net_pnl: number | null;
  win_rate: number | null;
  profit_factor: number | null;
}

export interface Coverage {
  real: { candles: number; from: string | null; to: string | null; option_contracts_with_candles: number;
          stored_timeframes: string[]; built_from: string | null };
  simulated: { candles: number; from: string | null; to: string | null; stored_timeframes: string[]; built_from: string | null };
}

export async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = (data as { detail?: unknown }).detail;
    const msg = typeof detail === "string" ? detail : Array.isArray(detail)
      ? detail.map((d: { msg?: string; loc?: string[] }) => `${d.loc?.slice(-1)[0] ?? ""}: ${d.msg ?? ""}`).join("; ")
      : `${res.status} ${res.statusText}`;
    throw new Error(msg);
  }
  return data as T;
}

// ---- custom strategies (M3b) -------------------------------------------------------
export interface SavedStrategy {
  id: string;
  name: string;
  version: number;
  spec: Record<string, unknown>;
  spec_hash: string;
  english: string;
  source_text: string | null;
  assumptions: string[];
  translator_model: string | null;
  created_at: string;
}

export interface Validation {
  valid: boolean;
  errors: string[];
  english: string | null;
  spec_hash: string | null;
}

export interface Translation {
  spec: Record<string, unknown> | null;
  english: string | null;
  assumptions: string[];
  questions: string[];
  unsupported: string[];
  errors: string[];
  model: string;
  repaired: boolean;
}
