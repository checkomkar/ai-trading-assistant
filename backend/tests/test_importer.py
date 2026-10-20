from datetime import timezone

from sqlalchemy import select

from trader.db.models import CandleRow
from trader.db.session import create_engine, create_sessionmaker
from trader.scripts.import_candles import import_file, parse_ts


def test_parse_ts_naive_is_ist():
    assert parse_ts("2026-10-20 09:15:00").astimezone(timezone.utc).hour == 3
    assert parse_ts("2026-10-20T03:45:00Z").hour == 3
    assert parse_ts(1_790_000_000_000).year == 2026  # epoch millis


async def test_import_csv_idempotent(tmp_path):
    csv = tmp_path / "nifty.csv"
    csv.write_text(
        "Date,Open,High,Low,Close,Volume\n"
        "2026-10-19 09:15:00,25000,25040,24990,25030,0\n"
        "2026-10-19 09:20:00,25030,25060,25020,25050,0\n"
        "2026-10-19 09:25:00,25050,25010,25000,25040,0\n",  # bad: high < open
        encoding="utf-8",
    )
    url = f"sqlite+aiosqlite:///{(tmp_path / 'h.db').as_posix()}"
    assert await import_file(csv, "nifty", "5m", url) == 2
    assert await import_file(csv, "NIFTY", "5m", url) == 2  # upsert, no duplicates

    engine = create_engine(url)
    async with create_sessionmaker(engine)() as s:
        rows = (await s.execute(select(CandleRow))).scalars().all()
    await engine.dispose()
    assert len(rows) == 2 and rows[0].close == 25030
