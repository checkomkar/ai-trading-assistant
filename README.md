# NIFTY AI Trader

A quantitative research and paper-trading platform for NIFTY and BANKNIFTY index options. The full product spec is [requirements.md](requirements.md). §14 covers implementation decisions, §15 the broker choice, and §16 the milestone roadmap.

**Current status:** M1 (market data platform) is done. M2 (live data via Fyers) is built and awaiting a first live session. M3 (backtesting) runs on a year of real Fyers data. M3b (natural-language strategies) is built. No orders are placed anywhere in the system.

```
provider (simulated | fyers)   ─► data-quality checks ─► quote cache ─► candles (1m/5m)
                                                       │                 │
                                                       ▼                 ▼
                                             WebSocket fan-out     SQLite / PostgreSQL
                                                       │      (ticks, candles, chain snapshots, audit events)
                                                       ▼
                                             React dashboard
```

## Quick start (no Docker needed)

Prerequisites: Python 3.10+ and Node 20+.

```bash
cp .env.example .env
```

Backend (from `backend/`):

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/uvicorn trader.api.main:app --reload
```

On macOS/Linux use `.venv/bin/...`. The API runs at <http://localhost:8000>, with interactive docs at `/docs`.

Frontend (from `web/`):

```bash
npm install
npm run dev
```

Then open <http://localhost:5173>.

The simulator streams outside market hours too, and the dashboard shows a **SIMULATED DATA** badge. On an empty database it backfills one session of synthetic candles for the `SIM:*` instruments, so charts aren't blank.

### With PostgreSQL

```bash
docker compose up -d
```

Then set `DATABASE_URL=postgresql+asyncpg://trader:trader@localhost:5432/trader` in `.env` and, from `backend/`, run:

```bash
.venv/Scripts/alembic upgrade head
```

On SQLite the schema is created automatically; on Postgres it is managed by Alembic.

## Live data with Fyers (M2)

1. **Create the app.** At <https://myapi.fyers.in>, create an *individual* app. Set its redirect URL to exactly `http://127.0.0.1:8765/fyers/callback`.
2. **Configure `.env`.** Set `MARKET_DATA_PROVIDER=fyers`, plus `FYERS_CLIENT_ID` (the App ID, e.g. `XXXXXXXX-100`) and `FYERS_SECRET_KEY`.
3. **Log in each trading day**, before starting the API. From `backend/`:

   ```bash
   .venv/Scripts/python -m trader.brokers.fyers.login
   ```

   Your browser opens the Fyers login, where you sign in. The token is captured and cached in `backend/data/fyers_token.json` (git-ignored), and stays valid until 06:30 IST the next day.
4. **Start the API.** It downloads the day's symbol master (about 15 s the first time) and seeds quotes over REST. It gap-fills the last `HISTORY_BACKFILL_DAYS` of candles, then streams indices, near futures and an ATM ±10 option window. The window recentres as spot moves and stays within the plan's 100-symbol cap.

Deeper history, from `backend/`. This resumes from the last stored candle:

```bash
.venv/Scripts/python -m trader.scripts.backfill_history --days 180
```

Expired option contracts, for backtests. The Standard plan allows 10 intraday calls a day, so re-run daily and it resumes. Pass `--prime` if you subscribe to Fyers Prime.

```bash
.venv/Scripts/python -m trader.scripts.fetch_expired_options --underlying NIFTY --expiry 2025-03-27 --monthly --atm 23500 --width 5
```

## Backtesting (M3)

Backtests run on stored candles. With Fyers set up, fetch about a year of real **1-minute** history first; that's the maximum on the free plan. From `backend/`:

```bash
.venv/Scripts/python -m trader.scripts.backfill_history --days 365 --timeframes 1m
```

Every higher timeframe (2, 3, 5, 10, 15, 20, 30 and 60 minutes) is then built exactly from the 1-minute candles, anchored at 09:15 like NSE charts. Lower timeframes can't be derived from higher ones.

Without broker data, you can generate clearly-labelled **simulated** history to try the tool:

```bash
.venv/Scripts/python -m trader.scripts.generate_sim_history --days 120
```

Then use the **Backtest** tab, which shows a candlestick chart with every entry and exit, or the CLI:

```bash
.venv/Scripts/python -m trader.scripts.backtest --underlying NIFTY --start 2025-11-01 --end 2026-10-17
```

How the engine works:
- Signals form on a bar's close and fill at the next bar's open.
- Buys pay the modelled ask and sells receive the bid.
- If one bar touches both the stop and the target, the stop is assumed hit first.
- Positions are flat by 15:15.
- Charges come from `backend/config/costs.yaml`.

Option prices are **synthetic** by default: Black-Scholes, with INDIA VIX as the IV proxy. They are **real** when stored option candles exist. Every report states its data source and pricing mode.

## Custom strategies (M3b)

The **Strategies** tab turns a plain-English description into rules you can backtest:

1. **Describe** the strategy, or start from the Momentum #1 template.
2. **Translate:** Claude converts the description into a Strategy Spec. It lists the assumptions it made and asks questions when something is ambiguous. It never runs or trades anything.
3. **Review:** check the **What will run** summary. Code generates it from the rules, so it's exactly what executes.
4. **Edit:** change the rules as JSON, which is validated as you type, or refine them in plain English.
5. **Save & backtest:** every save is an immutable version, and every backtest records which version ran.

Translation needs `ANTHROPIC_API_KEY` in `.env`. Without it, the template and JSON editing still work.

## Tests

From `backend/`:

```bash
.venv/Scripts/python -m pytest
```

