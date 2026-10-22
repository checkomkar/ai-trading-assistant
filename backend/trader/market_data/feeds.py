"""One live market-data feed per customer, built from the broker keys and the day's sign-in they saved on the Brokers page.

A customer's feed lives only in memory (no ticks or candles are written to the shared database), starts when they first open
the market, and is stopped after a period of no use. Nothing is read from `.env`: the app id, secret and token are the
customer's own, so each customer is on their own broker's data-access terms and symbol limits.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select

from trader.accounts import vault
from trader.brokers.fyers import auth as fyers_auth
from trader.config import Settings
from trader.db.models import BrokerConnectionRow
from trader.logging import get_logger
from trader.market_data.service import MarketDataService

log = get_logger("feeds")


class FeedUnavailable(Exception):
    """The customer cannot have a live feed right now. `code` tells the UI what to offer; `message` is safe to show."""

    code = "feed_unavailable"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NoBroker(FeedUnavailable):
    code = "broker_required"


class SignInNeeded(FeedUnavailable):
    code = "signin_required"


class Busy(FeedUnavailable):
    code = "feed_busy"


@dataclass
class _Feed:
    md: MarketDataService
    holder: dict  # {"token": str}: the provider reads it on every connect, so a new sign-in only needs this swapped
    client_id: str
    last_used: float = field(default_factory=time.monotonic)
    history_task: asyncio.Task | None = None
    broker: str = "fyers"


def default_provider(settings: Settings, holder: dict):  # noqa: ANN201
    if settings.market_data_provider == "shoonya":
        from trader.market_data.shoonya import ShoonyaMarketDataProvider

        return ShoonyaMarketDataProvider(settings, token_provider=lambda: holder.get("token"))
    from trader.brokers.fyers.provider import FyersMarketDataProvider

    return FyersMarketDataProvider(settings, token_provider=lambda: holder.get("token"))


class FeedManager:
    def __init__(self, settings: Settings, sessionmaker, provider_factory: Callable = default_provider,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.factory = provider_factory
        self.clock = clock
        self._feeds: dict[str, _Feed] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._reaper: asyncio.Task | None = None

    # ---- lookup ---------------------------------------------------------------------------
    async def md_for(self, owner: str) -> MarketDataService:
        """This customer's feed, started if needed. Raises FeedUnavailable (with a code the UI understands) when it cannot."""
        feed = self._feeds.get(owner)
        if feed is not None:
            if _feed_expired(feed.broker, feed.holder):
                await self.drop(owner)
                raise SignInNeeded(f"Your {BROKER_NAMES[feed.broker]} sign-in has expired. Sign in again on the Brokers page.")
            feed.last_used = self.clock()
            return feed.md
        lock = self._locks.setdefault(owner, asyncio.Lock())
        async with lock:
            if owner in self._feeds:  # another request started it while we waited
                return await self.md_for(owner)
            return await self._start(owner)

    def touch(self, owner: str) -> None:
        if (f := self._feeds.get(owner)) is not None:
            f.last_used = self.clock()

    async def _start(self, owner: str) -> MarketDataService:
        broker, keys = await self._keys(owner)
        token = keys.get("access_token")
        if _keys_expired(broker, keys):
            raise SignInNeeded(f"Sign in to {BROKER_NAMES[broker]} on the Brokers page to start your live data.")
        if len(self._feeds) >= self.settings.max_user_feeds:
            raise Busy("Live data is busy right now. Try again in a few minutes.")
        holder = {"token": token, "expires_at": _parse_expiry(keys.get("expires_at"))}
        if broker == "shoonya":
            st = self.settings.model_copy(update={"market_data_provider": "shoonya", "shoonya_user_id": keys["user_id"]})
        else:
            st = self.settings.model_copy(update={"market_data_provider": "fyers", "fyers_client_id": keys["client_id"],
                                                  "fyers_secret_key": keys["secret_key"]})
        md = MarketDataService(self.factory(st, holder), None, st)  # no sessionmaker: nothing of theirs is stored
        try:
            await md.start()
        except Exception as exc:  # noqa: BLE001
            log.warning("feed start failed", event="feed.start_failed", owner=owner, error=str(exc))
            try:
                await md.stop()
            except Exception:  # noqa: BLE001
                pass
            raise FeedUnavailable(f"Could not start your live data: {exc}") from exc
        feed = _Feed(md, holder, keys.get("client_id") or keys.get("user_id", ""), self.clock(), broker=broker)
        feed.history_task = asyncio.create_task(md.load_recent_history(st.feed_history_days))
        self._feeds[owner] = feed
        self._ensure_reaper()
        log.info("feed started", event="feed.started", owner=owner, active=len(self._feeds))
        return md

    async def _keys(self, owner: str) -> tuple[str, dict]:
        """(broker, keys) to run this customer's feed on: the connected broker that is signed in today, else the first one
        (so the message names the broker that needs a sign-in)."""
        async with self.sessionmaker() as s:
            rows = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == owner,
                                                                       BrokerConnectionRow.broker.in_(tuple(BROKER_NAMES)))
                                    .order_by(BrokerConnectionRow.created_at))).scalars().all()
        if not rows:
            raise NoBroker("Connect your broker on the Brokers page to see live data.")
        opened: list[tuple[str, dict]] = []
        for row in rows:
            try:
                opened.append((row.broker, vault.unseal(row.secret_blob, self.settings.credential_key)))
            except Exception:  # noqa: BLE001 - key changed or vault off: treat as not connected
                continue
        if not opened:
            raise NoBroker("Your saved broker keys can no longer be read. Add them again on the Brokers page.")
        return next(((b, k) for b, k in opened if not _keys_expired(b, k)), opened[0])

    # ---- changes from the Brokers page ------------------------------------------------------
    async def refresh(self, owner: str, token: str, expires_at: datetime | None = None) -> None:
        """The customer signed in again: give the running feed the new token and reconnect it."""
        feed = self._feeds.get(owner)
        if feed is None:
            return
        feed.holder["token"] = token
        if expires_at is not None:
            feed.holder["expires_at"] = expires_at
        await feed.md.restart()

    async def drop(self, owner: str) -> None:
        feed = self._feeds.pop(owner, None)
        if feed is None:
            return
        if feed.history_task is not None:
            feed.history_task.cancel()
        try:
            await feed.md.stop()
        except Exception as exc:  # noqa: BLE001
            log.warning("feed stop failed", event="feed.stop_failed", owner=owner, error=str(exc))
        log.info("feed stopped", event="feed.stopped", owner=owner, active=len(self._feeds))

    # ---- housekeeping -----------------------------------------------------------------------
    def _ensure_reaper(self) -> None:
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_loop(), name="feed-reaper")

    async def reap(self) -> int:
        """Stop feeds nobody has used for `feed_idle_seconds`. Returns how many were stopped."""
        now = self.clock()
        idle = [o for o, f in self._feeds.items() if now - f.last_used > self.settings.feed_idle_seconds]
        for owner in idle:
            await self.drop(owner)
        return len(idle)

    async def _reap_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            await self.reap()

    async def stop_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
        for owner in list(self._feeds):
            await self.drop(owner)

    def active(self) -> int:
        return len(self._feeds)


BROKER_NAMES = {"fyers": "Fyers", "shoonya": "Shoonya"}  # brokers with a live-data adapter


def _parse_expiry(value: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def _keys_expired(broker: str, keys: dict) -> bool:
    if not keys.get("access_token"):
        return True
    if broker == "shoonya":  # an opaque session token: the expiry was saved with it
        exp = _parse_expiry(keys.get("expires_at"))
        return exp is None or exp <= datetime.now(timezone.utc)
    return _expired(keys["access_token"])


def _feed_expired(broker: str, holder: dict) -> bool:
    if broker == "shoonya":
        exp = holder.get("expires_at")
        return exp is None or exp <= datetime.now(timezone.utc)
    return _expired(holder.get("token"))


def _expired(token: str | None) -> bool:
    if not token:
        return True
    exp = fyers_auth.jwt_expiry(token)
    return exp is not None and exp - fyers_auth.EXPIRY_MARGIN <= datetime.now(timezone.utc)
