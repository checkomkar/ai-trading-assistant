"""Stored market history (candles, option snapshots, ticks, instruments, expired-option data) is never deleted by the
application. New data is upserted: a candle with the same instrument, timeframe and time is updated, everything else is added."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from trader.db import repository as repo
from trader.db.models import Base, CandleRow, InstrumentRow, OptionSnapshotRow
from trader.market_data.candles import Candle

MARKET_TABLES = ("CandleRow", "OptionSnapshotRow", "MarketTickRow", "InstrumentRow")
SRC = Path(__file__).resolve().parents[1] / "trader"


def test_no_source_file_deletes_from_a_market_data_table():
    """Static guard: a DELETE/TRUNCATE/DROP naming a market table (or a bulk delete on its Row class) fails the build."""
    bad = []
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for table in MARKET_TABLES:
            if re.search(rf"delete\(\s*{table}\b", text) or re.search(rf"{table}\b[^\n]*\.delete\(", text):
                bad.append(f"{path.relative_to(SRC)}: delete on {table}")
        if re.search(r"(DELETE\s+FROM|TRUNCATE|DROP\s+TABLE)\s+(candles|option_snapshots|market_ticks|instruments)\b", text, re.I):
            bad.append(f"{path.relative_to(SRC)}: raw SQL delete on a market table")
    assert not bad, bad


@pytest.mark.asyncio
async def test_new_candles_are_added_and_a_repeat_updates_in_place_without_losing_history(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    def candle(minute: int, close: float) -> Candle:
        ts = datetime(2026, 10, 20, 4, minute, tzinfo=timezone.utc)
        return Candle("NSE:NIFTY50-INDEX", "1m", ts, close, close, close, close, 0, None)

    async with sm() as s, s.begin():
        s.add(InstrumentRow(token="NSE:NIFTY50-INDEX", symbol="NIFTY", underlying="NIFTY", exchange="NSE",
                            instrument_type="INDEX", lot_size=1, tick_size=0.05))
    async with sm() as s:
        ids = {r.token: r.id for r in (await s.execute(select(InstrumentRow))).scalars()}
    async with sm() as s, s.begin():
        await repo.upsert_candles(s, [candle(0, 100.0), candle(1, 101.0)], ids)  # the history we already fetched
    async with sm() as s, s.begin():
        await repo.upsert_candles(s, [candle(1, 101.5), candle(2, 102.0)], ids)  # a re-fetch plus one new candle
    async with sm() as s:
        rows = (await s.execute(select(CandleRow).order_by(CandleRow.ts))).scalars().all()
        assert [r.close for r in rows] == [100.0, 101.5, 102.0]  # nothing lost, the repeated one updated, the new one added
        assert (await s.execute(select(func.count()).select_from(OptionSnapshotRow))).scalar_one() == 0
    await engine.dispose()
