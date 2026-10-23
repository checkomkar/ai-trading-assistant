# Backtest Engine v2: what was built overnight, and what I need from you

Plan: PRD §24. Built on branch `claude/optimistic-edison-7rqa99`, one commit per phase, each with its tests passing.
You asked for all phases in one go, so I did not stop between them; review them in order and tell me what to change.

---

## B1 — Real-mode correctness and multi-year readiness

**Built**
- **Expiry by calendar (B1.1):** Real mode now trades the expiry the exchange calendar gives for the day. If that contract
  is not stored, the entry is skipped and the report names the expiries with no stored contracts. It never slides to a
  later expiry any more.
- **Expiry corrections (B1.4):** `data/fyers/expiry_map.json` (written by your Prime download when it probes a week) is
  applied by every pricer. Expiry rules corrected: NIFTY Tuesday from 2 Sep 2025; BANKNIFTY Wednesday weeklies from
  6 Sep 2023, monthly-only from 14 Nov 2024 (last Wednesday), last Thursday from Jan 2025, last Tuesday from Sep 2025.
  Each rule now starts the day after the last expiry under the old one.
- **No prints (B1.2):** a position is carried through bars where the option did not trade; a gap through the stop fills at
  the next traded open. Each trade records `no_print_bars`, and the report warns how many trades were affected.
- **Liquidity (B1.3):** Real candles: the entry bar must have traded at least one lot (`min_entry_lots`, execution
  parameter), else `illiquid entry bar`.
- **Lot sizes (B1.5):** `backend/config/lot_sizes.yaml` holds the dated history. Old contracts and synthetic runs use the
  size of their time; current contracts keep the broker's. Reports list every unverified size used.
- **Charges (B1.6):** `costs.yaml` now goes back to 2019 (STT 0.05% → 0.0625% Apr 2023 → 0.1% Oct 2024; stamp duty
  0.003% from Jul 2020). A trade before the earliest schedule fails the run instead of silently using a later one.
- **Stop slippage (B1.7):** an intrabar stop fills 10% of the bar's premium range below the stop (never below the low),
  `stop_slippage_range_pct`. Gap stops still fill at the open.
- **Coverage (B1.8):** Real and hybrid runs report how many of the at-the-money contracts that could have traded are
  stored (`option_coverage` in the result, and a warning under 95%).
- **Holidays:** candles on listed holidays (the Diwali Muhurat sessions) are left out of backtests.

**What I need from you**
1. **Check `backend/config/lot_sizes.yaml` against the NSE circulars** (search "revision in market lot" on nseindia.com).
   Every row is `verified: false`; I am not sure of the exact change dates. Fix any wrong row and set `verified: true`.
   Rupee P&L for old years scales with these numbers.
2. **Option volume units:** the liquidity check assumes Fyers reports option volume in units (shares), not lots. After
   the download, run one Real backtest on a recent month: if almost every entry is skipped as `illiquid entry bar`,
   tell me, and set `min_entry_lots` to 0 meanwhile.
3. If you have any contract note from 2019–2024, compare one trade's charges with the backtest (`costs.yaml` comments
   say which numbers are approximate).
4. After the options download, send me `backend/data/fyers/expiry_map.json`; I will fold confirmed corrections back into
   the expiry rules.

**How to see it:** run the same NIFTY month in Real mode; the warnings list coverage, missing expiries, no-print trades
and unverified lot sizes, and `skipped_entries` shows the reasons.

---

## B2 + B3 — Strategy indicators on the chart, indicator search, one indicator list

**Built**
- **What the strategy saw (B2.1):** `GET /backtests/{id}/chart` returns `series`: every indicator the run's strategy used,
  computed by the engine's own code (`FeatureEngine` + the interpreter's indicator bank) from the run's first bar. A test
  checks that the chart's RSI and EMA at each signal bar equal the values stored on the signal.
