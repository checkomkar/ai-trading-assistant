"""Audit trail and paper-trade journal persistence (PRD §9.3, §14.4).

`emit` and `add_trade` are synchronous and cheap; they buffer. `flush` writes the buffer in one
transaction. Without a database (unit tests) everything stays in memory, so the same code paths run.
Every record shares the trade's `correlation_id`: signal -> risk decision -> order -> fill -> exit.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.db.models import EventRow, PaperStateRow, PaperTradeRow
from trader.logging import get_logger

log = get_logger("paper")

SERVICE = "paper"
STATE_KEY = "paper-{}"  # one snapshot per data source: a replay can never overwrite the live one


class PaperStore:
    def __init__(self, sessionmaker: async_sessionmaker | None = None,
                 now_fn: Callable[[], datetime] | None = None, *, persist_state: bool = True) -> None:
        self.sessionmaker = sessionmaker
        self.persist_state = persist_state
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self._events: list[dict] = []
        self._trades: list[dict] = []
        self.events: list[dict] = []  # everything emitted this process (bounded for memory)
        self.trades: list[dict] = []
        self.recent: deque[dict] = deque(maxlen=300)
        self.write_errors = 0
        self._state: dict | None = None  # latest snapshot waiting for the next flush
        self._state_sig: str | None = None  # signature of the last snapshot set, to skip no-op writes
        self.state: dict | None = None  # last snapshot set in this process (what load_state returns without a DB)

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

    def set_state(self, trading_day: str, data_source: str, state: dict) -> None:
        """Buffer the paper-account snapshot (PRD §22.3). Unchanged snapshots are not rewritten; the write
        goes out with the next `flush`, in the same transaction as the events that explain it."""
        if not self.persist_state:
            return
        sig = json.dumps([trading_day, data_source, state], sort_keys=True, default=str)
        if sig == self._state_sig:
            return
        self._state_sig = sig
        self._state = {"key": STATE_KEY.format(data_source), "trading_day": trading_day, "saved_at": self.now(),
                       "data_source": data_source, "state": state}
        self.state = self._state

    async def load_state(self, data_source: str) -> dict | None:
        """The stored snapshot for this data source, or None. Reads the database when there is one."""
        if self.sessionmaker is None:
            return self.state if self.state and self.state["data_source"] == data_source else None
        async with self.sessionmaker() as s:
            row = await s.get(PaperStateRow, STATE_KEY.format(data_source))
        if row is None:
            return None
        return {"key": row.key, "trading_day": row.trading_day, "saved_at": row.saved_at,
                "data_source": row.data_source, "state": row.state}

    async def flush(self) -> None:
        if not self.sessionmaker or not (self._events or self._trades or self._state):
            self._events.clear()
            self._trades.clear()
            self._state = None
            return
        events, self._events = self._events, []
        trades, self._trades = self._trades, []
        state, self._state = self._state, None
        try:
            async with self.sessionmaker() as s, s.begin():
                s.add_all(EventRow(**e) for e in events)
                s.add_all(PaperTradeRow(**t) for t in trades)
                if state is not None:
                    await s.merge(PaperStateRow(**state))
        except Exception as exc:  # noqa: BLE001 - the audit write must never kill the runner
            self.write_errors += 1
            self._events = events + self._events  # keep for the next attempt
            self._trades = trades + self._trades
            if self._state is None:
                self._state = state
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
