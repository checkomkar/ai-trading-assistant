"""Data-access functions. Works on PostgreSQL and SQLite."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession

from trader.db.models import CandleRow, EventRow, InstrumentRow, MarketTickRow, OptionSnapshotRow
from trader.domain import Instrument
from trader.market_data.candles import Candle


def _insert(session: AsyncSession, table: Any):
    dialect = session.bind.dialect.name if session.bind else "sqlite"
    return (postgresql.insert if dialect == "postgresql" else sqlite.insert)(table)


async def upsert_instruments(
    session: AsyncSession, instruments: Iterable[Instrument], is_active: bool = True
) -> dict[str, int]:
    """Insert/update instruments by token. Returns token -> id."""
    rows = [
        dict(token=i.token, symbol=i.symbol, underlying=i.underlying, exchange=i.exchange,
             instrument_type=i.instrument_type.value, expiry=i.expiry, strike=i.strike,
             option_type=i.option_type.value if i.option_type else None,
             lot_size=i.lot_size, tick_size=i.tick_size, is_active=is_active)
        for i in instruments
    ]
    if rows:
        for chunk in _chunks(rows, 500):
            stmt = _insert(session, InstrumentRow).values(chunk)
            stmt = stmt.on_conflict_do_update(
                index_elements=["token"],
                set_={c: stmt.excluded[c] for c in ("symbol", "lot_size", "tick_size", "is_active", "expiry")},
            )
            await session.execute(stmt)
    result = await session.execute(select(InstrumentRow.token, InstrumentRow.id))
    return {t: i for t, i in result.all()}


async def insert_ticks(session: AsyncSession, rows: Sequence[dict]) -> None:
    for chunk in _chunks(list(rows), 1000):
        await session.execute(_insert(session, MarketTickRow).values(chunk))


async def upsert_candles(session: AsyncSession, candles: Iterable[Candle], token_to_id: dict[str, int]) -> int:
    rows = [
        dict(instrument_id=token_to_id[c.token], timeframe=c.timeframe, ts=c.ts,
             open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume, oi=c.oi)
        for c in candles if c.token in token_to_id
    ]
    for chunk in _chunks(rows, 500):
        stmt = _insert(session, CandleRow).values(chunk)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "timeframe", "ts"],
            set_={c: stmt.excluded[c] for c in ("open", "high", "low", "close", "volume", "oi")},
        )
        await session.execute(stmt)
    return len(rows)


async def insert_option_snapshots(session: AsyncSession, rows: Sequence[dict]) -> None:
    for chunk in _chunks(list(rows), 500):
        await session.execute(_insert(session, OptionSnapshotRow).values(chunk))


async def get_candles(
    session: AsyncSession,
    instrument_id: int,
    timeframe: str,
    limit: int = 500,
    end: datetime | None = None,
) -> list[CandleRow]:
    q = select(CandleRow).where(CandleRow.instrument_id == instrument_id, CandleRow.timeframe == timeframe)
    if end is not None:
        q = q.where(CandleRow.ts < end)
    q = q.order_by(CandleRow.ts.desc()).limit(limit)
    rows = (await session.execute(q)).scalars().all()
    return list(reversed(rows))


async def record_event(
    session: AsyncSession, service: str, event_type: str, payload: dict, correlation_id: str | None = None
) -> None:
    session.add(EventRow(service=service, event_type=event_type, payload=payload, correlation_id=correlation_id))


def _chunks(rows: list, size: int):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]
