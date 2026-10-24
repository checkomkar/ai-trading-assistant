"""Option premiums for backtests (PRD §14.5, §15).

synthetic — Black-Scholes on the index, IV = INDIA VIX x multiplier, modelled spread.
            Works on index history alone; optimistic about liquidity, so reports say so.
real      — stored option candles (Fyers expired-contract downloads or our own recordings).
            Contracts without data are skipped and counted, never filled at made-up prices.
hybrid    — real where the contract has a stored print at the entry bar, otherwise the synthetic model for that whole trade.
            Every trade is tagged with the source it used, and the report counts both, so nothing is hidden. Needed because
            stored option candles are sparse (the free Fyers plan allows 10 expired-contract downloads a day).
"""

from __future__ import annotations

import calendar as _cal
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from trader.instruments.calendar import TradingCalendar
from trader.instruments.specs import ExpiryCycle, get_spec
from trader.instruments.strikes import OptionType, atm_strike
from trader.market_data.candles import Candle
from trader.options import black_scholes as bs
from trader.options.selection import choose_strike


# NSE expiry conventions over time: (effective_from, cycle, weekday Mon=0). A rule covers the *expiries* (as scheduled,
# before a holiday moves them) from `effective_from` until the next rule starts, so a day near a switch can still
# roll into the first expiry of the new rule and never into a contract the old rule would have listed after it
# (NIFTY on Thu 28 Aug 2025 rolls to Tue 2 Sep, not to a Thursday 4 Sep that never existed). Used to pick the contract
# a strategy would have traded on a historical date. Rows before VERIFIED_RULES_FROM are from memory of the NSE
# circulars; the expired-option downloader checks them against what Fyers lists and records corrections in
# data/fyers/expiry_map.json, which expiry_for() applies (`overrides`). Known simplification: Sep 2023 - Feb 2024 the
# BANKNIFTY monthly contract expired on the last Thursday while weeklies were on Wednesdays.
EXPIRY_RULES: dict[str, list[tuple[date, ExpiryCycle, int]]] = {
    "NIFTY": [(date(2000, 1, 1), ExpiryCycle.WEEKLY, 3),      # Thursday weeklies, last one 28 Aug 2025
              (date(2025, 9, 1), ExpiryCycle.WEEKLY, 1)],     # Tuesday from 2 Sep 2025
    "BANKNIFTY": [(date(2000, 1, 1), ExpiryCycle.WEEKLY, 3),   # Thursday weeklies, last one 31 Aug 2023
                  (date(2023, 9, 4), ExpiryCycle.WEEKLY, 2),   # Wednesday weeklies from 6 Sep 2023
                  (date(2024, 11, 14), ExpiryCycle.MONTHLY, 2),  # weeklies end after 13 Nov 2024; monthly, last Wednesday
                  (date(2025, 1, 1), ExpiryCycle.MONTHLY, 3),  # monthly, last Thursday from Jan 2025
                  (date(2025, 9, 1), ExpiryCycle.MONTHLY, 1)],  # monthly, last Tuesday from Sep 2025
}
VERIFIED_RULES_FROM = date(2025, 9, 1)


def _rule(underlying: str, d: date) -> tuple[ExpiryCycle, int]:
    """The rule an expiry scheduled on `d` falls under."""
    rules = [r for r in EXPIRY_RULES[underlying] if r[0] <= d]
    _, cycle, wd = rules[-1]
    return cycle, wd


def _last_weekday(y: int, m: int, wd: int) -> date:
    last = date(y, m, _cal.monthrange(y, m)[1])
    return last - timedelta(days=(last.weekday() - wd) % 7)


def _scheduled(cycle: ExpiryCycle, wd: int, day: date) -> list[date]:
    """Expiry dates a rule would schedule around `day` (from the week before, a few cycles ahead), before holidays."""
    if cycle == ExpiryCycle.WEEKLY:
        d = day + timedelta(days=(wd - day.weekday()) % 7) - timedelta(days=7)
        return [d + timedelta(days=7 * i) for i in range(5)]
    out, y, m = [], day.year, day.month
    for _ in range(3):
        out.append(_last_weekday(y, m, wd))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def expiry_for(underlying: str, day: date, cal: TradingCalendar, roll_on_expiry_day: bool = True,
               overrides: "dict[tuple[str, date], date | None] | None" = None) -> date:
    """Nearest expiry on/after `day` (holidays roll back a day), each candidate taken from the rule covering it.

    `overrides` maps a rule-derived expiry to the date the exchange really used (from the expired-option
    downloader's probing, `contract_history.load_expiry_overrides`); a None value means no contract existed
    that week, so that candidate is dropped."""
    rules = EXPIRY_RULES[underlying]
    candidates: list[date] = []
    for i, (since, cycle, wd) in enumerate(rules):
        until = rules[i + 1][0] if i + 1 < len(rules) else date.max
        candidates += [cal.roll_back_to_trading_day(d) for d in _scheduled(cycle, wd, day) if since <= d < until]
    if overrides:
        mapped = [overrides.get((underlying, c), c) for c in candidates]
        candidates = [c for c in mapped if c is not None]
    live = sorted(c for c in candidates if c > day or (c == day and not roll_on_expiry_day))
    return live[0]


