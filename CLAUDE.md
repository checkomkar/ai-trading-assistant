# STRIKTEK (formerly NIFTY AI Trader)

Product name **STRIKTEK** (brand lives in `web/src/brand.ts`), tagline "Focus and succeed", planned address `striktek.si`. Code names are unchanged (`backend/trader/`).

Intraday NIFTY/BANKNIFTY **option-buying** research and paper-trading platform. Spec: `requirements.md` (PRD). §16 lists milestones; §17–21 record build notes and current status. Read the relevant PRD section before starting a milestone.

## Status (2026-10-21)
- **Done:**
  - M1: market data platform
  - M3: backtester, running on 1 year of real Fyers data
  - M3b: natural-language/JSON custom strategies
- **M2 (Fyers live data):** works; still needs its market-hours checks (PRD §21.1).
- **M4 (paper trading):** built (PRD §22): PaperBroker, risk engine, live runner, journal, Paper tab, ReplayProvider. Open positions, the day's risk counters and the kill switch survive restarts (§22.5). Still needs the 20-day unattended run.
- **Next: Backtest Engine v2** (PRD §24), phases B1–B9 built one at a time. Stop after each phase for the owner to verify and merge before starting the next.
- **Pending loose ends:**
  - live translator run (needs `ANTHROPIC_API_KEY`)
  - 20-prompt translator eval (§19.7)
  - contract-note cost check (`clearing_pct` in `backend/config/costs.yaml`)