- **Panes and thresholds (B2.2, B2.3):** price-scale indicators overlay the candles; futures/VWAP, 0-100 oscillators, MACD,
  points, percent, VIX and pattern flags each get a band below. Constants the strategy compares with (RSI 55/45, ADX 18)
  are dashed lines. Selecting a trade lists each entry condition and filter with ✓/✗ at the signal bar (`checks` on
  every trade of a spec strategy).
- **Indicator search (B2.4):** the "ƒ Indicators" button opens a TradingView-style dialog: search by name, alias
  ("moving average", "cpr", "williams", "boll"), grouped by category; add up to 10 with their own length, multiplier and
  colour; chips under the chart hide/show or remove them. Added indicators are computed on the server too.
- **Saved per account (B2.5):** `/prefs/chart-indicators` (new owner-scoped `user_prefs` table, migration `e1a7c3f09b52`),
  with this browser as a fallback.
- **One implementation (B2.7):** the browser-side indicator maths (`indicators.ts`) is gone.
- **One catalogue (B3.1):** `strategy/catalogue.py` adds categories, aliases, TradingView default settings and panes to the
  spec's own indicator table; `GET /indicators?q=` serves it. 99 indicators, including the new **CPR** top/bottom central
  pivots (`cpr_tc`, `cpr_bc`).
- **Golden tests (B3.4):** 36 indicators checked against TA-Lib (test-only dependency, `pip install -e .[dev]`) or against
  TradingView's formula where TA-Lib has none (Keltner, Hull); plus a test that every catalogue operand runs.
- **Bug fixed on the way:** a strategy using the `wma` operand crashed the interpreter (wrong call signature). Fixed,
  and now covered by the every-operand test.
- The stock (daily) chart uses the same series.

**Not done / changed from the plan**
- B3.2 (`params` object per indicator): not needed. Master already gives indicators a second setting via `mult`.
- B3.3: master already had most of the list; only CPR was missing.

**What I need from you**
1. Open a backtest, press **ƒ Indicators**, search and add a few; tell me if the panes get too crowded on your screen
   (I can make bands collapsible).
2. Run `pip install -e ".[dev]"` in `backend/` once, so the TA-Lib golden tests run on your machine too (they are
   skipped when TA-Lib is missing).

---

## B4 — Speed for many runs

**Built**
- **Timings (B4.1):** every backtest result has `timings` (load, engine, metrics seconds, bars, option contracts loaded).
- **Load once, run many (B4.2):** `research/parallel.run_many` runs any number of settings over one loaded dataset.
  Real/hybrid runs now load only option contracts that could have been near the money (the calendar expiry of each day,
  strikes within 2 of that day's range), not every contract stored, which matters once years of options are in the DB.
  Candles use `__slots__` (about 15% less memory).
- **Parallel (B4.3):** worker processes ("spawn", as on Windows), each given the dataset once. Tested: identical results
  serially and in parallel; a failing combination is reported without sinking the batch; cancel stops between runs.
- **Background jobs (B4.4):** `research_jobs` table (migration `f5c2d8e7a613`), a job manager on the API (one running job
  per account, two in total, the rest queued), progress, cancel, stored results; a restart marks unfinished jobs failed.
  `GET /research/jobs`, `GET /research/jobs/{id}`, `POST /research/jobs/{id}/cancel`.
- **Benchmark (B4.1/B4.5):** `python -m trader.scripts.bench_backtest` prints DB size and rows per type/timeframe, checks
  that the candle index serves the backtest query, times one run in synthetic and hybrid pricing, and times a sweep
  serially vs in parallel.

**Not done:** vectorising the engine (out of scope by design). Postgres/Parquet: only if your benchmark says SQLite is too slow.

**What I need from you**
1. After the downloads, run (from `backend/`):
   `.venv/Scripts/python -m trader.scripts.bench_backtest --start 2025-10-01 --end 2026-10-01 --sweep 24`
   and send me the output. I will set the research-job limits (and decide if SQLite needs help) from your numbers.
