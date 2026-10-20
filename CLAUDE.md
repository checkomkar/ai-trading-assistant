# NIFTY AI Trader

Intraday NIFTY/BANKNIFTY **option-buying** research and paper-trading platform. Spec: `requirements.md` (PRD). §16 lists milestones; §17–21 record build notes and current status. Read the relevant PRD section before starting a milestone.

## Status (2026-10-21)
- **Done:**
  - M1: market data platform
  - M3: backtester, running on 1 year of real Fyers data
  - M3b: natural-language/JSON custom strategies
- **M2 (Fyers live data):** works; still needs its market-hours checks (PRD §21.1).
- **M4 (paper trading):** built (PRD §22): PaperBroker, risk engine, live runner, journal, Paper tab, ReplayProvider. Still needs the 20-day unattended run, and persistence of open positions and the kill switch across restarts (§22.3).
- **Pending loose ends:**
  - live translator run (needs `ANTHROPIC_API_KEY`)
  - 20-prompt translator eval (§19.7)
  - contract-note cost check (`clearing_pct` in `backend/config/costs.yaml`)

## Non-negotiables
- **Safety:** the default path is DATA → ANALYSIS → PAPER. No code path may let LLM output place orders (PRD §2). Risk limits are enforced by the engine, not by strategies.
- **No look-ahead:** signals form on a bar's close and fill at the next bar's open. If one bar touches both stop and target, assume the stop hit.
- **Contract specs from the broker:** lot size, tick size and expiries come from the broker symbol master. `instruments/specs.py` is a simulator fallback only.
- **Never mix data sources:** real (`NSE:` tokens) and simulated (`SIM:` tokens) data stay separate. Reports always state the data source and the option-pricing mode.
- **Paper trading:** orders come only from the runner, through `RiskEngine.check`. There is no order endpoint, and `trader/ai` must never import `execution`, `risk` or `brokers` (both enforced by tests). Entries are blocked when the market is closed. Replay data is never persisted and always labelled (`REPLAY:` tokens, synthetic options).
- **Credentials:** live only in `.env` (git-ignored), never in `.env.example`.

## Layout
- `backend/trader/`: Python 3.10 package
  - `config.py`, `logging.py`, `domain.py`, `features.py` (FeatureEngine shared by backtest and live)
  - `market_data/`: provider base, simulator, replay, collector service, candles, resample, quality, bus
  - `brokers/fyers/`: auth/login CLI, symbol master, socket provider, expired-contract data
  - `instruments/`: calendar/holidays, expiry, strikes, registry
  - `options/`: Black-Scholes, chain analytics
  - `indicators/`: incremental indicators
  - `strategy/`: base, momentum, spec (Strategy Spec v1) and rule_strategy (interpreter)
  - `execution/`: `Broker` interface, `PaperBroker`, `runner` (live strategy runner), `store` (audit + journal), `summary`, `rehearsal` (in-process replay driver)
  - `risk/`: deterministic `RiskEngine` (pre-order checklist, kill switch). The only gate to the broker.
  - `backtest/`: costs, pricing (synthetic/real), engine, metrics, data loader, runner
  - `ai/strategy_translator.py`: Claude, translation only
  - `api/`: FastAPI routes (market, options, backtests, strategies, paper, health, ws)
  - `db/`: models, migrate, repository
  - `scripts/`: backfill_history, fetch_expired_options, generate_sim_history, backtest, import_candles, paper_rehearsal
- `backend/alembic/`: migrations, run automatically at API start-up
- `backend/config/`: `nse_holidays.yaml`, `costs.yaml`
- `web/`: Vite + React + TS + Tailwind + lightweight-charts 4.2. Pages: Market, Paper, Strategies, Backtest.

## Commands
Run from `backend/` unless noted.
- Tests:
  ```bash
  .venv/Scripts/python -m pytest -q
  ```
  159 tests, about 1 min (the replay tests run full days); all must pass.
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
- Backtest CLI:
  ```bash
  .venv/Scripts/python -m trader.scripts.backtest --underlying NIFTY --source real --start 2025-10-23 --end 2026-10-19
  ```

## Conventions and gotchas
- Store 1m candles only; higher timeframes are built exactly by `market_data/resample.py`, anchored at 09:15.
- Timestamps are UTC in the DB; SQLite returns them naive, so treat them as UTC. Convert to IST only at the edges.
- Fyers plan limits:
  - Standard (free): about 1 year of intraday history, 100 socket symbols, 10 expired-option intraday calls a day.
  - Clamp requests rather than letting them fail.
- Index candles have no usable volume, so VWAP comes from futures (`fut_close` vs `vwap`).
- The Bash tool's heredocs break on some quoting. For long text edits, write a Python script to the scratchpad and run it.
- Charts: validated colours are up `#22c55e`, down `#f43f5e`, EMA fast `#3987e5` and EMA slow `#d95926`.
- Verify UI changes in the browser only when the UI changed; otherwise tests are enough.
