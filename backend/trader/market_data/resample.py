"""Build higher-timeframe candles from lower ones (e.g. 1m -> 3m/15m/30m/1h).

Exact for OHLCV: open = first open, high = max, low = min, close = last close,
volume = sum, OI = last. Buckets are anchored to the 09:15 IST session open, which is how
NSE charts draw 30m / 1h candles (09:15-09:45, 09:15-10:15, ...). You can only go *up*:
a 5m candle cannot be split into 1m candles.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from trader.instruments.calendar import IST
from trader.market_data.candles import Candle

SESSION_OPEN = time(9, 15)

# Timeframes the backtester accepts, in minutes.
SUPPORTED = {"1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "20m": 20, "30m": 30, "60m": 60}


def minutes(tf: str) -> int:
    try:
        return SUPPORTED[tf]
    except KeyError:
        raise ValueError(f"Unsupported timeframe {tf!r}; use one of {list(SUPPORTED)}") from None


def session_bucket(ts: datetime, tf_minutes: int) -> datetime:
    local = ts.astimezone(IST)
    anchor = datetime.combine(local.date(), SESSION_OPEN, tzinfo=IST)
    if local < anchor:  # pre-open prints belong to the first bucket
        return anchor.astimezone(timezone.utc)
    n = int((local - anchor).total_seconds() // (tf_minutes * 60))
    return (anchor + timedelta(minutes=n * tf_minutes)).astimezone(timezone.utc)


def resample(candles: list[Candle], target_tf: str, source_tf: str | None = None) -> list[Candle]:
    """`candles` must be sorted by time and share one token and timeframe."""
    tgt = minutes(target_tf)
    if source_tf is not None:
        src = minutes(source_tf)
        if tgt < src or tgt % src:
            raise ValueError(f"cannot build {target_tf} from {source_tf} (target must be a multiple)")
        if tgt == src:
            return list(candles)
    out: list[Candle] = []
    cur: Candle | None = None
    for c in candles:
        b = session_bucket(c.ts, tgt)
        if cur is None or b != cur.ts:
            if cur is not None:
                out.append(cur)
            cur = Candle(c.token, target_tf, b, c.open, c.high, c.low, c.close, c.volume, True, c.oi)
            continue
        cur.high, cur.low, cur.close = max(cur.high, c.high), min(cur.low, c.low), c.close
        cur.volume += c.volume
        if c.oi is not None:
            cur.oi = c.oi
    if cur is not None:
        out.append(cur)
    return out


def best_source(target_tf: str, available: list[str]) -> str | None:
    """Coarsest stored timeframe that the target is an exact multiple of (fewest rows to read)."""
    tgt = minutes(target_tf)
    usable = [tf for tf in available if tf in SUPPORTED and tgt % SUPPORTED[tf] == 0 and SUPPORTED[tf] <= tgt]
    return max(usable, key=lambda tf: SUPPORTED[tf]) if usable else None
