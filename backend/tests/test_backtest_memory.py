"""Option candles are held as compact series (backtest/data.py CandleSeries) so a year of 1m data fits a small server.
They must read exactly like the {ts: Candle} dicts they replace."""

from __future__ import annotations

import pickle
from datetime import datetime, timedelta, timezone

from trader.backtest.data import CandleSeries
from trader.market_data.candles import Candle

T0 = datetime(2026, 10, 19, 3, 45, tzinfo=timezone.utc)


def candles(n=50):
    return [Candle("NSE:X", "1m", T0 + timedelta(minutes=i), 100 + i, 101 + i, 99 + i, 100.5 + i, 10 * i, True,
                   oi=None if i % 3 else 500 + i) for i in range(n)]


def test_series_reads_like_the_dict_it_replaces():
    cs = candles()
    as_dict = {c.ts: c for c in cs}
    ser = CandleSeries.of(list(reversed(cs)), "NSE:X", "1m")  # any order in, time order kept
    assert len(ser) == len(as_dict) and list(ser) == list(as_dict)
    for ts, c in as_dict.items():
        assert ts in ser and ser.get(ts) == c and ser[ts] == c
    missing = T0 + timedelta(seconds=30)
    assert ser.get(missing) is None and missing not in ser and ser.get(missing, "x") == "x"
    assert dict(ser.items()) == as_dict


def test_duplicates_keep_one_and_series_pickle_for_parallel_workers():
    cs = candles(5)
    ser = CandleSeries.of(cs + cs[:2], "NSE:X", "1m")
    assert len(ser) == 5
    back = pickle.loads(pickle.dumps(ser))
    assert dict(back.items()) == dict(ser.items()) and back.token == "NSE:X"


def test_series_is_much_smaller_than_candle_objects():
    import tracemalloc

    rows = [(T0 + timedelta(minutes=i), 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10 * i) for i in range(20_000)]
    tracemalloc.start()
    base = tracemalloc.take_snapshot()
    objs = {r[0]: Candle("NSE:X", "1m", r[0] + timedelta(0), r[1], r[2], r[3], r[4], r[5], True) for r in rows}
    as_objects = sum(x.size for x in tracemalloc.take_snapshot().compare_to(base, "filename"))
    del objs
    base = tracemalloc.take_snapshot()
    ser = CandleSeries("NSE:X", "1m")
    for r in rows:
        ser.append(int(r[0].timestamp()), r[1], r[2], r[3], r[4], r[5], None)
    as_series = sum(x.size for x in tracemalloc.take_snapshot().compare_to(base, "filename"))
    tracemalloc.stop()
    assert as_series * 3 < as_objects, (as_series, as_objects)  # about 59 vs 190 bytes a candle