def lot_size_for(underlying: str, expiry: date, fallback: int | None = None) -> tuple[int, bool]:
    """(lot size, verified) for a contract expiring on `expiry`.

    The broker's symbol master is the source for live contracts (`fallback`, trusted). The dated table is used
    for expiries before its newest row: contracts from earlier lot-size regimes, which the broker no longer lists
    and the expired-contract download does not describe."""
    from trader.instruments.contract_history import lot_size_table

    table = lot_size_table()
    rows = table.rows.get(underlying.upper(), [])
    row = table.on(underlying, expiry)
    if row is not None and rows and expiry < rows[-1].since:
        return row.lot, row.verified
    if fallback:
        return fallback, True
    if row is not None:
        return row.lot, row.verified
    return get_spec(underlying).lot_size, False


@dataclass(frozen=True)
class Contract:
    underlying: str
    expiry: date
    strike: float
    option_type: OptionType
    lot_size: int
    synthetic: bool = False  # priced by the model (hybrid mode) instead of stored candles

    @property
    def label(self) -> str:
        return f"{self.underlying} {self.expiry:%d%b%y} {self.strike:g} {self.option_type.value}".upper()


@dataclass(frozen=True)
class PremiumBar:
    """Mid-price premium path over one underlying bar."""
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


@dataclass(frozen=True)
class ExecutionModel:
    spread_pct: float = 0.30  # full bid-ask spread as % of premium (ATM index options are tight)
    slippage_ticks: int = 1
    tick: float = 0.05
    # A stop is a market order once touched: in a fast bar it fills below the level. The fill is moved this share
    # of the bar's premium range below the stop (never below the bar's low), on top of spread and tick slippage.
    stop_slippage_range_pct: float = 10.0
    # Real option candles: the entry bar must have traded at least this many lots, or the entry is skipped.
    min_entry_lots: float = 1.0

    def stop_fill(self, stop: float, bar_high: float, bar_low: float) -> float:
        """Mid-price level an intrabar stop fills at (before the spread and tick slippage of `sell`)."""
        return max(bar_low, stop - (bar_high - bar_low) * self.stop_slippage_range_pct / 100)

    def half_spread(self, mid: float) -> float:
        return max(self.tick / 2, mid * self.spread_pct / 200)

    def buy(self, mid: float) -> float:
        return _round_tick(mid + self.half_spread(mid) + self.slippage_ticks * self.tick, self.tick)

    def sell(self, mid: float) -> float:
        return max(self.tick, _round_tick(mid - self.half_spread(mid) - self.slippage_ticks * self.tick, self.tick))


def _round_tick(x: float, tick: float) -> float:
    return round(round(x / tick) * tick, 2)


class SyntheticPricer:
    mode = "synthetic"

    def __init__(self, cal: TradingCalendar, risk_free_rate: float = 0.065, iv_multiplier: dict | None = None,
                 default_iv: float = 0.14, roll_on_expiry_day: bool = True,
                 expiry_overrides: dict | None = None, calibration=None) -> None:  # noqa: ANN001
        self.cal, self.r = cal, risk_free_rate
        self.calibration = calibration  # backtest.calibration.Calibration fitted on real option prices, or None
        self.iv_mult = iv_multiplier or {"NIFTY": 1.0, "BANKNIFTY": 1.15}
        self.default_iv = default_iv
        self.roll = roll_on_expiry_day
        self.overrides = expiry_overrides or {}
        self._confirmed = {(u, v) for (u, _), v in self.overrides.items() if v is not None}
        self.missing_vix_bars = 0
        self.unverified_expiry_dates: set[date] = set()
        self.unverified_lots: set[tuple[str, int]] = set()

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None, selection: str = "ATM", at: datetime | None = None,
                 vix: float | None = None) -> Contract | None:
        exp = expiry_for(underlying, day, self.cal, self.roll, self.overrides)
        strike = choose_strike(underlying, option_type, spot, selection, expiry=exp, at=at, vix=vix, r=self.r)
        if strike is None:
            return None
        if day < VERIFIED_RULES_FROM and (underlying, exp) not in self._confirmed:
            self.unverified_expiry_dates.add(day)
        if lot_size is None:
            lot_size, ok = lot_size_for(underlying, exp, get_spec(underlying).lot_size)
            if not ok:
                self.unverified_lots.add((underlying, lot_size))
        return Contract(underlying, exp, float(strike), option_type, lot_size)

    def iv(self, underlying: str, vix: float | None) -> float:
        if vix is None or vix <= 0:
            self.missing_vix_bars += 1
            return self.default_iv
        return vix / 100 * self.iv_mult.get(underlying, 1.0)

    def premium(self, c: Contract, spot: float, at: datetime, iv: float) -> float:
        t = max(bs.time_to_expiry(c.expiry, at), bs.MIN_T)
        return max(bs.price(spot, c.strike, t, self.r, iv, c.option_type), 0.05)

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None:
        iv = self.iv(c.underlying, vix)
        if self.calibration is not None and vix is not None and vix > 0:
            # IV/VIX ratio fitted on real option prices for this moneyness, days to expiry and time of day (PRD §24.9)
            iv = vix / 100 * self.calibration.ratio(u.open, c.strike, c.option_type, c.expiry, u.ts)
        mid_t = u.ts + tf / 2
        o = self.premium(c, u.open, u.ts, iv)
        cl = self.premium(c, u.close, u.ts + tf, iv)
        # Premium is monotonic in spot: the CE peaks at the underlying high, the PE at the low.
        best, worst = (u.high, u.low) if c.option_type == OptionType.CE else (u.low, u.high)
        hi = max(o, cl, self.premium(c, best, mid_t, iv))
        lo = min(o, cl, self.premium(c, worst, mid_t, iv))
        return PremiumBar(o, hi, lo, cl)


