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

/** The plan said no (HTTP 402): which allowance ran out. The UI turns it into an upgrade prompt. */
export interface QuotaInfo {
  code: "quota_exceeded";
  resource: string;
  plan: string;
  limit: number | null;
  used: number;
  message: string;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly quota?: QuotaInfo) {
    super(message);
  }
}

/** One place that turns a failed response into an Error, and tells the app about sign-in and quota problems. */
function failure(res: Response, data: unknown, path = ""): ApiError {
  const detail = (data as { detail?: unknown } | null)?.detail;
  let msg = `${res.status} ${res.statusText}${path ? `: ${path}` : ""}`;
  let quota: QuotaInfo | undefined;
  if (typeof detail === "string") msg = detail;
  else if (Array.isArray(detail)) msg = detail.map((d: { msg?: string; loc?: string[] }) => `${d.loc?.slice(-1)[0] ?? ""}: ${d.msg ?? ""}`).join("; ");
  else if (detail && typeof detail === "object" && (detail as QuotaInfo).code === "quota_exceeded") {
    quota = detail as QuotaInfo;
    msg = quota.message;
  }
  if (res.status === 401) window.dispatchEvent(new Event("striktek:unauth"));
  if (quota) window.dispatchEvent(new CustomEvent("striktek:quota", { detail: quota }));
  return new ApiError(msg, res.status, quota);
}

/** Something that may have used allowance finished: the header's usage meter re-reads. */
const usageChanged = () => window.dispatchEvent(new Event("striktek:usage"));

export async function getJSON<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw failure(res, await res.json().catch(() => ({})), path);
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
  if (!res.ok) throw failure(res, data);
  usageChanged();
  return data as T;
}

export async function postAudio<T>(path: string, audio: Blob): Promise<T> {
  const res = await fetch(`/api${path}`, { method: "POST", headers: { "Content-Type": audio.type || "audio/webm" }, body: audio });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw failure(res, data);
  usageChanged();
  return data as T;
}

export interface TranslatorInfo {
  configured: boolean;
  // the operator sees the provider chain; customers only learn whether translation is available
  provider?: string;
  model?: string;
  fallbacks?: string[];
  note?: string | null;
}

export interface KnowledgeDoc {
  id: string;
  filename: string;
  kind: string;
  size_bytes: number;
  status: "ready" | "no_text" | "no_vision";
  summary: string;
  chars: number;
}

/** Send one file's bytes; the server extracts its text (PDF, Word, Excel, text, or an image via a vision model). */
export async function uploadFile(file: File, save = true): Promise<KnowledgeDoc> {
  const res = await fetch(`/api/knowledge/upload?filename=${encodeURIComponent(file.name)}&save=${save}`, {
    method: "POST",
    headers: { "Content-Type": file.type || "application/octet-stream" },
    body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw failure(res, data);
  usageChanged();
  return data as KnowledgeDoc;
}

export async function del(path: string): Promise<void> {
  const res = await fetch(`/api${path}`, { method: "DELETE" });
  if (!res.ok) throw failure(res, await res.json().catch(() => ({})));
}

// ---- custom strategies (M3b) -------------------------------------------------------
export interface StrategyFolder {
  id: string;
  name: string;
  position: number;
  count: number;
}

export async function sendJSON<T>(method: "PATCH" | "PUT", path: string, body: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw failure(res, data);
  return data as T;
}

export interface SavedStrategy {
  id: string;
  folder_id?: string | null;
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

// ---- paper trading (M4) -------------------------------------------------------------
export interface PaperDecision {
  kind: "signal" | "no_trade" | "none" | "disabled";
  ts: string;
  reason?: string;
  correlation_id?: string;
  direction?: "LONG" | "SHORT";
  confidence?: number;
  strategy?: string;
  option?: string;
  entry?: number;
  stop?: number;
  target?: number;
  outcome?: "pending" | "ordered" | "filled" | "skipped" | "risk_rejected" | "order_rejected";
  detail?: string;
}

export interface PaperLane {
  underlying: string;
  strategy: string;
  timeframe: string;
  enabled: boolean;
  warmed_bars: number;
  last_bar: string | null;
  last_decision: PaperDecision | null;
  signals: PaperDecision[];
}

export interface RiskStatus {
  kill_switch: { active: boolean; reason: string; since: string | null };
  disabled_strategies: string[];
  day: string | null;
  limits: {
    max_daily_loss: number;
    max_consecutive_losses: number;
    max_trades_per_day: number;
    max_open_positions: number;
    max_lots_per_trade: number;
    max_risk_per_trade: number;
    max_premium_exposure: number;
    max_underlying_exposure: number;
    max_data_age_seconds: number;
  };
  usage: {
    day_pnl: number;
    trades_today: number;
    consecutive_losses: number;
    open_positions: number;
    premium_exposure: number;
    underlying_exposure: Record<string, number>;
  };
}

export interface PaperStatus {
  running: boolean;
  now: string;
  broker: string;
  data_source: string;
  option_pricing: string;
  cash: number;
  starting_cash: number;
  realized_pnl: number;
  unrealized_pnl: number;
  errors: number;
  last_error: string | null;
  lanes: PaperLane[];
  risk: RiskStatus;
}

export interface PaperPosition {
  correlation_id: string;
  state: "ENTRY" | "OPEN" | "EXIT";
  underlying: string;
  symbol: string;
  strategy: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  entry_price: number | null;
  current_price: number | null;
  stop: number | null;
  target: number | null;
  unrealized_pnl: number | null;
  minutes_held: number;
  exit_reason: string | null;
  entry_time: string | null;
}

export interface PaperTrade {
  id: number;
  correlation_id: string;
  trading_day: string;
  underlying: string;
  symbol: string;
  strategy: string;
  direction: "LONG" | "SHORT";
  quantity: number;
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  exit_reason: string;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  mae_pct: number | null;
  mfe_pct: number | null;
  minutes_held: number | null;
  signal_reason: string | null;
  confidence: number | null;
  data_source: string;
  option_pricing: string;
}

export interface PaperSummary {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  max_drawdown: number;
  profit_factor: number | null;
  by_strategy: Record<string, { trades: number; wins: number; losses: number; net_pnl: number }>;
  by_exit_reason: Record<string, number>;
}

export interface PaperEvent {
  ts: string;
  event_type: string;
  correlation_id: string | null;
  payload: Record<string, unknown>;
}

// ---- accounts and billing (SaaS) ---------------------------------------------------
export interface Me {
  signed_in: boolean;
  auth_enabled: boolean;
  id?: string;
  email?: string;
  name?: string;
  is_admin?: boolean;
  plan?: string;
  signup_open?: boolean;
  invite_required?: boolean;
}

export interface UsageLine {
  used: number;
  limit: number | null;
  remaining: number | null;
}

export interface Usage {
  plan: { id: string; name: string; price_inr: number; lifetime: boolean };
  period: { start: string | null; end: string | null };
  usage: Record<"generations" | "chat_messages" | "speech_requests" | "backtests" | "strategies" | "docs", UsageLine>;
  ai_allowance_pct: number | null;
  renews: string | null;
  subscription: boolean;
}

export interface PlanInfo {
  id: string;
  name: string;
  price_inr: number;
  tagline: string;
  lifetime: boolean;
  features: string[];
  buyable: boolean;
  limits: Record<string, number | null>;
}

export interface Plans {
  plans: PlanInfo[];
  checkout_enabled: boolean;
  gst_included: boolean;
  note: string;
}
