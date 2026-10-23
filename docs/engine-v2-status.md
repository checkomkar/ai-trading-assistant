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
