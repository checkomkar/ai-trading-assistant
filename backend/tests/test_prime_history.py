"""Multi-year download (PRD §24.11): deep backfill, holiday inference, expiry probing, and the file archive."""

import asyncio
import gzip
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import func, select

from tests.test_expired_bulk import no_sleep
from tests.test_paper_runner import IDX, world  # noqa: F401 - `world` seeds 13-21 Oct 2026 index candles
from trader.brokers.fyers import expired, expired_bulk as bulk
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.models import CandleRow, InstrumentRow
from trader.db.session import create_engine, create_sessionmaker
from trader.domain import Instrument, InstrumentType
from trader.instruments import holiday_inference as hol
from trader.instruments.calendar import IST, TradingCalendar, get_calendar
from trader.market_data import archive, deep_backfill
from trader.market_data.candles import Candle

CAL = get_calendar()
NOW = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
INST = Instrument(IDX, "NIFTY", "NIFTY", "NSE", InstrumentType.INDEX)


def fake_history(depth: date, calls: list | None = None, fail: bool = False):
    """Five 1m candles per weekday from `depth` onwards, like a broker whose history starts there."""

    async def history(inst, tf, start, end):
        if calls is not None:
            calls.append((start, end))
        if fail:
            raise RuntimeError("Invalid input")
        out, d = [], start.astimezone(IST).date()
        while d <= end.astimezone(IST).date():
            if d >= depth and d.weekday() < 5:
                for i in range(5):
                    ts = datetime.combine(d, time(9, 15 + i), tzinfo=IST).astimezone(timezone.utc)
                    if start <= ts < end:
                        out.append(Candle(inst.token, tf, ts, 100, 101, 99, 100, 0, True))
            d += timedelta(days=1)
        return out

    return history


@pytest.fixture
async def empty_db(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'deep.db').as_posix()}"
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        ids = await repo.upsert_instruments(s, [INST])
    yield sm, ids[IDX]
    await engine.dispose()


async def count(sm) -> int:
    async with sm() as s:
        return (await s.execute(select(func.count()).select_from(CandleRow))).scalar_one()


# ---- deep backfill ---------------------------------------------------------------------------------------
async def test_deep_backfill_walks_back_to_where_the_brokers_history_ends_and_a_rerun_costs_one_request(empty_db):
    sm, iid = empty_db
    hist = fake_history(depth=date(2026, 9, 1))
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    res = await deep_backfill.backfill_older(hist, sm, INST, iid, "1m", start, NOW, window=timedelta(days=10),
                                             say=lambda _: None)
    assert not res.reached_start and "no data before" in res.note
    assert res.earliest.astimezone(IST).date() == date(2026, 9, 1)
    n = await count(sm)
    assert n == res.stored > 0
    calls: list = []
    again = await deep_backfill.backfill_older(fake_history(date(2026, 9, 1), calls), sm, INST, iid, "1m", start, NOW,
                                               window=timedelta(days=10), say=lambda _: None)
    assert again.stored == 0 and len(calls) == 1 and await count(sm) == n


async def test_deep_backfill_stops_at_the_requested_start(empty_db):
    sm, iid = empty_db
    start = datetime.combine(date(2026, 10, 1), time(0), tzinfo=IST).astimezone(timezone.utc)
    res = await deep_backfill.backfill_older(fake_history(date(2020, 1, 1)), sm, INST, iid, "1m", start, NOW,
                                             window=timedelta(days=4), say=lambda _: None)
    assert res.reached_start and res.earliest.astimezone(IST).date() == date(2026, 10, 1)


async def test_a_refusal_is_retried_then_reported_as_the_end_of_history(empty_db):
    sm, iid = empty_db
    calls: list = []
    res = await deep_backfill.backfill_older(fake_history(date(2020, 1, 1), calls, fail=True), sm, INST, iid, "1m",
                                             datetime(2026, 1, 1, tzinfo=timezone.utc), NOW, say=lambda _: None)
    assert len(calls) == 2 and "refused" in res.note and res.stored == 0 and res.earliest is None


# ---- holidays ---------------------------------------------------------------------------------------------
async def test_weekdays_without_any_index_candle_are_holiday_candidates(world):
    days = await hol.stored_days(world, ["NIFTY", "BANKNIFTY"])
    assert date(2026, 10, 13) in days and date(2026, 10, 21) in days
    assert hol.missing_weekdays(days, set()) == [date(2026, 10, 20)]
    assert hol.missing_weekdays(days, {date(2026, 10, 20)}) == []  # already listed