class HybridPricer:
    """Stored candles where they exist for the entry, the model otherwise. One source per trade: a position is never priced by
    real candles on one bar and by Black-Scholes on the next, which would put artificial jumps into its path."""

    mode = "hybrid"

    def __init__(self, real: "RealPricer", synthetic: SyntheticPricer) -> None:
        self.real, self.synthetic = real, synthetic
        self.default_iv = synthetic.default_iv

    @property
    def missing_vix_bars(self) -> int:
        return self.synthetic.missing_vix_bars

    @property
    def unverified_lots(self) -> set[tuple[str, int]]:
        return self.real.unverified_lots | self.synthetic.unverified_lots

    @property
    def unverified_expiry_dates(self) -> set[date]:
        return self.synthetic.unverified_expiry_dates

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None, selection: str = "ATM", at: datetime | None = None,
                 vix: float | None = None) -> Contract | None:
        real = self.real.contract(underlying, option_type, spot, day, lot_size, selection, at, vix)
        if real is not None:
            return real
        m = self.synthetic.contract(underlying, option_type, spot, day, lot_size, selection, at, vix)  # nothing stored
        if m is None:
            return None
        return Contract(m.underlying, m.expiry, m.strike, m.option_type, m.lot_size, True)

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None:
        return self.synthetic.bar(c, u, tf, vix) if c.synthetic else self.real.bar(c, u, tf, vix)

    def fallback(self, c: Contract) -> Contract | None:
        """The same contract priced by the model, for when the stored candles have no print at the entry bar."""
        return None if c.synthetic else Contract(c.underlying, c.expiry, c.strike, c.option_type, c.lot_size, True)


class RealPricer:
    """Option candles from the database, keyed by (expiry, strike, option_type) -> {bar ts: Candle}."""

    mode = "real"

    def __init__(self, candles: dict[tuple[date, float, OptionType], dict[datetime, Candle]],
                 lot_sizes: dict[tuple[date, float, OptionType], int], cal: TradingCalendar,
                 roll_on_expiry_day: bool = True, expiry_overrides: dict | None = None) -> None:
        self.candles, self.lot_sizes, self.cal, self.roll = candles, lot_sizes, cal, roll_on_expiry_day
        self.overrides = expiry_overrides or {}
        self.missing_contracts: set[str] = set()
        self.missing_expiries: set[date] = set()  # the true expiry had no stored contracts at all
        self.unverified_lots: set[tuple[str, int]] = set()
        self._expiries = {k[0] for k in candles}

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None, selection: str = "ATM", at: datetime | None = None,
                 vix: float | None = None) -> Contract | None:
        # The contract a trader would have bought is fixed by the exchange calendar, never by which contracts happen
        # to be stored: if the true expiry is missing, the entry is skipped rather than moved to a later expiry.
        exp = expiry_for(underlying, day, self.cal, self.roll, self.overrides)
        k = choose_strike(underlying, option_type, spot, selection, expiry=exp, at=at, vix=vix)
        if k is None:
            return None
        strike = float(k)
        key = (exp, strike, option_type)
        if key not in self.candles:
            if exp not in self._expiries:
                self.missing_expiries.add(exp)
            self.missing_contracts.add(f"{underlying} {exp} {strike:g} {option_type.value}")
            return None
        if lot_size is None:
            lot_size, ok = lot_size_for(underlying, exp, self.lot_sizes.get(key))
            if not ok:
                self.unverified_lots.add((underlying, lot_size))
        return Contract(underlying, exp, strike, option_type, lot_size)

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None:
        k = self.candles.get((c.expiry, c.strike, c.option_type), {}).get(u.ts)
        if k is None or not all(math.isfinite(x) for x in (k.open, k.high, k.low, k.close)):
            return None  # no trade printed in this bar
        return PremiumBar(k.open, k.high, k.low, k.close, k.volume)
