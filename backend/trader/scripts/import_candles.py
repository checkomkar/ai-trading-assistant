"""Historical candle importer (PRD §2.4, Sprint 3 task 23).

    python -m trader.scripts.import_candles data.csv --symbol NIFTY --timeframe 5m
    python -m trader.scripts.import_candles data.json --symbol BANKNIFTY --timeframe 1m

CSV: header row with a timestamp column (`timestamp`, `datetime`, `date` or `ts`) and
`open, high, low, close[, volume]`. JSON: a list of objects with the same keys, or the
common broker shape `{"candles": [[ts, o, h, l, c, v], ...]}`.
Naive timestamps are interpreted as IST.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from trader.config import get_settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.models import InstrumentRow
from trader.db.session import create_engine, create_sessionmaker
from trader.domain import Instrument, InstrumentType
from trader.instruments.calendar import IST
from trader.market_data.candles import TIMEFRAMES, Candle
from trader.market_data.resample import minutes, session_bucket

TS_KEYS = ("timestamp", "datetime", "date", "ts", "time")


def parse_ts(raw: str | int | float) -> datetime:
    if isinstance(raw, (int, float)) or (isinstance(raw, str) and raw.isdigit()):
        v = float(raw)
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
    dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=IST)).astimezone(timezone.utc)


def _row_to_candle(row: dict, token: str, timeframe: str) -> Candle:
    lower = {k.strip().lower(): v for k, v in row.items()}
    ts_key = next((k for k in TS_KEYS if k in lower), None)
    if ts_key is None:
        raise ValueError(f"No timestamp column in {list(row)}")
    o, h, l, c = (float(lower[k]) for k in ("open", "high", "low", "close"))
    if not (l <= min(o, c) and h >= max(o, c) and l > 0):
        raise ValueError(f"Inconsistent OHLC at {lower[ts_key]}: {o} {h} {l} {c}")
    ts = session_bucket(parse_ts(lower[ts_key]), minutes(timeframe))  # 09:15-anchored, like NSE charts
    vol = int(float(lower.get("volume") or 0))
    return Candle(token, timeframe, ts, o, h, l, c, vol, closed=True)


def load_rows(path: Path) -> Iterable[dict]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "candles" in data:
            keys = ("timestamp", "open", "high", "low", "close", "volume")
            return [dict(zip(keys, c)) for c in data["candles"]]
        return data
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


async def import_file(path: Path, symbol: str, timeframe: str, database_url: str | None = None) -> int:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"timeframe must be one of {list(TIMEFRAMES)}")
    url = database_url or get_settings().resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    symbol = symbol.upper()
    token = f"HIST:{symbol}"
    errors: list[str] = []
    candles: list[Candle] = []
    for i, row in enumerate(load_rows(path), start=2):
        try:
            candles.append(_row_to_candle(row, token, timeframe))
        except (ValueError, KeyError) as exc:
            errors.append(f"row {i}: {exc}")
    if errors:
        print(f"Skipped {len(errors)} invalid rows (first 5):", *errors[:5], sep="\n  ")

    async with sm() as s, s.begin():
        # Attach history to the existing index instrument with this symbol, else create one.
        existing = (await s.execute(
            select(InstrumentRow)
            .where(InstrumentRow.symbol == symbol, InstrumentRow.instrument_type == "INDEX")
            .order_by(InstrumentRow.token.like("SIM:%"))  # prefer a real broker instrument over the simulator's
        )).scalars().first()
        if existing is not None:
            ids = {existing.token: existing.id}
            for c in candles:
                c.token = existing.token
        else:
            ids = await repo.upsert_instruments(s, [Instrument(token, symbol, symbol, "NSE", InstrumentType.INDEX)])
        n = await repo.upsert_candles(s, candles, ids)
        await repo.record_event(s, "importer", "candles.imported",
                                {"file": path.name, "symbol": symbol, "timeframe": timeframe, "rows": n,
                                 "skipped": len(errors)})
    await engine.dispose()
    return n


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", type=Path)
    p.add_argument("--symbol", required=True)
    p.add_argument("--timeframe", default="5m", choices=list(TIMEFRAMES))
    args = p.parse_args()
    n = asyncio.run(import_file(args.file, args.symbol, args.timeframe))
    print(f"Imported {n} candles for {args.symbol.upper()} ({args.timeframe})")


if __name__ == "__main__":
    main()
