"""Audit trail and paper-trade journal persistence (PRD §9.3, §14.4).

`emit` and `add_trade` are synchronous and cheap; they buffer. `flush` writes the buffer in one
transaction. Without a database (unit tests) everything stays in memory, so the same code paths run.
Every record shares the trade's `correlation_id`: signal -> risk decision -> order -> fill -> exit.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.db.models import EventRow, PaperTradeRow
from trader.logging import get_logger

log = get_logger("paper")

SERVICE = "paper"


class PaperStore:
    def __init__(self, sessionmaker: async_sessionmaker | None = None,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        self.sessionmaker = sessionmaker
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self._events: list[dict] = []
        self._trades: list[dict] = []
        self.events: list[dict] = []  # everything emitted this process (bounded for memory)
        self.trades: list[dict] = []
        self.recent: deque[dict] = deque(maxlen=300)
        self.write_errors = 0

    def emit(self, event_type: str, correlation_id: str | None, payload: dict[str, Any]) -> None:
        ev = {"ts": self.now(), "service": SERVICE, "event_type": event_type,
              "correlation_id": correlation_id, "payload": payload}
        self._events.append(ev)
        self.events.append(ev)
        del self.events[:-5000]
        self.recent.append({**ev, "ts": ev["ts"].isoformat()})

    def add_trade(self, trade: dict) -> None:
        self._trades.append(trade)
        self.trades.append(trade)

    async def flush(self) -> None:
        if not self.sessionmaker or not (self._events or self._trades):
            self._events.clear()
            self._trades.clear()
            return
        events, self._events = self._events, []
        trades, self._trades = self._trades, []
        try:
            async with self.sessionmaker() as s, s.begin():
                s.add_all(EventRow(**e) for e in events)
                s.add_all(PaperTradeRow(**t) for t in trades)
        except Exception as exc:  # noqa: BLE001 - the audit write must never kill the runner
            self.write_errors += 1
            self._events = events + self._events  # keep for the next attempt
            self._trades = trades + self._trades
            log.error("paper store flush failed", event="paper.flush_failed", error=str(exc))


async def load_trades(s, *, day: str | None = None, symbol: str | None = None, strategy: str | None = None,
                      direction: str | None = None, result: str | None = None, underlying: str | None = None,
                      data_source: str | None = None, limit: int = 500) -> list[PaperTradeRow]:
    q = select(PaperTradeRow)
    if day:
        q = q.where(PaperTradeRow.trading_day == day)
    if underlying:
        q = q.where(PaperTradeRow.underlying == underlying.upper())
    if symbol:
        q = q.where(PaperTradeRow.symbol.like(f"%{symbol.upper()}%"))
    if strategy:
        q = q.where(PaperTradeRow.strategy == strategy)
    if direction:
        q = q.where(PaperTradeRow.direction == direction.upper())
    if data_source:
        q = q.where(PaperTradeRow.data_source == data_source)
    if result == "win":
        q = q.where(PaperTradeRow.net_pnl > 0)
    elif result == "loss":
        q = q.where(PaperTradeRow.net_pnl <= 0)
    q = q.order_by(PaperTradeRow.exit_time.desc()).limit(limit)
    return list((await s.execute(q)).scalars().all())