def test_inferred_holidays_are_appended_without_touching_the_rest_of_the_file(tmp_path):
    src = (CAL_PATH := get_calendar_path()).read_text(encoding="utf-8")
    p = tmp_path / "nse_holidays.yaml"
    p.write_text(src, encoding="utf-8")
    before = TradingCalendar.from_yaml(p).holidays
    assert hol.append_holidays(p, [date(2021, 3, 29), date(2021, 1, 26)]) == 2
    after = TradingCalendar.from_yaml(p).holidays
    assert set(after) == set(before) | {date(2021, 3, 29), date(2021, 1, 26)}
    text = p.read_text(encoding="utf-8")
    assert text.startswith(src.split("holidays:")[0]) and "special_sessions" in text
    assert CAL_PATH.read_text(encoding="utf-8") == src  # the real file is untouched


def get_calendar_path():
    from trader.config import CONFIG_DIR

    return CONFIG_DIR / "nse_holidays.yaml"


# ---- expiry probing ---------------------------------------------------------------------------------------
class ShiftedRest:
    """The 19 Oct 2026 (Monday) weekly expiry really was on Wednesday the 21st, as under a wrong weekday rule."""

    def __init__(self, missing_all: bool = False) -> None:
        self.symbols: list[str] = []
        self.missing_all = missing_all

    def fno_historical_data(self, req: dict) -> dict:
        sym = req["symbol"]
        self.symbols.append(sym)
        day = int(sym.split("NIFTY26O")[1][:2]) if "NIFTY26O" in sym else None
        if self.missing_all or day == 19:
            return {"s": "no_data", "candles": []}
        t0 = int(datetime.combine(date(2026, 10, 12), time(9, 15), tzinfo=IST).timestamp())
        rows = [[t0 + 60 * i, 100, 101, 99, 100, 500, 1000] for i in range(3)]
        return {"s": "ok", "columns": ["timestamp", "open", "high", "low", "close", "volume", "oi"], "candles": rows}


async def plan(world, tmp_path, emap):
    return await bulk.build_plan(world, CAL, ["NIFTY"], date(2026, 10, 13), date(2026, 10, 21), timeframe="1m",
                                 pad=1, today=date(2026, 10, 22), ledger=bulk.EmptyLedger(tmp_path / "empty.json"),
                                 expiry_map=emap)


async def test_a_wrong_weekly_expiry_is_found_on_another_day_of_its_week_and_remembered(world, tmp_path):
    emap = bulk.ExpiryMap(tmp_path / "map.json")
    p = await plan(world, tmp_path, emap)
    rest = ShiftedRest()
    res = await bulk.fetch(rest, world, p, timeframe="1m", quota=expired.DailyQuota(tmp_path / "q.json", 0),
                           ledger=bulk.EmptyLedger(tmp_path / "empty.json"), say=lambda _: None, sleep=no_sleep,
                           expiry_map=emap, cal=CAL)
    assert emap.get("NIFTY", date(2026, 10, 19)) == (date(2026, 10, 21), False)
    assert emap.get("NIFTY", date(2026, 10, 13)) == (date(2026, 10, 13), False)  # confirmed as planned
    assert len(rest.symbols) == len(set(rest.symbols))  # the probe hit is not fetched twice
    async with world() as s:
        exps = set((await s.execute(select(InstrumentRow.expiry).where(InstrumentRow.instrument_type == "OPT"))).scalars())
    assert date(2026, 10, 21) in exps and date(2026, 10, 19) not in exps and res.errors == 0
    # Re-planning uses the map: nothing already fetched is asked for again. The corrected window is longer
    # (to the 21st), so the strikes the index reached in those extra days are added.
    again = await plan(world, tmp_path, bulk.ExpiryMap(tmp_path / "map.json"))
    assert again.stored == res.fetched
    assert all(it.window.expiry == date(2026, 10, 21) and it.symbol not in rest.symbols for it in again.items)


