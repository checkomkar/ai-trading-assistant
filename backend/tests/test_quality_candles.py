from datetime import datetime, timedelta, timezone

from trader.domain import Tick
from trader.instruments.calendar import IST
from trader.market_data.candles import CandleAggregator, bucket_start
from trader.market_data.quality import DataQualityMonitor, Issue

T0 = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


def tick(token="IDX", sec=0, ltp=100.0, **kw):
    return Tick(token, T0 + timedelta(seconds=sec), ltp, **kw)


def test_quality_accepts_normal_ticks():
    q = DataQualityMonitor()
    assert q.check(tick(sec=0), T0).accept
    assert q.check(tick(sec=1, ltp=100.5), T0 + timedelta(seconds=1)).accept
    assert q.accepted == 2 and q.rejected == 0


def test_quality_rejections():
    q = DataQualityMonitor(index_tokens={"IDX"})
    now = T0 + timedelta(seconds=10)
    q.check(tick(sec=5, volume=10), now)
    assert Issue.DUPLICATE in q.check(tick(sec=5, volume=10), now).issues
    assert Issue.OUT_OF_ORDER in q.check(tick(sec=4), now).issues
    assert Issue.PRICE_JUMP in q.check(tick(sec=6, ltp=150), now).issues
    assert Issue.INVALID_PRICE in q.check(tick(sec=7, ltp=0), now).issues
    assert Issue.FUTURE_TIMESTAMP in q.check(tick(sec=60), now).issues
    assert q.rejected == 5
    # crossed quote is flagged but not rejected
    v = q.check(tick(sec=8, bid=101, ask=100), now)
    assert v.accept and Issue.CROSSED_QUOTE in v.issues


def test_quality_gap_and_stale():
    q = DataQualityMonitor(watched_tokens={"IDX"})
    q.check(tick(sec=0), T0)
    assert q.sweep_stale(T0 + timedelta(seconds=5)) == set()
    assert q.sweep_stale(T0 + timedelta(seconds=11)) == {"IDX"}
    assert q.sweep_stale(T0 + timedelta(seconds=12)) == set()  # reported once
    v = q.check(tick(sec=45), T0 + timedelta(seconds=45))
    assert v.accept and Issue.GAP in v.issues
    assert "IDX" not in q.stale_tokens


def test_bucket_alignment_ist():
    ts = datetime(2026, 10, 20, 9, 17, 42, tzinfo=IST)
    assert bucket_start(ts, timedelta(minutes=5)).astimezone(IST).strftime("%H:%M") == "09:15"
    assert bucket_start(ts, timedelta(minutes=1)).astimezone(IST).strftime("%H:%M:%S") == "09:17:00"


def test_candle_ohlc_and_volume_from_cumulative():
    agg = CandleAggregator(("1m",))
    base = datetime(2026, 10, 20, 9, 30, 0, tzinfo=IST).astimezone(timezone.utc)
    seq = [(0, 100, 1000), (20, 103, 1100), (40, 99, 1250), (59, 101, 1300), (61, 102, 1400)]
    closed = []
    for sec, px, vol in seq:
        closed += agg.update("F", base + timedelta(seconds=sec), px, vol)
    assert len(closed) == 1
    c = closed[0]
    assert (c.open, c.high, c.low, c.close) == (100, 103, 99, 101)
    assert c.volume == 300
    assert agg.current("F", "1m").volume == 100  # 1400 - 1300


def test_close_due_without_new_ticks():
    agg = CandleAggregator(("1m",))
    base = datetime(2026, 10, 20, 9, 30, 10, tzinfo=IST).astimezone(timezone.utc)
    agg.update("F", base, 100, None)
    assert agg.close_due(base + timedelta(seconds=30)) == []
    closed = agg.close_due(base + timedelta(seconds=55))
    assert len(closed) == 1 and closed[0].closed