## Stocks (2026-10-22)
Daily-chart, long-only, delivery trading of the NIFTY 50 stocks, next to the intraday option platform. Paper and research only.
- **Spec:** `market: "equity"` (default `"options"`; options hashes are unchanged), `timeframe "1d"`, `underlyings` = stock symbols or `NIFTY50`, only a `long` side with its own entry. Exits are % of the share price, `time_exit_bars` counts days, `risk.max_positions` (equal capital slots) and `risk_per_trade_pct` size positions. Not allowed for stocks: `or_*`, `fut_close`, `vwap`, `vix`, `daily_*` (`semantic_errors`). `volume`/`rel_volume` use the stock's own volume.
- **Data:** `instruments/equities.py` (universe, tokens `NSE:SYM-EQ`, `InstrumentType.EQUITY` = "EQ"), `market_data/equity_daily.py` (`refresh`, `load_daily`, `coverage`). Daily candles are stored with timeframe `1d` stamped 09:15 IST of the day, upserted only like all history. Load with `python -m trader.scripts.backfill_equities --years 5` (needs the daily Fyers login); move to a server with `merge_market_data --equities-only`. The operator can also press "Fetch latest daily candles" on the Stocks page (`POST /equity/refresh`, admin only).
- **Engine:** `backtest/equity.py` (rules: signal on close, fill at next open, stop before target, gap fills at the open, costs on both legs from `equity_costs.py`), `equity_runner.py` (backtest + metrics from the daily mark-to-market curve), routed from `POST /backtests` when the spec is `equity`.
- **Paper:** `backtest/equity_paper.py` + `api/routes/equity.py` + the Stocks page. A portfolio is only a row (`equity_paper`: spec, capital, start day); positions, next-open orders and P&L are the engine re-run from the start day to the latest stored day (cached per latest day), so it needs no runner and survives restarts. Per account, not operator-only. The intraday runner refuses stock strategies.
- **Not built:** stocks outside the NIFTY 50, shorting, intraday stock trading, corporate-action adjustment (a warning says prices are the broker's), automatic daily refresh (manual button / script), live LTP in portfolios (last close only).

## Product direction
Intended to become a hosted multi-user (SaaS) product, paper and research only until the regulatory questions in PRD §23 are settled. Until stage S1 lands (§23.4), keep new code *tenant-ready* without building tenancy: don't add new module-level singletons or process-wide state that belongs to one trader, keep per-account settings out of `os.environ` reads, and put owner-scoped data behind the repository/store layer so an `account_id` can be added later.

## SaaS layer (2026-10-21; details in `docs/pricing-and-saas.md`)
- `AUTH_ENABLED=false` (default) = local single user, owner `"local"`, no limits. `true` = cookie sessions, per-account data, plans.
- Every owned row carries `owner` (= `accounts.id`); routes take it from `CurrentUser` (`api/deps.py`), never from a request field. Market data needs a login; **paper and admin routes are operator-only** until per-account runners exist.
- Metering: `billing/meter.py` (`Meter.metered` for model calls, `counted` for backtests/speech, `check_stock` for stock limits). 402 `quota_exceeded` is the UI's upgrade prompt. Plans and the AI cost ceiling: `billing/plans.py` (a test enforces the margin). Payments: `billing/razorpay.py`; access is granted only by the signed webhook.
- Live data when accounts are on: each customer gets their own in-memory feed from the Fyers keys and daily sign-in saved on the Brokers page (`market_data/feeds.py`, `FeedManager`); it never writes to the shared database and needs nothing in `.env`. No broker connected = 409 `broker_required` (the UI shows a connect card). Routes get the feed through `get_user_md` / `resolve_md`, never `app.state.md` directly (that is the shared operator feed, used in local mode and as the operator's fallback).
- New AI call sites must go through `meter.metered(...)`; new owned tables need an `owner` column and repo-level filtering.

## Non-negotiables
- **Safety:** the default path is DATA → ANALYSIS → PAPER. No code path may let LLM output place orders (PRD §2). Risk limits are enforced by the engine, not by strategies.
- **No look-ahead:** signals form on a bar's close and fill at the next bar's open. If one bar touches both stop and target, assume the stop hit.
- **Contract specs from the broker:** lot size, tick size and expiries come from the broker symbol master. `instruments/specs.py` is a simulator fallback only.
- **Never mix data sources:** real (`NSE:` tokens) and simulated (`SIM:` tokens) data stay separate. Reports always state the data source and the option-pricing mode.
- **Paper trading:** orders come only from the runner, through `RiskEngine.check`. There is no order endpoint, and `trader/ai` must never import `execution`, `risk` or `brokers` (both enforced by tests). Entries are blocked when the market is closed. Replay data is never persisted and always labelled (`REPLAY:` tokens, synthetic options).
- **Restart state:** positions, same-day counters and the kill switch are snapshotted to `paper_state` (one row per data source) and restored at API start-up; a snapshot from an earlier day is flagged (`state.stale`), never resumed, and the kill switch is never cleared by a restart. A restored position with no lane to manage it refuses the runner start. Replay does not persist.
- **Stored market history is never deleted:** candles, option snapshots, ticks, instruments and fetched expired-option data are only upserted (candles by instrument + timeframe + time; instruments by token). No code, migration or script may `DELETE`/`TRUNCATE`/`DROP` them (`tests/test_market_data_is_never_deleted.py` scans for it). Never run `docker compose down -v` on a host that holds the data volume.
- **Credentials:** live only in `.env` (git-ignored), never in `.env.example`.

## Layout
- `backend/trader/`: Python 3.10 package
  - `config.py`, `logging.py`, `domain.py`, `features.py` (FeatureEngine shared by backtest and live)
  - `market_data/`: provider base, simulator, replay, collector service, candles, resample, quality, bus
  - `brokers/fyers/`: auth/login CLI, symbol master, socket provider, expired-contract data (`expired`, `expired_bulk` planner)
  - `instruments/`: calendar/holidays, expiry, strikes, registry
  - `options/`: Black-Scholes, chain analytics
  - `indicators/`: incremental indicators
  - `strategy/`: base, momentum, spec (Strategy Spec v1) and rule_strategy (interpreter), `sizing.py` (risk-based lots)
    - Spec operands beyond the basics: candlestick flags `cs_*` and RSI divergence (0/1, use `> 0`), `swing_high`/`swing_low` (dynamic support/resistance), `volume`/`rel_volume` (futures volume; no futures data = unknown = no trade), `daily_ema`/`daily_sma`/`daily_rsi` (completed days only; context, the trading timeframe stays 1m-60m, no overnight holds).
    - Extra indicators (`indicators/extra.py`, operands in `strategy/extra_operands.py`) follow TradingView's names and defaults; `mult` is the optional second setting (Supertrend factor, Keltner/Bollinger multiplier).
    - Multiple timeframes: any price/indicator operand takes `tf` (3m, 9m, 15m ...), read on candles built from the strategy's own bars (`strategy/htf.py`), completed candles only, so no look-ahead; `shift` then counts those candles. `tf` must be a whole multiple of the base timeframe; session/daily operands (vwap, or_*, prev_day_*, daily_*, pivots) cannot take one. Intraday strategies only.
    - `risk.risk_per_trade_pct` sizes lots so the stop loses that % of capital (backtest `capital`, paper `starting_cash`; a single lot over budget = trade skipped); `risk.min_reward_risk` rejects specs whose target/stop is lower. Measured-move targets are not built: use exit conditions on `swing_*`.
  - `execution/`: `Broker` interface, `PaperBroker`, `runner` (live strategy runner), `store` (audit + journal + state row), `state` (snapshot/restore policy), `service`, `summary`, `rehearsal` (in-process replay driver)
  - `risk/`: deterministic `RiskEngine` (pre-order checklist, kill switch). The only gate to the broker.
  - `backtest/`: costs, pricing (synthetic/real), engine, metrics, data loader, runner; stocks: `equity`, `equity_costs`, `equity_runner`, `equity_paper`
  - `ai/strategy_translator.py`: Claude, translation only
  - `api/`: FastAPI routes (market, options, backtests, strategies, paper, health, ws)
  - `db/`: models, migrate, repository
  - `scripts/`: backfill_history, backfill_equities, merge_market_data, fetch_expired_options, fetch_expired_bulk, generate_sim_history, backtest, import_candles, paper_rehearsal, fetch_prime_history (multi-year download in order), archive_market_data (gzip CSV export/verify/restore)
- `backend/alembic/`: migrations, run automatically at API start-up
- `backend/config/`: `nse_holidays.yaml`, `costs.yaml`
- `web/`: Vite + React + TS + Tailwind + lightweight-charts 4.2. Pages: Market, Paper, Stocks, Strategies, Assistant, Brokers (backtests are run from Strategies).

## Commands
Run from `backend/` unless noted.
- Tests:
  ```bash
  .venv/Scripts/python -m pytest -q
  ```
  About 430 tests, about 2 min (the replay tests run full days); all must pass.
- API (port 8000):
  ```bash
  .venv/Scripts/uvicorn trader.api.main:app
  ```
  In the desktop app, use `preview_start` with name `api`.
- Frontend (port 5173, `/api` proxied), from `web/`:
  ```bash
  npm run dev
  ```
  Use `preview_start` with name `web`. Type-check with `npx tsc -b`.
- Rehearse stored days through the paper pipeline (replay tokens, synthetic option quotes, journal rows tagged `replay`; then compares with a backtest of the same days):
  ```bash
  .venv/Scripts/python -m trader.scripts.paper_rehearsal --underlying NIFTY --start 2026-10-12 --end 2026-10-19
  ```
  For the dashboard, copy `backend/data/trader.db` to `backend/data/rehearsal.db` and use `preview_start` with names `paper-replay-api` and `paper-replay-web` (ports 8001/5174; edit `REPLAY_DATE` in `.claude/launch.json`). Never point replay at the live DB while the collector runs.
- Daily Fyers login (the user signs in themselves; tokens expire at 06:00 IST):
  ```bash
  .venv/Scripts/python -m trader.brokers.fyers.login
  ```
- History backfill (resumable):
  ```bash
  .venv/Scripts/python -m trader.scripts.backfill_history --days 365 --timeframes 1m
  ```
- Fyers Prime, everything in order (index/VIX/futures 1m back N years, holidays, expired options, NIFTY 50 daily, archive); resumable, `--dry-run` first:
  ```bash
  .venv/Scripts/python -m trader.scripts.fetch_prime_history --years 7
  ```
- Archive of broker data as gzip CSV + checksummed manifest (`export` only rewrites changed months; `restore` only adds missing rows):
  ```bash
  .venv/Scripts/python -m trader.scripts.archive_market_data export --to D:/striktek-archive
  ```
- Backtest CLI:
  ```bash
  .venv/Scripts/python -m trader.scripts.backtest --underlying NIFTY --source real --start 2025-10-23 --end 2026-10-19
  ```

## Conventions and gotchas
- Store 1m candles only; higher timeframes are built exactly by `market_data/resample.py`, anchored at 09:15.
- Timestamps are UTC in the DB; SQLite returns them naive, so treat them as UTC. Convert to IST only at the edges.
- Fyers plan limits:
  - Standard (free): about 1 year of intraday history, 100 socket symbols, 10 expired-option intraday calls a day.
  - Prime: 5+ years intraday, expired F&O daily 7+ years (expired intraday depth not published: `fetch_prime_history` finds it). `fetch_prime_history` lifts the depth clamp for its own run only.
  - Clamp requests rather than letting them fail.
- Index candles have no usable volume, so VWAP comes from futures (`fut_close` vs `vwap`).
- The Bash tool's heredocs break on some quoting. For long text edits, write a Python script to the scratchpad and run it.
- Charts: validated colours are up `#22c55e`, down `#f43f5e`, EMA fast `#3987e5` and EMA slow `#d95926`.
- Verify UI changes in the browser only when the UI changed; otherwise tests are enough.
