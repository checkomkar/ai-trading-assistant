"""Simulated NSE feed (PRD §14.1).

Generates a realistic-looking intraday session so the whole pipeline (storage, candles,
option analytics, dashboard, and later strategies / paper broker) can run without broker
credentials or outside market hours:

* NIFTY / BANKNIFTY spot: correlated GBM with a slowly mean-reverting drift (trend bursts).
* INDIA VIX: Ornstein-Uhlenbeck, negatively correlated with NIFTY returns.
* Near-month futures: spot + cost-of-carry basis, with traded volume (indices have none).
* Options: Black-Scholes on the future with a put-skewed smile driven by VIX, two-sided
  quotes, cumulative volume and evolving open interest. Illiquid strikes tick less often.

Everything is seeded (`SIM_SEED`) so tests are deterministic.
"""

from __future__ import annotations

import asyncio
import math
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import AsyncIterator, Callable

from trader.domain import Instrument, InstrumentType, Tick
from trader.market_data.candles import TIMEFRAMES, Candle, CandleAggregator
from trader.instruments.calendar import TradingCalendar, get_calendar
from trader.instruments.expiry import monthly_expiry, upcoming_expiries
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL, get_spec
from trader.instruments.strikes import OptionType, strike_ladder
from trader.market_data.base import MarketDataProvider
from trader.options import black_scholes as bs

TRADING_SECONDS_PER_YEAR = 252 * 6.25 * 3600

DEFAULT_SPOT = {"NIFTY": 25_000.0, "BANKNIFTY": 56_000.0}
IV_MULTIPLIER = {"NIFTY": 1.0, "BANKNIFTY": 1.18}
VOL_MULTIPLIER = {"NIFTY": 1.0, "BANKNIFTY": 1.2}


def _round_tick(x: float, tick: float = 0.05) -> float:
    return round(round(x / tick) * tick, 2)


@dataclass
class _Underlying:
    symbol: str
    spot: float
    prev_close: float
    open: float
    high: float
    low: float
    drift: float = 0.0  # annualised, mean-reverting
    fut_volume: int = 0


@dataclass
class _OptionState:
    inst: Instrument
    oi: int
    prev_oi: int
    volume: int = 0
    last_price: float | None = None


@dataclass
class _Vix:
    level: float
    prev_close: float
    open: float
    high: float
    low: float
    mean: float = 13.5


@dataclass
class SimConfig:
    seed: int | None = None
    tick_interval: float = 1.0
    strikes_each_side: int = 20
    nifty_expiries: int = 2
    banknifty_expiries: int = 2
    disconnect_probability: float = 0.0
    ignore_session: bool = True
    risk_free_rate: float = 0.065
    initial_spot: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SPOT))
    initial_vix: float = 13.5


