"""Market-data collector (PRD §1, Sprint 2-3).

    provider.stream() -> quality check -> latest-quote cache -> candle aggregation
                      -> event bus (/ws) -> batched DB writes (ticks, candles, option snapshots)
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from typing import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.config import Settings
from trader.db import repository as repo
from trader.domain import Instrument, InstrumentType, Tick
from trader.instruments.calendar import IST, TradingCalendar, get_calendar
from trader.instruments.registry import InstrumentRegistry
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL, get_spec
from trader.instruments.strikes import OptionType
from trader.logging import get_logger
from trader.market_data.base import MarketDataProvider, ProviderAuthError
from trader.market_data.bus import EventBus
from trader.market_data.candles import TIMEFRAMES, Candle, CandleAggregator
from trader.market_data.quality import DataQualityMonitor
from trader.options.chain import OptionChain, build_chain

log = get_logger("collector")

TOPIC_TICKS = "market.ticks"
TOPIC_CANDLES = "market.candles"


@dataclass
class Quote:
    symbol: str
    token: str
    ltp: float
    change: float | None
    change_pct: float | None
    open: float | None
    high: float | None
    low: float | None
    prev_close: float | None
    ts: datetime


class MarketDataService:
    def __init__(
        self,
        provider: MarketDataProvider,
        sessionmaker: async_sessionmaker | None,
        settings: Settings,
        bus: EventBus | None = None,
        calendar: TradingCalendar | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self.provider = provider
        self.sessionmaker = sessionmaker
        self.settings = settings
        self.bus = bus or EventBus()
        self.calendar = calendar or get_calendar()
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.registry = InstrumentRegistry()
        self.quality = DataQualityMonitor()
        self.candles = CandleAggregator(("1m", "5m"))
        self.latest: dict[str, Tick] = {}
        self.token_to_id: dict[str, int] = {}

        self._tick_buffer: list[dict] = []
        self._candle_buffer: list[Candle] = []
        self._step_cache: dict[tuple, float] = {}
        # token -> close of the trading day before the current session (from stored candles).
        self.ref_prev_close: dict[str, float] = {}
        self._candle_tokens: set[str] = set()
        self._tasks: list[asyncio.Task] = []
        self._tick_times: deque[float] = deque(maxlen=5000)
        self._last_snapshot = 0.0
        self.started_at: datetime | None = None
        self.writes = {"ticks": 0, "candles": 0, "option_snapshots": 0, "errors": 0}

    # ---- lifecycle ------------------------------------------------------------
    async def discover(self) -> list[Instrument]:
        """Load instruments into the registry and mark which tokens are indices / get candles."""
        instruments = await self.provider.discover_instruments()
        self.registry.add_many(instruments)
        log.info("instruments discovered", event="instruments.discovered", count=len(instruments))
        index_tokens = {i.token for i in instruments if i.instrument_type == InstrumentType.INDEX}
        self.quality.index_tokens = index_tokens
        self.quality.watched_tokens = index_tokens
        self._candle_tokens = {i.token for i in instruments
                               if i.instrument_type in (InstrumentType.INDEX, InstrumentType.FUT)}
        return instruments

    async def start(self) -> None:
        self.started_at = self.now()
        instruments = await self.discover()

        if self.sessionmaker:
            async with self.sessionmaker() as s, s.begin():
                self.token_to_id = await repo.upsert_instruments(s, instruments)
                await repo.record_event(s, "collector", "collector.started",
                                        {"provider": self.provider.name, "instruments": len(instruments)})
            await self._backfill_simulated()
            await self._backfill_history()
            await self.refresh_reference_closes()

        # Seed quotes over REST so the dashboard (and ATM selection) work before the first tick.
        try:
            for tick in await self.provider.snapshot(self._core_instruments()):
                self.ingest(tick)
        except Exception as exc:  # noqa: BLE001 - seeding is best effort
            log.warning("snapshot seed failed", event="collector.snapshot_failed", error=str(exc))

        await self.rebalance_subscriptions()
        self._tasks = [
            asyncio.create_task(self._consume(), name="md-consume"),
            asyncio.create_task(self._flush_loop(), name="md-flush"),
            asyncio.create_task(self._rebalance_loop(), name="md-rebalance"),
        ]

    async def restart(self) -> None:
        """Reconnect the feed with fresh credentials (the daily broker sign-in) without restarting the process."""
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        try:
            await self.provider.disconnect()
        except Exception:  # noqa: BLE001 - already broken is fine
            pass
        self.provider.reopen()
        await self.start()
        log.info("collector restarted", event="collector.restarted")

    # ---- subscriptions (PRD §17.3) ----------------------------------------------------
    def _core_instruments(self) -> list[Instrument]:
        core = [i for sym in (*UNDERLYINGS, VIX_SYMBOL) if (i := self.registry.index(sym))]
        core += [f for sym in UNDERLYINGS if (f := self.registry.future(sym))]
        return core

    def desired_tokens(self) -> list[str]:
        """Indices + near futures + ATM-window options, nearest-to-ATM first, capped at max_symbols."""
        policy = self.provider.subscription_policy
        now = self.now()
        core = [i.token for i in self._core_instruments()]
        ranked: list[tuple[float, str]] = []
        for und in UNDERLYINGS:
            spot = self.spot(und)
            if spot is None:
                continue  # options are added once the underlying has a price
            for exp in self.registry.expiries(und, now)[: policy.expiries_per_underlying]:
                strikes = self.registry.strikes(und, exp)
                if not strikes:
                    continue
                step = self._strike_step(und, exp)
                atm = min(strikes, key=lambda k: abs(k - spot))
                for k in strikes:
                    dist = abs(k - atm) / step
                    if dist > policy.strikes_each_side + 1e-9:
                        continue
                    for ot in OptionType:
                        inst = self.registry.option(und, exp, k, ot)
                        if inst:
                            ranked.append((dist, inst.token))
        ranked.sort()
        tokens = core + [t for _, t in ranked]
        return tokens if policy.max_symbols is None else tokens[: policy.max_symbols]

    async def rebalance_subscriptions(self) -> dict[str, int]:
        desired = set(self.desired_tokens())
        current = set(self.provider.subscribed)
        add, drop = desired - current, current - desired
        if drop:
            await self.provider.unsubscribe(drop)
        if add:
            await self.provider.subscribe(add)
        if add or drop:
            log.info("subscriptions rebalanced", event="subscriptions.rebalanced",
                     added=len(add), removed=len(drop), total=len(desired))
        return {"added": len(add), "removed": len(drop), "total": len(desired)}

    async def _rebalance_loop(self, interval: float = 30.0) -> None:
        # First pass soon after start: the first index ticks enable the option window.
        await asyncio.sleep(2.0)
        while True:
            try:
                await self.rebalance_subscriptions()
            except Exception as exc:  # noqa: BLE001
                log.error("rebalance failed", event="subscriptions.rebalance_failed", error=str(exc))
            await asyncio.sleep(interval)

    def _strike_step(self, underlying: str, expiry) -> float:  # noqa: ANN001
        key = (underlying, expiry)
        step = self._step_cache.get(key)
        if step is None:
            strikes = self.registry.strikes(underlying, expiry)
            diffs = [b - a for a, b in zip(strikes, strikes[1:]) if b > a]
            # Smallest listed gap, from the broker's instrument master rather than specs.py.
            step = min(diffs) if diffs else float(get_spec(underlying).strike_interval)
            self._step_cache[key] = step
        return step

    # ---- history ------------------------------------------------------------------------
    async def _backfill_history(self) -> None:
        """Fill candle gaps for indices/futures from the provider's history API (PRD M2 item 5)."""
        days = self.settings.history_backfill_days
        if self.provider.is_simulated or days <= 0 or not self.sessionmaker:
            return
        now = self.now()
        for inst in self._core_instruments():
            inst_id = self.token_to_id.get(inst.token)
            if inst_id is None:
                continue
            for tf in self.candles.timeframes:
                try:
                    n = await self.backfill_instrument(inst, tf, now - timedelta(days=days), now)
                    if n:
                        log.info("history backfilled", event="history.backfilled", symbol=inst.symbol,
                                 timeframe=tf, candles=n)
                except ProviderAuthError as exc:
                    log.warning("history backfill skipped", event="history.backfill_skipped", error=str(exc))
                    return  # same failure for every request; don't repeat it
                except Exception as exc:  # noqa: BLE001 - history is best effort; live data matters more
                    log.warning("history backfill failed", event="history.backfill_failed",
                                symbol=inst.symbol, timeframe=tf, error=str(exc))

    async def backfill_instrument(self, inst: Instrument, timeframe: str, start: datetime, end: datetime) -> int:
        """Fetch [max(start, last stored), end) and upsert closed candles. Returns candles stored."""
        assert self.sessionmaker
        inst_id = self.token_to_id[inst.token]
        async with self.sessionmaker() as s:
            last = await repo.get_candles(s, inst_id, timeframe, limit=1)
        if last:
            last_ts = last[0].ts if last[0].ts.tzinfo else last[0].ts.replace(tzinfo=timezone.utc)
            start = max(start, last_ts)
        if start >= end:
            return 0
        candles = await self.provider.history(inst, timeframe, start, end)
        tf_len = TIMEFRAMES[timeframe]
        closed = [c for c in candles if c.ts + tf_len <= end]
        if not closed:
            return 0
        async with self.sessionmaker() as s, s.begin():
            n = await repo.upsert_candles(s, closed, self.token_to_id)
        self.writes["candles"] += n
        return n

    async def _backfill_simulated(self) -> None:
        minutes = self.settings.sim_backfill_minutes
        backfill = getattr(self.provider, "backfill", None)
        if not (self.provider.is_simulated and backfill and minutes > 0 and self.sessionmaker):
            return
        idx = self.registry.index(next(iter(UNDERLYINGS)))
        if idx is None or idx.token not in self.token_to_id:
            return
        async with self.sessionmaker() as s, s.begin():
            if await repo.get_candles(s, self.token_to_id[idx.token], "1m", limit=1):
                return  # history already exists; never overwrite it
            candles = backfill(minutes, self.candles.timeframes)
            n = await repo.upsert_candles(s, candles, self.token_to_id)
            await repo.record_event(s, "collector", "sim.backfilled", {"minutes": minutes, "candles": n})
        log.info("simulated history backfilled", event="sim.backfilled", candles=n, minutes=minutes)

    def session_date(self, now: datetime | None = None):  # noqa: ANN201
        """The session quotes belong to: today once the market has opened, else the last trading day."""
        local = (now or self.now()).astimezone(IST)
        d = local.date()
        if self.calendar.is_trading_day(d) and local.time() >= dtime(9, 15):
            return d
        return self.calendar.previous_trading_day(d)

    async def refresh_reference_closes(self) -> None:
        """Previous-session close per index/future, so day change is right even on holidays
        (the feed's own prev_close equals the last close when the exchange is shut)."""
        if not self.sessionmaker:
            return
        start = datetime.combine(self.session_date(), dtime(0, 0), tzinfo=IST).astimezone(timezone.utc)
        refs: dict[str, float] = {}
        async with self.sessionmaker() as s:
            for inst in self._core_instruments():
                iid = self.token_to_id.get(inst.token)
                if iid is None:
                    continue
                for tf in ("1m", "5m"):
                    rows = await repo.get_candles(s, iid, tf, limit=1, end=start)
                    if rows:
                        refs[inst.token] = rows[-1].close
                        break
        self.ref_prev_close = refs
        self._ref_session = self.session_date()

    async def stop(self) -> None:
        await self.provider.disconnect()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._candle_buffer.extend(self.candles.flush())
        await self._flush()
        log.info("collector stopped", event="collector.stopped")

    # ---- ingestion ----------------------------------------------------------------
    async def _consume(self) -> None:
        try:
            async for tick in self.provider.stream():
                self.ingest(tick)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - e.g. expired token: needs a human, don't spin
            self.provider.health.connected = False
            self.provider.health.last_error = f"{type(exc).__name__}: {exc}"
            log.error("feed stopped", event="collector.feed_stopped", error=self.provider.health.last_error)

    def ingest(self, tick: Tick) -> bool:
        now = self.now()
        verdict = self.quality.check(tick, now)
        if not verdict.accept:
            return False
        self.latest[tick.token] = tick
        self._tick_times.append(time.monotonic())

        if tick.token in self._candle_tokens:
            closed = self.candles.update(tick.token, tick.ts, tick.ltp, tick.volume)
            for c in closed:
                self._candle_buffer.append(c)
                self.bus.publish(TOPIC_CANDLES, c)
        if self._should_store(tick):
            self._tick_buffer.append(dict(
                ts=tick.ts, instrument_id=self.token_to_id.get(tick.token), ltp=tick.ltp,
                bid=tick.bid, ask=tick.ask, volume=tick.volume, oi=tick.oi))
        self.bus.publish(TOPIC_TICKS, tick)
        return True

    def _should_store(self, tick: Tick) -> bool:
        if not self.settings.store_ticks or tick.token not in self.token_to_id:
            return False  # candles and option snapshots are kept either way; backtests only need those
        inst = self.registry.by_token(tick.token)
        if inst is None or not inst.is_option:
            return True
        # Options: only store ticks near the money (PRD §14 — full chain goes to snapshots).
        spot = self.spot(inst.underlying)
        if spot is None or inst.strike is None:
            return False
        step = self._strike_step(inst.underlying, inst.expiry)
        return abs(inst.strike - spot) <= step * (self.settings.tick_store_strike_window + 0.5)

    # ---- persistence ----------------------------------------------------------------
    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            now = self.now()
            for c in self.candles.close_due(now):
                self._candle_buffer.append(c)
                self.bus.publish(TOPIC_CANDLES, c)
            for token in self.quality.sweep_stale(now):
                log.warning("stale feed", event="data.stale", token=token)
            if getattr(self, "_ref_session", None) != self.session_date(now):
                await self.refresh_reference_closes()
            if time.monotonic() - self._last_snapshot >= self.settings.option_snapshot_interval_seconds:
                self._last_snapshot = time.monotonic()
                await self._snapshot_chains(now)
            await self._flush()

    async def _flush(self) -> None:
        if not self.sessionmaker or not (self._tick_buffer or self._candle_buffer):
            self._tick_buffer.clear()
            self._candle_buffer.clear()
            return
        ticks, self._tick_buffer = self._tick_buffer, []
        candles, self._candle_buffer = self._candle_buffer, []
        try:
            async with self.sessionmaker() as s, s.begin():
                if ticks:
                    await repo.insert_ticks(s, ticks)
                if candles:
                    await repo.upsert_candles(s, candles, self.token_to_id)
            self.writes["ticks"] += len(ticks)
            self.writes["candles"] += len(candles)
        except Exception as exc:  # noqa: BLE001 - never let storage kill the feed
            self.writes["errors"] += 1
            log.error("db flush failed", event="db.flush_failed", error=str(exc), ticks=len(ticks))

    async def _snapshot_chains(self, now: datetime) -> None:
        if not self.sessionmaker:
            return
        rows: list[dict] = []
        for und in UNDERLYINGS:
            for exp in self.registry.expiries(und, now):
                chain = self.chain(und, exp)
                for r in chain.rows:
                    for ot, leg in (("CE", r.ce), ("PE", r.pe)):
                        if leg is None or leg.ltp is None:
                            continue
                        rows.append(dict(ts=now, underlying=und, expiry=exp, strike=r.strike, option_type=ot,
                                         spot=chain.spot, ltp=leg.ltp, bid=leg.bid, ask=leg.ask, oi=leg.oi,
                                         oi_change=leg.oi_change, volume=leg.volume, iv=leg.iv, delta=leg.delta))
        if not rows:
            return
        try:
            async with self.sessionmaker() as s, s.begin():
                await repo.insert_option_snapshots(s, rows)
            self.writes["option_snapshots"] += len(rows)
        except Exception as exc:  # noqa: BLE001
            self.writes["errors"] += 1
            log.error("snapshot failed", event="db.snapshot_failed", error=str(exc))

    # ---- read side -------------------------------------------------------------------
    def spot(self, underlying: str) -> float | None:
        inst = self.registry.index(underlying)
        tick = self.latest.get(inst.token) if inst else None
        return tick.ltp if tick else None

    def quote(self, symbol: str) -> Quote | None:
        inst = self.registry.index(symbol) if symbol in UNDERLYINGS or symbol == VIX_SYMBOL \
            else self.registry.by_symbol(symbol)
        if inst is None:
            return None
        t = self.latest.get(inst.token)
        if t is None:
            return None
        prev = self.ref_prev_close.get(inst.token) or t.prev_close
        change = t.ltp - prev if prev else None
        pct = change / prev * 100 if change is not None and prev else None
        return Quote(inst.symbol, inst.token, t.ltp, change, pct, t.open, t.high, t.low, prev, t.ts)

    def chain(self, underlying: str, expiry, strike_window: int | None = None) -> OptionChain:
        return build_chain(
            underlying, expiry, self.registry.options_for_expiry(underlying, expiry), self.latest.get,
            self.spot(underlying), self.now(), self.settings.risk_free_rate, strike_window,
        )

    def ticks_per_second(self, window: float = 5.0) -> float:
        cutoff = time.monotonic() - window
        n = sum(1 for t in reversed(self._tick_times) if t >= cutoff)
        return round(n / window, 1)

    def candle_instrument(self, symbol: str) -> Instrument | None:
        """Candles are built for indices and futures. `NIFTY` -> index, `NIFTY-FUT` -> near future."""
        if symbol.endswith("-FUT"):
            return self.registry.future(symbol[:-4])
        if symbol in UNDERLYINGS or symbol == VIX_SYMBOL:
            return self.registry.index(symbol)
        return self.registry.by_symbol(symbol)
