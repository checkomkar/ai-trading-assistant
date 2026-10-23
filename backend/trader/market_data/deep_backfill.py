"""Reach further back than what is stored (PRD §24.11: a paid data plan with years of history).

`MarketDataService.backfill_instrument` only resumes *forward* from the last stored candle, so once a year
is stored a longer `--days` fetches nothing older. This walks backwards from the first stored candle in
broker-sized windows, newest first, storing each window as it arrives, so an interrupted run keeps what it
got and a re-run continues from the new first candle.

It stops by itself where the broker's history ends: a window that comes back empty (no 90-day stretch of
the market is closed) or refused twice in a row is taken as the start of the available history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from sqlalchemy import select

from trader.db import repository as repo
from trader.db.models import CandleRow
from trader.domain import Instrument
from trader.market_data.candles import TIMEFRAMES

WINDOW = timedelta(days=90)  # under Fyers' 100-day limit per intraday request


@dataclass
class DeepResult:
    stored: int = 0
    windows: int = 0
    earliest: datetime | None = None  # first stored candle after the run
    reached_start: bool = False  # True: got back to the requested start; False: the broker's history ended first
    note: str = ""


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


async def first_stored(sm, instrument_id: int, timeframe: str) -> datetime | None:  # noqa: ANN001
    async with sm() as s:
        ts = (await s.execute(select(CandleRow.ts).where(CandleRow.instrument_id == instrument_id,
                                                        CandleRow.timeframe == timeframe)
                              .order_by(CandleRow.ts).limit(1))).scalar_one_or_none()
    return _utc(ts) if ts else None


async def backfill_older(history: Callable[[Instrument, str, datetime, datetime], Awaitable[list]], sm,  # noqa: ANN001
                         inst: Instrument, instrument_id: int, timeframe: str, start: datetime, now: datetime, *,
                         window: timedelta = WINDOW, retries: int = 1,
                         say: Callable[[str], None] = print) -> DeepResult:
    """Fetch [start, first stored candle) backwards. With nothing stored yet, starts from `now`."""
    res = DeepResult()
    cur = await first_stored(sm, instrument_id, timeframe) or now
    tf_len = TIMEFRAMES[timeframe]
    while cur > start:
        lo = max(start, cur - window)
        candles = None
        for attempt in range(retries + 1):
            try:
                candles = await history(inst, timeframe, lo, cur)
                break
            except Exception as exc:  # noqa: BLE001 - beyond the plan's depth Fyers answers "Invalid input"
                if attempt == retries:
                    res.note = f"refused before {cur:%Y-%m-%d}: {str(exc)[:120]}"
        res.windows += 1
        closed = [c for c in candles or [] if c.ts + tf_len <= now and c.ts < cur]
        if not closed:
            if lo <= start and not res.note:  # the last window, up to the requested start, was simply empty
                res.reached_start = True
            else:
                res.note = res.note or f"no data before {cur:%Y-%m-%d}"
            break
        async with sm() as s, s.begin():
            res.stored += await repo.upsert_candles(s, closed, {inst.token: instrument_id})
        say(f"  {inst.symbol:<16} {timeframe}: {closed[0].ts:%Y-%m-%d} .. {closed[-1].ts:%Y-%m-%d} "
            f"+{len(closed)} candles")
        cur = min(c.ts for c in closed)
        if lo <= start:
            res.reached_start = True
            break
    res.earliest = await first_stored(sm, instrument_id, timeframe)
    return res
