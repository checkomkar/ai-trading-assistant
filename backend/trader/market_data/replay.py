"""ReplayProvider: rehearse a stored trading day through the live pipeline (PRD §14.1, M4).

It plays stored 1m candles of one day (index, near future, INDIA VIX) back as ticks, 12 per minute
(every 5 s), shaped open -> first extreme -> second extreme -> close so the collector's candle
aggregator rebuilds the stored OHLC exactly. Because ticks go through the normal `MarketDataService`,
the dashboard, strategy runner, risk engine and paper broker run unmodified.

* **Option quotes are synthetic.** Stored candles carry no option bid/ask for past days, so
  quotes are Black-Scholes on the replayed spot with IV = INDIA VIX x multiplier and a modelled
  spread, the same pricer the backtest's synthetic mode uses. Everything is labelled
  `option_pricing = synthetic`, and nothing in a replay is real fills.
* **Never mixed with real data.** Replay instruments use `REPLAY:` tokens and market data from a
  replay is not persisted, so a rehearsal cannot overwrite real candles.
* **Deterministic.** There is no randomness. The clock is `clock`, advanced only by the stream (paced
  by `speed`; 0 means as fast as possible) or by `steps()` for an in-process driver.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import AsyncIterator, Iterator

from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.backtest import data as bt_data
from trader.backtest.pricing import Contract, ExecutionModel, SyntheticPricer, expiry_for
from trader.domain import Instrument, InstrumentType, Tick
from trader.instruments.calendar import IST, TradingCalendar, get_calendar
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL, get_spec
from trader.instruments.strikes import OptionType, atm_strike
from trader.market_data.base import MarketDataProvider
from trader.market_data.candles import Candle

STEP_SECONDS = 5
STEPS_PER_MINUTE = 60 // STEP_SECONDS  # 12
PREFIX = "REPLAY:"
# Indices into the 12 points of a minute where the two extremes and the close are printed.
_A, _B, _C = 4, 8, 11


def _path(c: Candle) -> list[float]:
    """12 prices from open to close visiting both extremes, so tick-built OHLC equals `c`."""
    first, second = (c.low, c.high) if c.close >= c.open else (c.high, c.low)
    knots = [(0, c.open), (_A, first), (_B, second), (_C, c.close)]
    out: list[float] = []
    for (i0, p0), (i1, p1) in zip(knots, knots[1:]):
        for i in range(i0, i1):
            out.append(p0 + (p1 - p0) * (i - i0) / (i1 - i0))
    out.append(c.close)
    out[_A], out[_B] = first, second
    return out


@dataclass
class _Series:
    token: str
    candles: dict[datetime, Candle]  # minute start (UTC) -> 1m candle


class ReplayProvider(MarketDataProvider):
    name = "replay"
    has_own_clock = True
    persist_market_data = False  # a rehearsal must never write into the real candle tables
    option_pricing = "synthetic (Black-Scholes on replayed spot)"

    def __init__(
        self,
        sessionmaker: async_sessionmaker,
        day: date,
        *,
        speed: float = 60.0,
        source: str = "real",
        underlyings: tuple[str, ...] = tuple(UNDERLYINGS),
        start_time: time = time(9, 15),
        end_time: time = time(15, 30),
        option_window: int = 8,
        ladder_width: int = 25,
        calendar: TradingCalendar | None = None,
        risk_free_rate: float = 0.065,
    ) -> None:
        super().__init__()
        self.sessionmaker, self.day, self.speed, self.source = sessionmaker, day, speed, source
        self.is_simulated = source == "simulated"
        self.underlyings = underlyings
        self.start_time, self.end_time = start_time, end_time
        self.option_window, self.ladder_width = option_window, ladder_width
        self.cal = calendar or get_calendar()
        self.pricer = SyntheticPricer(self.cal, risk_free_rate)
        self.exe = ExecutionModel()
        self.clock = datetime.combine(day, start_time, tzinfo=IST).astimezone(timezone.utc)
        self._loaded = False
        self._instruments: dict[str, Instrument] = {}
        self._idx: dict[str, _Series] = {}
        self._fut: dict[str, _Series] = {}
        self._vix: _Series | None = None
        self._options: dict[str, list[tuple[Instrument, Contract]]] = {}
        self._cum_volume: dict[str, int] = {}

    # ---- clock ---------------------------------------------------------------------------------
    def clock_now(self) -> datetime:
        return self.clock

    @property
    def start(self) -> datetime:
        return datetime.combine(self.day, self.start_time, tzinfo=IST).astimezone(timezone.utc)

    @property
    def end(self) -> datetime:
        return datetime.combine(self.day, self.end_time, tzinfo=IST).astimezone(timezone.utc)

    # ---- data ----------------------------------------------------------------------------------
    async def load(self) -> None:
        if self._loaded:
            return
        if not self.cal.is_trading_day(self.day):
            raise LookupError(f"{self.day} is not an NSE trading day")
        self._instruments.clear()
        tag = PREFIX
        for und in self.underlyings:
            async with self.sessionmaker() as s:
                data = await bt_data.load(s, und, "1m", self.day, self.day, self.source)
            if not data.bars or data.built_from != "1m":
                raise LookupError(f"no stored 1m candles for {und} on {self.day} (source={self.source}); "
                                  "backfill history first")
            spec = get_spec(und)
            self._idx[und] = _Series(f"{tag}{und}", {b.ts: b for b in data.bars})
            self._instruments[f"{tag}{und}"] = Instrument(f"{tag}{und}", und, und, "NSE", InstrumentType.INDEX)
            if data.futures:
                self._fut[und] = _Series(f"{tag}{und}-FUT", {t: c for t, c in data.futures.items()})
                self._instruments[f"{tag}{und}-FUT"] = Instrument(
                    f"{tag}{und}-FUT", f"{und}FUT", und, "NFO", InstrumentType.FUT, lot_size=spec.lot_size,
                    tick_size=spec.tick_size)
            if self._vix is None and data.vix:
                self._vix = _Series(f"{tag}{VIX_SYMBOL}", {t: Candle(VIX_SYMBOL, "1m", t, v, v, v, v, closed=True)
                                                            for t, v in data.vix.items()})
                self._instruments[self._vix.token] = Instrument(self._vix.token, VIX_SYMBOL, VIX_SYMBOL, "NSE",
                                                                InstrumentType.INDEX, tick_size=0.0025)
            self._build_options(und, data.bars[0].open)
        self._loaded = True

    def _build_options(self, und: str, open_px: float) -> None:
        spec = get_spec(und)
        e1 = expiry_for(und, self.day, self.cal, True)
        exps = [e1, expiry_for(und, e1, self.cal, True)]
        rows: list[tuple[Instrument, Contract]] = []
        for exp in exps:
            for i in range(-self.ladder_width, self.ladder_width + 1):
                k = float(atm_strike(und, open_px) + i * spec.strike_interval)
                for ot in OptionType:
                    token = f"{PREFIX}{und}-{exp.isoformat()}-{k:g}-{ot.value}"
                    inst = Instrument(token, f"{und}{exp:%d%b%y}{k:g}{ot.value}".upper(), und, "NFO",
                                      InstrumentType.OPT, lot_size=spec.lot_size, tick_size=spec.tick_size,
                                      expiry=exp, strike=k, option_type=ot)
                    self._instruments[token] = inst
                    rows.append((inst, Contract(und, exp, k, ot, spec.lot_size)))
        self._options[und] = rows

    # ---- ticks ---------------------------------------------------------------------------------
    def steps(self) -> Iterator[tuple[datetime, list[Tick]]]:
        """Synchronous replay: (sim time, ticks) every 5 s from `start` to `end` (exclusive).
        Setting `clock` is the caller's job; `_stream` does it, as does the in-process driver."""
        assert self._loaded, "call load() first"
        self._cum_volume.clear()
        t = self.start
        while t < self.end:
            yield t, self._ticks_at(t)
            t += timedelta(seconds=STEP_SECONDS)

    def _ticks_at(self, t: datetime) -> list[Tick]:
        minute = t.replace(second=0, microsecond=0)
        k = (t - minute).seconds // STEP_SECONDS
        ticks: list[Tick] = []
        vix_c = self._vix.candles.get(minute) if self._vix else None
        vix = _path(vix_c)[k] if vix_c else None
        if vix_c and self._vix:
            ticks.append(Tick(self._vix.token, t, round(vix, 4)))  # type: ignore[arg-type]
        for und in self.underlyings:
            idx_c = self._idx[und].candles.get(minute)
            if idx_c is None:
                continue  # no print this minute: the feed is quiet, exactly what stored data says
            spot = _path(idx_c)[k]
            ticks.append(Tick(self._idx[und].token, t, round(spot, 2)))
            fut = self._fut.get(und)
            if fut and (fc := fut.candles.get(minute)):
                px = round(_path(fc)[k], 2)
                tok = fut.token
                # Cumulative day volume rises across the minute: k=0 repeats the previous minute's total and
                # k=11 reaches it plus this minute's volume, so rebuilt candles carry exactly the stored volume.
                base = self._cum_volume.get(tok, 0)
                cum = base + fc.volume * k // _C
                if k == _C:
                    self._cum_volume[tok] = cum
                ticks.append(Tick(tok, t, px, bid=round(px - 0.05, 2), ask=round(px + 0.05, 2),
                                  volume=cum, oi=fc.oi))
            ticks += self._option_ticks(und, spot, vix, t)
        return ticks

    def _option_ticks(self, und: str, spot: float, vix: float | None, t: datetime) -> list[Tick]:
        step = get_spec(und).strike_interval
        iv = self.pricer.iv(und, vix)
        out: list[Tick] = []
        for inst, c in self._options[und]:
            if abs(c.strike - spot) > self.option_window * step:
                continue
            mid = self.pricer.premium(c, spot, t, iv)
            half = self.exe.half_spread(mid)
            tick = inst.tick_size
            bid = max(tick, round(round((mid - half) / tick) * tick, 2))
            ask = round(round((mid + half) / tick) * tick, 2)
            out.append(Tick(inst.token, t, round(mid, 2), bid=bid, ask=max(ask, bid + tick)))
        return out

    # ---- MarketDataProvider ----------------------------------------------------------------------
    async def discover_instruments(self) -> list[Instrument]:
        await self.load()
        return list(self._instruments.values())

    async def _connect(self) -> None:
        await self.load()

    async def _disconnect(self) -> None:
        await asyncio.sleep(0)

    async def _stream(self) -> AsyncIterator[Tick]:
        for t, ticks in self.steps():
            if self._closing:
                return
            if t < self.clock:
                continue  # resuming after a reconnect
            self.clock = t
            self.heartbeat()
            for tick in ticks:
                yield tick
            await asyncio.sleep(STEP_SECONDS / self.speed if self.speed > 0 else 0)
        self.clock = self.end
        while not self._closing:  # day over: stay connected, quietly
            self.heartbeat()
            await asyncio.sleep(1.0)
