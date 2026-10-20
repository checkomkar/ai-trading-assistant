from datetime import datetime, time, timedelta, timezone

import pytest

from trader.instruments.calendar import IST
from trader.market_data.candles import Candle
from trader.market_data.resample import best_source, resample, session_bucket


def one_minute_day(day=datetime(2026, 10, 21).date()):
    t0 = datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(timezone.utc)
    return [Candle("X", "1m", t0 + timedelta(minutes=i), 100 + i, 100 + i + 2, 100 + i - 1, 100 + i + 1, 10, True, 1000 + i)
            for i in range(375)]  # 09:15 .. 15:29


def test_resample_ohlcv_exact():
    m1 = one_minute_day()
    m5 = resample(m1, "5m", "1m")
    assert len(m5) == 75
    first = m5[0]
    assert (first.open, first.high, first.low, first.close, first.volume, first.oi) == (100, 106, 99, 105, 50, 1004)
    assert first.ts.astimezone(IST).time() == time(9, 15)


def test_hourly_and_30m_anchor_at_session_open():
    m1 = one_minute_day()
    h1 = resample(m1, "60m", "1m")
    assert [c.ts.astimezone(IST).strftime("%H:%M") for c in h1] == \
        ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
    assert h1[-1].volume == 15 * 10  # last hour is the partial 15:15-15:30 bar
    m30 = resample(m1, "30m", "1m")
    assert m30[0].ts.astimezone(IST).strftime("%H:%M") == "09:15" and len(m30) == 13
    assert resample(resample(m1, "5m", "1m"), "15m", "5m") == resample(m1, "15m", "1m")  # path-independent


def test_cannot_build_lower_or_non_multiple():
    with pytest.raises(ValueError):
        resample([], "1m", "5m")
    with pytest.raises(ValueError):
        resample([], "10m", "3m")
    assert best_source("15m", ["1m", "5m"]) == "5m"
    assert best_source("10m", ["3m"]) is None
    assert best_source("60m", ["1m", "15m", "20m"]) == "20m"


def test_session_bucket_preopen():
    pre = datetime(2026, 10, 21, 9, 7, tzinfo=IST)
    assert session_bucket(pre, 5).astimezone(IST).time() == time(9, 15)
