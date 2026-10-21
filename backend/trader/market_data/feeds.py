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


def default_provider(settings: Settings, holder: dict):  # noqa: ANN201
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
            if _expired(feed.holder.get("token")):
                await self.drop(owner)
                raise SignInNeeded("Your Fyers sign-in has expired. Sign in again on the Brokers page.")
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
        keys = await self._keys(owner)
        token = keys.get("access_token")
        if _expired(token) or not token:
            raise SignInNeeded("Sign in to Fyers on the Brokers page to start your live data.")
        if len(self._feeds) >= self.settings.max_user_feeds:
            raise Busy("Live data is busy right now. Try again in a few minutes.")
        holder = {"token": token}
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
        feed = _Feed(md, holder, keys["client_id"], self.clock())
        feed.history_task = asyncio.create_task(md.load_recent_history(st.feed_history_days))
        self._feeds[owner] = feed
        self._ensure_reaper()
        log.info("feed started", event="feed.started", owner=owner, active=len(self._feeds))
        return md

    async def _keys(self, owner: str) -> dict:
        async with self.sessionmaker() as s:
            row = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == owner,
                                                                      BrokerConnectionRow.broker == "fyers"))).scalars().first()
        if row is None:
            raise NoBroker("Connect your broker on the Brokers page to see live data.")
        try:
            return vault.unseal(row.secret_blob, self.settings.credential_key)
        except Exception as exc:  # noqa: BLE001 - key changed or vault off: treat as not connected
            raise NoBroker("Your saved broker keys can no longer be read. Add them again on the Brokers page.") from exc

    # ---- changes from the Brokers page ------------------------------------------------------
    async def refresh(self, owner: str, token: str) -> None:
        """The customer signed in again: give the running feed the new token and reconnect it."""
        feed = self._feeds.get(owner)
        if feed is None:
            return
        feed.holder["token"] = token
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


def _expired(token: str | None) -> bool:
    if not token:
        return True
    exp = fyers_auth.jwt_expiry(token)
    return exp is not None and exp - fyers_auth.EXPIRY_MARGIN <= datetime.now(timezone.utc)
