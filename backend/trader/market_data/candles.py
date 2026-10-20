"""Tick -> OHLCV candle aggregation (Sprint 3, task 21).

Buckets are aligned in IST from midnight, so 5m candles start at 09:15, 09:20, ...
Volume is derived from the cumulative day volume carried on each tick.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from trader.instruments.calendar import IST

TIMEFRAMES: dict[str, timedelta] = {
    tf: timedelta(minutes=m) for tf, m in
    {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "20m": 20, "30m": 30, "60m": 60}.items()
}


@dataclass
class Candle:
    token: str
    timeframe: str
    ts: datetime  # bucket start, UTC
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    closed: bool = False
    oi: int | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        return d


def bucket_start(ts: datetime, tf: timedelta) -> datetime:
    local = ts.astimezone(IST)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    n = (local - midnight) // tf
    return (midnight + n * tf).astimezone(timezone.utc)


@dataclass
class _Building:
    candle: Candle
    start_volume: int | None


class CandleAggregator:
    def __init__(self, timeframes: tuple[str, ...] = ("1m", "5m")) -> None:
        self.timeframes = timeframes
        self._open: dict[tuple[str, str], _Building] = {}
        self._last_cum: dict[str, int] = {}

    def update(self, token: str, ts: datetime, price: float, cum_volume: int | None) -> list[Candle]:
        """Apply a tick. Returns candles that closed because this tick started a new bucket."""
        closed: list[Candle] = []
        prev_cum = self._last_cum.get(token)
        for tf_name in self.timeframes:
            start = bucket_start(ts, TIMEFRAMES[tf_name])
            key = (token, tf_name)
            cur = self._open.get(key)
            if cur is not None and start < cur.candle.ts:
                continue  # late tick for an already-closed bucket
            if cur is not None and start > cur.candle.ts:
                cur.candle.closed = True
                closed.append(cur.candle)
                cur = None
            if cur is None:
                # Volume of a new candle counts from the last cumulative volume seen before it.
                cur = _Building(Candle(token, tf_name, start, price, price, price, price),
                                prev_cum if prev_cum is not None else cum_volume)
                self._open[key] = cur
            else:
                c = cur.candle
                c.high, c.low, c.close = max(c.high, price), min(c.low, price), price
            if cum_volume is not None and cur.start_volume is not None:
                if cum_volume < cur.start_volume:  # cumulative counter reset (new session)
                    cur.start_volume = 0
                cur.candle.volume = cum_volume - cur.start_volume
        if cum_volume is not None:
            self._last_cum[token] = cum_volume
        return closed

    def close_due(self, now: datetime, grace: timedelta = timedelta(seconds=2)) -> list[Candle]:
        """Close candles whose bucket ended (for instruments that stopped ticking)."""
        closed: list[Candle] = []
        for key, b in list(self._open.items()):
            if b.candle.ts + TIMEFRAMES[b.candle.timeframe] + grace <= now:
                b.candle.closed = True
                closed.append(b.candle)
                del self._open[key]
        return closed

    def current(self, token: str, timeframe: str) -> Candle | None:
        b = self._open.get((token, timeframe))
        return b.candle if b else None

    def flush(self) -> list[Candle]:
        out = []
        for b in self._open.values():
            b.candle.closed = True
            out.append(b.candle)
        self._open.clear()
        return out