The tests cover:
- NSE session states
- weekly and monthly expiry resolution, including holiday roll-back
- ATM and strike resolvers
- Black-Scholes pricing, Greeks and the IV solver
- data-quality rules
- candle aggregation
- simulator determinism
- reconnect behaviour
- the importer
- every REST and WebSocket endpoint
- the Fyers adapter: auth, symbol master, socket bridge and reconnect, subscription cap, history, expired contracts
- schema migrations
- indicators vs. reference implementations, cost schedule, expiry rules, every engine fill/exit rule, metrics, backtest API

## Testing paper trading and restart safety locally

Run these from `backend/` with the venv active. The first two need no market hours or broker login.

1. **Automated (covers the mid-trade restart).** `python -m pytest -q tests/test_paper_persistence.py` replays a full day, "crashes" with a trade open, restarts over the same database and checks the trade still exits and is journalled once. It also covers the kill switch, stale-day state and stop/start. The whole suite is `python -m pytest -q` (about 2 minutes).
2. **Kill switch survives a restart (manual).**
   1. Start the API (`uvicorn trader.api.main:app`) and the web app (`npm run dev` in `web/`), open the Paper tab.
   2. Press the KILL SWITCH button, or `curl -X POST localhost:8000/paper/kill -H "content-type: application/json" -d "{\"reason\":\"restart test\"}"`.
   3. Stop the API with Ctrl+C and start it again.
   4. `curl localhost:8000/paper/status`: `risk.kill_switch.active` must be `true` with your reason, and `restore.status` must be `restored`.
   5. Clear it with `POST /paper/kill/reset`, restart once more, and confirm it stays off.
3. **A rehearsal of stored days** (needs backfilled history): `python -m trader.scripts.paper_rehearsal --underlying NIFTY --start 2026-10-12 --end 2026-10-19`. Replay never saves or restores state, by design.
4. **Open position across a restart (manual, market hours only).** Entries are refused outside 09:15-15:30 IST even on the simulator, so this can only be done in a live session. Set `PAPER_AUTOSTART=true`, run with `MARKET_DATA_PROVIDER=simulated` (or `fyers`), wait for `GET /paper/positions` to show an OPEN position, then Ctrl+C and restart the API. Expect `restore.status: restored`, the same position (same `correlation_id`, stop and target) in `/paper/positions`, a `position.restored` row in `/paper/events`, and later a journal row under that same id. The simulator may not produce a signal on a given day, in which case rely on step 1.
5. **Stale state.** Restart the API on a later trading day than the one that saved a snapshot: `restore.status` is `stale`, the abandoned positions are listed, nothing is resumed.

## API

| Endpoint | Purpose |
|---|---|
| `GET /health`, `/health/feed`, `/health/data` | Service, feed and data-quality health |
| `GET /market/overview` | NIFTY, BANKNIFTY, INDIA VIX quotes and session state |
| `GET /market/{nifty\|banknifty\|indiavix}` | Single quote |
| `GET /market/quotes?symbols=NIFTY,BANKNIFTY` | Several quotes |
| `GET /market/candles?symbol=NIFTY&timeframe=5m` | History plus the live candle (`NIFTY-FUT` for futures with volume) |
| `GET /options/{u}/expiries` | Listed and computed expiries, lot size, cycle |
| `GET /options/{u}/chain?expiry=&strikes=10` | Chain with IV, Greeks, OI, ΔOI, PCR, max-OI strikes |
| `GET /instruments/resolve?underlying=NIFTY&option_type=CE&offset=1&expiry=nearest` | Option resolver (PRD §3.4) |
| `WS /ws/market` | Quotes and feed status every 500 ms, plus the live candle for a subscribed symbol |
| `WS /ws/options` | Chain every 2 s for `{underlying, expiry, strikes}` |
| `WS /ws/signals` | Reserved for Phase 7 (sends heartbeats only) |

## Importing history

From `backend/`:

```bash
.venv/Scripts/python -m trader.scripts.import_candles nifty_5m.csv --symbol NIFTY --timeframe 5m
```

The importer accepts CSV or JSON. Naive timestamps are read as IST, and re-importing the same file is idempotent.

## Layout

```
backend/trader/
  config.py            settings; LIVE mode needs a second explicit confirmation
  logging.py           structured JSON logs
  domain.py            Instrument, Tick
  instruments/         NSE calendar, sessions, expiry/ATM/strike resolvers, registry
  options/             Black-Scholes, IV, Greeks, chain analytics
  market_data/         provider interface, simulator, quality, candles, bus, collector + subscription manager
  brokers/fyers/       auth + login CLI, symbol master, data socket provider, expired-contract data
  db/                  SQLAlchemy models + repository
  api/                 FastAPI app, REST + WebSocket routes
  indicators/          incremental EMA/RSI/ATR/ADX/MACD/VWAP/...
  features.py          feature engine shared by backtest and (later) live
  strategy/            Strategy interface, Signal, Momentum strategy #1
  backtest/            costs, option pricing, event-driven engine, metrics, data loader, runner
  scripts/             importer, history backfill, expired options, sim history, backtest CLI
backend/config/nse_holidays.yaml   fill in from the NSE holiday circular
backend/alembic/                   migrations
web/                               Vite + React + TS + Tailwind + Lightweight Charts
```

## Before using real data

1. **Holidays:** complete `backend/config/nse_holidays.yaml` from the NSE circular. Two 2026 holidays are already inferred from the exchange's own expiry dates. On start-up the Fyers adapter warns if the exchange's expiries disagree with the calendar.
2. **Contract specs:** with Fyers, lot sizes, tick sizes and expiries come from the exchange master. `instruments/specs.py` is only the simulator's fallback.

## Next

1. **Verify M2 live.** Follow the DoD in PRD §16.
2. **Finish M3:** backfill real history, review Strategy #1 out-of-sample, and reconcile costs with a contract note. See PRD §18.2.

See the PRD for the full milestone list.