class SimulatedMarketDataProvider(MarketDataProvider):
    name = "simulated"
    is_simulated = True

    def __init__(
        self,
        config: SimConfig | None = None,
        now_fn: Callable[[], datetime] | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        super().__init__()
        self.cfg = config or SimConfig()
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._cal = calendar or get_calendar()
        self._rng = random.Random(self.cfg.seed)
        self._instruments: dict[str, Instrument] = {}
        self._und: dict[str, _Underlying] = {}
        self._opts: dict[str, _OptionState] = {}
        self._vix: _Vix | None = None
        self._fut_token: dict[str, str] = {}
        self._index_token: dict[str, str] = {}
        self._vix_token = f"SIM:{VIX_SYMBOL}"
        self._initialised = False

    # ---- discovery ------------------------------------------------------------
    async def discover_instruments(self) -> list[Instrument]:
        self._ensure_initialised()
        return list(self._instruments.values())

    def _ensure_initialised(self) -> None:
        if self._initialised:
            return
        now = self._now()
        rng = self._rng
        vix0 = self.cfg.initial_vix * (1 + rng.gauss(0, 0.03))
        self._vix = _Vix(level=vix0, prev_close=vix0 * (1 + rng.gauss(0, 0.02)), open=vix0, high=vix0, low=vix0,
                         mean=self.cfg.initial_vix)
        self._add(Instrument(self._vix_token, VIX_SYMBOL, VIX_SYMBOL, "NSE", InstrumentType.INDEX, tick_size=0.0025))

        for sym in UNDERLYINGS:
            spec = get_spec(sym)
            spot = self.cfg.initial_spot.get(sym, DEFAULT_SPOT[sym])
            prev_close = spot * (1 + rng.gauss(0, 0.004))
            self._und[sym] = _Underlying(sym, spot, prev_close, spot, spot, spot)

            idx_token = f"SIM:{sym}"
            self._index_token[sym] = idx_token
            self._add(Instrument(idx_token, sym, sym, "NSE", InstrumentType.INDEX, tick_size=0.05))

            local = now.astimezone(bs.IST)
            fut_exp = monthly_expiry(local.year, local.month, spec.expiry_weekday, self._cal)
            if fut_exp < local.date() or (fut_exp == local.date() and local.time() >= bs.EXPIRY_TIME):
                y, m = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
                fut_exp = monthly_expiry(y, m, spec.expiry_weekday, self._cal)
            fut_token = f"SIM:{sym}-FUT-{fut_exp.isoformat()}"
            self._fut_token[sym] = fut_token
            self._add(Instrument(fut_token, f"{sym}{fut_exp:%y%b}FUT".upper(), sym, "NFO", InstrumentType.FUT,
                                 lot_size=spec.lot_size, tick_size=spec.tick_size, expiry=fut_exp))

            n_exp = self.cfg.nifty_expiries if sym == "NIFTY" else self.cfg.banknifty_expiries
            for exp in upcoming_expiries(spec, now, count=n_exp, cal=self._cal):
                for k in strike_ladder(sym, spot, self.cfg.strikes_each_side):
                    for ot in OptionType:
                        token = f"SIM:{sym}-{exp.isoformat()}-{k}-{ot.value}"
                        inst = Instrument(token, f"{sym}{exp:%d%b%y}{k}{ot.value}".upper(), sym, "NFO",
                                          InstrumentType.OPT, lot_size=spec.lot_size, tick_size=spec.tick_size,
                                          expiry=exp, strike=float(k), option_type=ot)
                        self._add(inst)
                        oi0 = self._initial_oi(sym, spot, k, ot, spec.lot_size, spec.strike_interval)
                        prev = max(0, int(oi0 * (1 + rng.gauss(-0.05, 0.08))) // spec.lot_size * spec.lot_size)
                        self._opts[token] = _OptionState(inst, oi=oi0, prev_oi=prev)
        self._initialised = True

    def _add(self, inst: Instrument) -> None:
        self._instruments[inst.token] = inst

    def _initial_oi(self, sym: str, spot: float, k: int, ot: OptionType, lot: int, step: int) -> int:
        """OI peaks slightly OTM (option writers), with extra interest at round strikes."""
        dist = (k - spot) / (step * 6)
        otm_side = dist if ot == OptionType.CE else -dist
        bump = math.exp(-((otm_side - 0.6) ** 2) / 1.2) + 0.25 * math.exp(-(dist ** 2) / 4)
        round_bonus = 1.6 if k % (step * 10) == 0 else (1.25 if k % (step * 2) == 0 else 1.0)
        base_lots = 90_000 if sym == "NIFTY" else 25_000
        lots = base_lots * bump * round_bonus * self._rng.uniform(0.7, 1.3)
        return int(lots) * lot

    # ---- connection -------------------------------------------------------------
    async def _connect(self) -> None:
        self._ensure_initialised()
        await asyncio.sleep(0)

    async def _disconnect(self) -> None:
        await asyncio.sleep(0)

    async def _stream(self) -> AsyncIterator[Tick]:
        while not self._closing:
            await asyncio.sleep(self.cfg.tick_interval)
            self.heartbeat()
            now = self._now()
            if not self.cfg.ignore_session and not self._cal.is_market_open(now):
                continue
            if self.cfg.disconnect_probability and self._rng.random() < self.cfg.disconnect_probability:
                raise ConnectionError("simulated feed disconnect")
            for tick in self.step(now):
                if tick.token in self._subscribed:
                    yield tick

    async def snapshot(self, instruments: list[Instrument]) -> list[Tick]:
        wanted = {i.token for i in instruments}
        return [t for t in self.step(self._now()) if t.token in wanted]

    # ---- model ------------------------------------------------------------------
    def step(self, now: datetime, dt_seconds: float | None = None) -> list[Tick]:
        """Advance the model by one interval and return ticks for *all* instruments."""
        self._ensure_initialised()
        assert self._vix is not None
        rng = self._rng
        dt = (dt_seconds or self.cfg.tick_interval) / TRADING_SECONDS_PER_YEAR
        vix = self._vix
        ticks: list[Tick] = []

        z_mkt = rng.gauss(0, 1)
        for sym, u in self._und.items():
            sigma = vix.level / 100 * VOL_MULTIPLIER[sym]
            # OU drift with ~1h half-life and ~1.4 annualised std: produces intraday trend bursts.
            u.drift += -6.0 * u.drift * (dt * 252) + 5.0 * rng.gauss(0, 1) * math.sqrt(dt * 252)
            z = 0.85 * z_mkt + math.sqrt(1 - 0.85 ** 2) * rng.gauss(0, 1)
            u.spot *= math.exp((u.drift - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z)
            u.high, u.low = max(u.high, u.spot), min(u.low, u.spot)
            ticks.append(Tick(self._index_token[sym], now, _round_tick(u.spot), volume=0,
                              open=_round_tick(u.open), high=_round_tick(u.high), low=_round_tick(u.low),
                              prev_close=_round_tick(u.prev_close)))

        # VIX: mean reverting, rises when the market falls.
        sqrt_day = math.sqrt(dt * 252)
        vix.level += 0.15 * (vix.mean - vix.level) * dt * 252 - 0.9 * z_mkt * sqrt_day + 0.6 * rng.gauss(0, 1) * sqrt_day
        vix.level = min(max(vix.level, 8.0), 60.0)
        vix.high, vix.low = max(vix.high, vix.level), min(vix.low, vix.level)
        ticks.append(Tick(self._vix_token, now, round(vix.level, 4), volume=0, open=round(vix.open, 4),
                          high=round(vix.high, 4), low=round(vix.low, 4), prev_close=round(vix.prev_close, 4)))

        forwards: dict[str, dict[date, float]] = {}
        for sym, u in self._und.items():
            fut = self._instruments[self._fut_token[sym]]
            assert fut.expiry
            t = bs.time_to_expiry(fut.expiry, now)
            fut_px = u.spot * math.exp(self.cfg.risk_free_rate * t) + rng.gauss(0, 0.5)
            u.fut_volume += self._lots(rng.expovariate(1 / 12)) * fut.lot_size
            spread = fut.tick_size * rng.choice((1, 1, 2, 3))
            ticks.append(Tick(fut.token, now, _round_tick(fut_px), bid=_round_tick(fut_px - spread),
                              ask=_round_tick(fut_px + spread), volume=u.fut_volume))
            forwards[sym] = {}

        for st in self._opts.values():
            inst = st.inst
            u = self._und[inst.underlying]
            assert inst.expiry and inst.strike is not None and inst.option_type
            step = get_spec(inst.underlying).strike_interval
            dist = abs(inst.strike - u.spot) / step
            # Illiquid (far) strikes trade less frequently.
            if st.last_price is not None and rng.random() > 0.25 + 0.75 * math.exp(-(dist ** 2) / 40):
                continue
            t = max(bs.time_to_expiry(inst.expiry, now), bs.MIN_T)
            fwd = forwards[inst.underlying].setdefault(
                inst.expiry, u.spot * math.exp(self.cfg.risk_free_rate * t))
            iv = self._smile_iv(inst.underlying, fwd, inst.strike, t, vix.level)
            theo = bs.price(u.spot, inst.strike, t, self.cfg.risk_free_rate, iv, inst.option_type)
            px = max(_round_tick(theo * (1 + rng.gauss(0, 0.002))), inst.tick_size)
            half = max(inst.tick_size, _round_tick(px * 0.0012 + 0.05 * dist / 4))
            bid, ask = max(px - half, inst.tick_size), px + half

            liquidity = math.exp(-(dist ** 2) / 30)
            st.volume += self._lots(rng.expovariate(1 / (1 + 40 * liquidity))) * inst.lot_size
            moved = px - (st.last_price or px)
            # Writers add OI on strikes the market moves away from; buyers unwind into strength.
            writer_bias = -moved if inst.option_type == OptionType.CE else moved
            d_lots = rng.gauss(0.3 * liquidity, 3 * liquidity + 0.2) + 0.02 * writer_bias
            st.oi = max(0, st.oi + int(d_lots) * inst.lot_size)
            st.last_price = px
            ticks.append(Tick(inst.token, now, px, bid=_round_tick(bid), ask=_round_tick(ask),
                              volume=st.volume, oi=st.oi, prev_oi=st.prev_oi))
        return ticks

    def backfill(self, minutes: int, timeframes: tuple[str, ...] = ("1m", "5m")) -> list[Candle]:
        """Synthetic candle history ending at the current simulated price (dev convenience only).

        Uses the same volatility as the live model at 5-second resolution, then rescales the
        path so that it ends exactly at today's spot. Written only to `SIM:*` instruments.
        """
        self._ensure_initialised()
        if minutes <= 0:
            return []
        rng = random.Random((self.cfg.seed or 0) + 7919)
        end = self._now()
        step_s = 5
        n = minutes * 60 // step_s
        dt = step_s / TRADING_SECONDS_PER_YEAR
        agg = CandleAggregator(timeframes)
        assert self._vix is not None

        series: dict[str, list[float]] = {}
        for sym, u in self._und.items():
            sigma, drift, x = self._vix.level / 100 * VOL_MULTIPLIER[sym], 0.0, 1.0
            path = []
            for _ in range(n):
                drift += -6.0 * drift * dt * 252 + 5.0 * rng.gauss(0, 1) * math.sqrt(dt * 252)
                x *= math.exp((drift - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
                path.append(x)
            scale = u.spot / path[-1]
            series[self._index_token[sym]] = [p * scale for p in path]
            fut = self._instruments[self._fut_token[sym]]
            assert fut.expiry
            basis = math.exp(self.cfg.risk_free_rate * bs.time_to_expiry(fut.expiry, end))
            series[fut.token] = [p * scale * basis for p in path]

        out: list[Candle] = []
        for token, path in series.items():
            is_fut = token in self._fut_token.values()
            cum = 0
            for i, px in enumerate(path):
                ts = end - timedelta(seconds=(n - i) * step_s)
                if is_fut:
                    cum += self._lots(rng.expovariate(1 / 60)) * self._instruments[token].lot_size
                out += agg.update(token, ts, _round_tick(px), cum if is_fut else 0)
        # Keep only completed buckets: the live feed continues the current one.
        return out + [c for c in agg.flush() if c.ts + TIMEFRAMES[c.timeframe] <= end]

    @staticmethod
    def _lots(x: float) -> int:
        return max(0, int(x))

    @staticmethod
    def _smile_iv(sym: str, fwd: float, k: float, t: float, vix: float) -> float:
        atm = vix / 100 * IV_MULTIPLIER[sym] * (1 + 0.15 * math.exp(-t * 365 / 3))  # short-dated premium
        m = math.log(k / fwd) / (atm * math.sqrt(t))
        iv = atm * (1 - 0.06 * m + 0.025 * m * m)
        return min(max(iv, atm * 0.6), atm * 3.0)