async def test_expiries_with_no_contract_on_any_day_stop_the_index_after_a_streak(world, tmp_path):
    emap = bulk.ExpiryMap(tmp_path / "map.json")
    p = await plan(world, tmp_path, emap)
    res = await bulk.fetch(ShiftedRest(missing_all=True), world, p, timeframe="1m",
                           quota=expired.DailyQuota(tmp_path / "q.json", 0), ledger=bulk.EmptyLedger(tmp_path / "e.json"),
                           say=lambda _: None, sleep=no_sleep, expiry_map=emap, cal=CAL, stop_after_missing_windows=1)
    assert res.fetched == 0 and res.stopped and "appears to end" in res.stopped
    newest = max(it.window.expiry for it in p.items)
    assert ExpiryMap_key(newest) in emap and emap.get("NIFTY", newest) is None
    again = await plan(world, tmp_path, bulk.ExpiryMap(tmp_path / "map.json"))
    assert any(str(newest) in w for w in again.skipped_windows)


def ExpiryMap_key(d: date) -> str:  # noqa: N802
    return bulk.ExpiryMap.key("NIFTY", d)


def test_alternates_cover_the_rest_of_the_week_nearest_first_and_the_monthly_symbol_in_the_last_week():
    w = bulk.Window("NIFTY", date(2021, 3, 25), date(2021, 3, 18), False)  # a Thursday in March's last week
    alts = bulk.alternates(w, CAL)
    assert alts[0] == (date(2021, 3, 25), True)  # same day, monthly symbol
    assert [d for d, m in alts if not m][:2] == [date(2021, 3, 24), date(2021, 3, 26)]
    mid = bulk.alternates(bulk.Window("NIFTY", date(2021, 3, 11), date(2021, 3, 4), False), CAL)
    assert not any(m for _, m in mid) and (date(2021, 3, 11), False) not in mid


# ---- archive ---------------------------------------------------------------------------------------------
async def test_archive_round_trip_is_incremental_verified_and_restores_only_missing_rows(world, tmp_path):
    root = tmp_path / "archive"
    first = await archive.export(world, root, say=lambda _: None)
    total = await count(world)
    assert first.rows == total and first.written and archive.verify(root) == []
    assert all(k.startswith("candles/") and "/1m/2026-10.csv.gz" in k for k in first.written)

    again = await archive.export(world, root, say=lambda _: None)
    assert again.written == [] and again.unchanged == len(first.written)

    # One new candle rewrites only its own period file.
    async with world() as s, s.begin():
        iid = (await s.execute(select(InstrumentRow.id).where(InstrumentRow.token == IDX))).scalar_one()
        await repo.upsert_candles(s, [Candle(IDX, "1m", datetime(2026, 11, 2, 3, 45, tzinfo=timezone.utc),
                                             1, 1, 1, 1, 0, True)], {IDX: iid})
    third = await archive.export(world, root, say=lambda _: None)
    assert third.written == ["candles/INDEX/NIFTY/1m/2026-11.csv.gz"]

    # Restore into an empty database gives the same rows; a second restore adds nothing.
    url = f"sqlite+aiosqlite:///{(tmp_path / 'restored.db').as_posix()}"
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm2 = create_sessionmaker(engine)
    res = await archive.restore(sm2, root, say=lambda _: None)
    assert res.candles_added == total + 1 == await count(sm2) and res.instruments_added == 3
    assert (await archive.restore(sm2, root, say=lambda _: None)).candles_added == 0
    async with world() as a, sm2() as b:
        q = select(CandleRow.ts, CandleRow.open, CandleRow.close, CandleRow.volume, CandleRow.oi).order_by(CandleRow.ts, CandleRow.open)
        assert (await a.execute(q.limit(50))).all() == (await b.execute(q.limit(50))).all()
    await engine.dispose()

    # A damaged file is caught, and restore refuses it.
    f = root / first.written[0]
    f.write_bytes(gzip.compress(b"token,ts\n"))
    assert any("checksum" in x for x in archive.verify(root))
    with pytest.raises(ValueError):
        await archive.restore(world, root, say=lambda _: None)


async def test_simulated_data_is_never_archived(world, tmp_path):
    async with world() as s, s.begin():
        ids = await repo.upsert_instruments(s, [Instrument("SIM:NIFTY", "NIFTY", "NIFTY", "NSE", InstrumentType.INDEX)])
        await repo.upsert_candles(s, [Candle("SIM:NIFTY", "1m", datetime(2026, 9, 1, 4, tzinfo=timezone.utc),
                                             1, 1, 1, 1, 0, True)], ids)
    res = await archive.export(world, tmp_path / "a", say=lambda _: None)
    assert not any("2026-09" in k for k in res.written)
    with gzip.open(tmp_path / "a" / "instruments.csv.gz", "rt") as f:
        assert "SIM:" not in f.read()
