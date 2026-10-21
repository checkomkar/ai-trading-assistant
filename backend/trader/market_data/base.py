"""`MarketDataProvider` interface (PRD §1.1, §1.2, §12).

Concrete providers implement `_connect`, `_disconnect`, `_stream` and instrument
discovery. The base class owns subscription state, reconnect with exponential backoff,
heartbeat bookkeeping and health reporting, so every provider behaves the same way.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Iterable

from trader.domain import Instrument, Tick
from trader.logging import get_logger
from trader.market_data.candles import Candle

log = get_logger("market_data")


class ProviderAuthError(RuntimeError):
    """Credentials missing/expired/rejected. Retrying cannot help; a human must log in."""


@dataclass
class ProviderHealth:
    connected: bool = False
    connected_since: datetime | None = None
    last_tick_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    reconnects: int = 0
    ticks_received: int = 0
    last_error: str | None = None
    subscribed: int = 0


@dataclass(frozen=True)
class SubscriptionPolicy:
    """Which option contracts to stream (PRD §17.3). `max_symbols=None` means unlimited."""

    strikes_each_side: int = 20
    expiries_per_underlying: int = 2
    max_symbols: int | None = None


@dataclass
class ReconnectPolicy:
    initial_delay: float = 1.0
    max_delay: float = 30.0
    factor: float = 2.0


class MarketDataProvider(ABC):
    name: str = "base"
    is_simulated: bool = False
    has_own_clock: bool = False  # True for replay: `clock_now()` replaces the wall clock
    persist_market_data: bool = True  # False for replays: they must not write into the real candle tables
    option_pricing: str = "live quotes"  # how option prices are produced; paper journals record it

    def __init__(
        self,
        reconnect: ReconnectPolicy | None = None,
        subscription: SubscriptionPolicy | None = None,
    ) -> None:
        self.health = ProviderHealth()
        self._policy = reconnect or ReconnectPolicy()
        self.subscription_policy = subscription or SubscriptionPolicy()
        self._subscribed: set[str] = set()
        self._closing = False

    def clock_now(self) -> datetime:
        """The provider's own clock (only called when `has_own_clock`)."""
        return datetime.now(timezone.utc)

    # ---- to implement -------------------------------------------------------
    @abstractmethod
    async def discover_instruments(self) -> list[Instrument]:
        """Return indices, futures and option contracts available for the configured underlyings."""

    @abstractmethod
    async def _connect(self) -> None: ...

    @abstractmethod
    async def _disconnect(self) -> None: ...

    @abstractmethod
    def _stream(self) -> AsyncIterator[Tick]:
        """Yield ticks for subscribed tokens. Raise ConnectionError when the feed drops."""

    async def history(
        self, instrument: Instrument, timeframe: str, start: datetime, end: datetime
    ) -> list[Candle]:
        """Closed candles in [start, end). Providers without a history API return []."""
        return []

    async def snapshot(self, instruments: list[Instrument]) -> list[Tick]:
        """Current quotes via REST, used to seed state before (or outside) streaming."""
        return []

    async def _on_subscribe(self, tokens: set[str]) -> None:  # noqa: B027 - optional hook
        """Send a subscribe frame to the broker. Default: nothing (subscription is local)."""

    async def _on_unsubscribe(self, tokens: set[str]) -> None:  # noqa: B027 - optional hook
        """Send an unsubscribe frame to the broker."""

    # ---- public API (PRD §1.2) ---------------------------------------------
    async def connect(self) -> None:
        await self._connect()
        self.health.connected = True
        self.health.connected_since = datetime.now(timezone.utc)
        self.health.last_error = None
        log.info("provider connected", event="provider.connected", provider=self.name)

    async def disconnect(self) -> None:
        self._closing = True
        if self.health.connected:
            await self._disconnect()
        self.health.connected = False
        log.info("provider disconnected", event="provider.disconnected", provider=self.name)

    async def subscribe(self, tokens: Iterable[str]) -> None:
        new = set(tokens) - self._subscribed
        if not new:
            return
        self._subscribed |= new
        self.health.subscribed = len(self._subscribed)
        if self.health.connected:
            await self._on_subscribe(new)

    async def unsubscribe(self, tokens: Iterable[str]) -> None:
        gone = set(tokens) & self._subscribed
        if not gone:
            return
        self._subscribed -= gone
        self.health.subscribed = len(self._subscribed)
        if self.health.connected:
            await self._on_unsubscribe(gone)

    async def reconnect(self) -> None:
        try:
            await self._disconnect()
        except Exception:  # noqa: BLE001 - best effort, the connection is already broken
            pass
        self.health.connected = False
        await self.connect()
        if self._subscribed:
            await self._on_subscribe(set(self._subscribed))

    def reopen(self) -> None:
        """Allow a provider that was shut down (or gave up on bad credentials) to connect again."""
        self._closing = False
        self.health.last_error = None

    def heartbeat(self) -> None:
        self.health.last_heartbeat_at = datetime.now(timezone.utc)

    @property
    def subscribed(self) -> frozenset[str]:
        return frozenset(self._subscribed)

    async def stream(self) -> AsyncIterator[Tick]:
        """Resilient tick stream: reconnects with exponential backoff until `disconnect()`."""
        delay = self._policy.initial_delay
        while not self._closing:
            try:
                if not self.health.connected:
                    await self.reconnect()
                async for tick in self._stream():
                    delay = self._policy.initial_delay
                    self.health.ticks_received += 1
                    self.health.last_tick_at = tick.ts
                    yield tick
                    if self._closing:
                        return
                if self._closing:
                    return
                raise ConnectionError("stream ended unexpectedly")
            except (ConnectionError, OSError, asyncio.TimeoutError) as exc:
                if self._closing:
                    return
                self.health.connected = False
                self.health.reconnects += 1
                self.health.last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "feed dropped, reconnecting",
                    event="provider.reconnecting",
                    provider=self.name,
                    delay_s=delay,
                    error=self.health.last_error,
                )
                await asyncio.sleep(delay)
                delay = min(delay * self._policy.factor, self._policy.max_delay)
