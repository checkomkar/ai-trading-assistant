"""Reuse a stored backtest when nothing that decides its result has changed.

A run is keyed by everything that goes into it: the request (with the strategy spec resolved, so a saved version and
the same spec pasted in give the same key), the stored market data it would read, the files that shape prices, costs
and contracts, and the engine's own code. Change any of them and the key changes, so the next request runs fresh.

The data part is a cheap fingerprint, not a checksum of every candle: the count and newest timestamp of the
index and futures candles in the run's range (warm-up included), and the number of option contracts stored for its expiries.
New days, a backfilled gap or newly downloaded contracts all move it. A correction that rewrites candles in place
without adding any does not: pass `force` to run again after one.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.backtest import data as bt_data
from trader.config import BACKEND_DIR, CONFIG_DIR
from trader.db.models import BacktestRunRow, CandleRow, InstrumentRow

# Code whose change can change a result. Hashed once per process, so an upgrade invalidates every cached run.
_CODE_DIRS = ("trader/backtest", "trader/strategy", "trader/options", "trader/indicators", "trader/instruments")
_CODE_FILES = ("trader/features.py", "trader/domain.py", "trader/market_data/resample.py")
_CONFIG_FILES = ("lot_sizes.yaml", "costs.yaml", "nse_holidays.yaml")


@lru_cache(maxsize=1)
def code_version() -> str:
    h = hashlib.sha256()
    paths = [p for d in _CODE_DIRS for p in sorted((BACKEND_DIR / d).rglob("*.py"))]
    paths += [BACKEND_DIR / f for f in _CODE_FILES]
    for p in paths:
        if p.exists():
            h.update(p.relative_to(BACKEND_DIR).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:16]


def _file_stamp(p: Path) -> list:
    try:
        st = p.stat()
        return [p.name, st.st_size, st.st_mtime_ns]
    except FileNotFoundError:
        return [p.name, None]


def files_stamp(data_dir: Path, underlying: str) -> list:
    files = [CONFIG_DIR / f for f in _CONFIG_FILES]
    files += [data_dir / "fyers" / "expiry_map.json", data_dir / "calibration" / f"{underlying.upper()}.json"]
    return [_file_stamp(p) for p in files]


async def data_stamp(s: AsyncSession, underlying: str, source: str, start: date, end: date, options: bool) -> list:
    """Index, VIX and futures candles in the range (every stored timeframe) and option contracts for its expiries."""
    lo, hi = bt_data._bounds(start, end)
    if bt_data._naive(s):
        lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
    q = (
        select(InstrumentRow.instrument_type, func.count(), func.max(CandleRow.ts))
        .join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
        .where(or_(and_(InstrumentRow.symbol.in_((underlying, bt_data.VIX_SYMBOL)), InstrumentRow.instrument_type == "INDEX"),
                   and_(InstrumentRow.underlying == underlying, InstrumentRow.instrument_type == "FUT")),
               CandleRow.ts >= lo, CandleRow.ts <= hi)
        .group_by(InstrumentRow.instrument_type))
    if source != "auto":  # "auto" may fall back to simulated data, so both sources count
        q = q.where(bt_data._source_filter(source))
    rows = (await s.execute(q)).all()
    out: list = sorted([t, n, newest.isoformat() if newest else None] for t, n, newest in rows)
    if options:
        contracts = (await s.execute(
            select(func.count()).select_from(InstrumentRow)
            .where(InstrumentRow.underlying == underlying, InstrumentRow.option_type.is_not(None),
                   InstrumentRow.expiry >= start, InstrumentRow.expiry <= end + timedelta(days=100)))).scalar_one()
        out.append(contracts)
    return out


async def equity_stamp(s: AsyncSession, symbols: list[str], start: date, end: date) -> list:
    lo, hi = bt_data._bounds(start, end)
    if bt_data._naive(s):
        lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
    n, newest = (await s.execute(
        select(func.count(), func.max(CandleRow.ts))
        .join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
        .where(InstrumentRow.symbol.in_(symbols), InstrumentRow.instrument_type == "EQ", CandleRow.timeframe == "1d",
               CandleRow.ts >= lo, CandleRow.ts <= hi))).one()
    return [n, newest.isoformat() if newest else None]


def make_key(owner: str, request: dict, stamp: list) -> str:
    payload = json.dumps({"owner": owner, "request": request, "stamp": stamp, "code": code_version()},
                         sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


async def lookup(s: AsyncSession, owner: str, key: str) -> dict | None:
    row = (await s.execute(select(BacktestRunRow)
                           .where(BacktestRunRow.owner == owner, BacktestRunRow.cache_key == key)
                           .order_by(BacktestRunRow.created_at.desc()).limit(1))).scalars().first()
    if row is None:
        return None
    return {"id": row.id, "underlying": row.underlying, "start": row.start.isoformat(), "end": row.end.isoformat(),
            "timeframe": row.timeframe, "strategy": "spec" if row.strategy.startswith("spec:") else row.strategy,
            **row.result, "cached": True, "cached_at": row.created_at.isoformat()}
