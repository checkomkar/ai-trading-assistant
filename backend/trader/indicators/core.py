from __future__ import annotations

import math
from collections import deque
from datetime import date


class SMA:
    def __init__(self, period: int) -> None:
        self.period = period
        self._win: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if len(self._win) == self.period:
            self._sum -= self._win[0]
        self._win.append(x)
        self._sum += x
        self.value = self._sum / self.period if len(self._win) == self.period else None
        return self.value


class EMA:
    """Seeded with the SMA of the first `period` values (standard convention)."""

    def __init__(self, period: int) -> None:
        self.period = period
        self.k = 2 / (period + 1)
        self._seed: list[float] = []
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.period:
                self.value = sum(self._seed) / self.period
            return self.value
        self.value = x * self.k + self.value * (1 - self.k)
        return self.value


class _Wilder:
    """Wilder smoothing (RMA): seeded with the simple mean of the first `period` values."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._seed: list[float] = []
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._seed.append(x)
            if len(self._seed) == self.period:
                self.value = sum(self._seed) / self.period
            return self.value
        self.value = (self.value * (self.period - 1) + x) / self.period
        return self.value


class RSI:
    def __init__(self, period: int = 14) -> None:
        self._gain, self._loss = _Wilder(period), _Wilder(period)
        self._prev: float | None = None
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev is None:
            self._prev = close
            return None
        ch = close - self._prev
        self._prev = close
        g, l = self._gain.update(max(ch, 0.0)), self._loss.update(max(-ch, 0.0))
        if g is None or l is None:
            return None
        self.value = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
        return self.value


def true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


class ATR:
    def __init__(self, period: int = 14) -> None:
        self._rma = _Wilder(period)
        self._prev_close: float | None = None
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        self.value = self._rma.update(true_range(high, low, self._prev_close))
        self._prev_close = close
        return self.value


class ADX:
    """Wilder's ADX with +DI / -DI."""

    def __init__(self, period: int = 14) -> None:
        self.period = period
        self._tr, self._pdm, self._mdm = _Wilder(period), _Wilder(period), _Wilder(period)
        self._dx = _Wilder(period)
        self._prev: tuple[float, float, float] | None = None
        self.plus_di: float | None = None
        self.minus_di: float | None = None
        self.value: float | None = None

    def update(self, high: float, low: float, close: float) -> float | None:
        if self._prev is None:
            self._prev = (high, low, close)
            return None
        ph, pl, pc = self._prev
        self._prev = (high, low, close)
        up, down = high - ph, pl - low
        pdm = up if up > down and up > 0 else 0.0
        mdm = down if down > up and down > 0 else 0.0
        tr = self._tr.update(true_range(high, low, pc))
        sp, sm = self._pdm.update(pdm), self._mdm.update(mdm)
        if tr is None or sp is None or sm is None or tr == 0:
            return None
        self.plus_di, self.minus_di = 100 * sp / tr, 100 * sm / tr
        denom = self.plus_di + self.minus_di
        dx = 0.0 if denom == 0 else 100 * abs(self.plus_di - self.minus_di) / denom
        self.value = self._dx.update(dx)
        return self.value


class MACD:
    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self._fast, self._slow, self._signal = EMA(fast), EMA(slow), EMA(signal)
        self.macd: float | None = None
        self.signal: float | None = None
        self.histogram: float | None = None

    def update(self, close: float) -> float | None:
        f, s = self._fast.update(close), self._slow.update(close)
        if f is None or s is None:
            return None
        self.macd = f - s
        self.signal = self._signal.update(self.macd)
        self.histogram = self.macd - self.signal if self.signal is not None else None
        return self.macd


class ROC:
    def __init__(self, period: int = 10) -> None:
        self._win: deque[float] = deque(maxlen=period + 1)
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        self._win.append(close)
        if len(self._win) == self._win.maxlen and self._win[0] != 0:
            self.value = (close / self._win[0] - 1) * 100
        return self.value


class BollingerBands:
    def __init__(self, period: int = 20, k: float = 2.0) -> None:
        self.period, self.k = period, k
        self._win: deque[float] = deque(maxlen=period)
        self.mid: float | None = None
        self.upper: float | None = None
        self.lower: float | None = None

    def update(self, close: float) -> float | None:
        self._win.append(close)
        if len(self._win) < self.period:
            return None
        m = sum(self._win) / self.period
        sd = math.sqrt(sum((x - m) ** 2 for x in self._win) / self.period)
        self.mid, self.upper, self.lower = m, m + self.k * sd, m - self.k * sd
        return self.mid


class HistoricalVolatility:
    """Annualised close-to-close volatility from log returns of the last `period` bars."""

    def __init__(self, period: int = 20, bars_per_year: float = 252 * 75) -> None:
        self._rets: deque[float] = deque(maxlen=period)
        self._prev: float | None = None
        self._ann = math.sqrt(bars_per_year)
        self.value: float | None = None

    def update(self, close: float) -> float | None:
        if self._prev:
            self._rets.append(math.log(close / self._prev))
        self._prev = close
        if len(self._rets) == self._rets.maxlen:
            m = sum(self._rets) / len(self._rets)
            var = sum((r - m) ** 2 for r in self._rets) / (len(self._rets) - 1)
            self.value = math.sqrt(var) * self._ann
        return self.value


class RollingExtreme:
    """Highest high / lowest low of the previous `period` bars (excluding the current one)."""

    def __init__(self, period: int) -> None:
        self._highs: deque[float] = deque(maxlen=period)
        self._lows: deque[float] = deque(maxlen=period)
        self.high: float | None = None
        self.low: float | None = None

    def update(self, high: float, low: float) -> None:
        full = len(self._highs) == self._highs.maxlen
        self.high = max(self._highs) if full else None
        self.low = min(self._lows) if full else None
        self._highs.append(high)
        self._lows.append(low)


class SessionVWAP:
    """VWAP that resets every trading day. Needs traded volume (use futures for indices)."""

    def __init__(self) -> None:
        self._day: date | None = None
        self._pv = 0.0
        self._v = 0.0
        self.value: float | None = None

    def update(self, day: date, high: float, low: float, close: float, volume: float) -> float | None:
        if day != self._day:
            self._day, self._pv, self._v, self.value = day, 0.0, 0.0, None
        if volume > 0:
            self._pv += (high + low + close) / 3 * volume
            self._v += volume
            self.value = self._pv / self._v
        return self.value


class Pivots:
    """Swing highs/lows by fractal: a bar is a swing high when its high is above the `k` bars before and after it.
    A pivot is only known `k` bars after it happened, so this never looks ahead. Keeps the latest two of each,
    with the RSI (if given) at the pivot, for divergence checks."""

    def __init__(self, k: int) -> None:
        self.k = k
        self._win: deque[tuple[float, float, float | None]] = deque(maxlen=2 * k + 1)
        self._n = 0
        self.highs: deque[tuple[int, float, float | None]] = deque(maxlen=2)  # (bar index, price, rsi)
        self.lows: deque[tuple[int, float, float | None]] = deque(maxlen=2)
        self.index = -1  # index of the bar last passed to update()

    def update(self, high: float, low: float, rsi: float | None = None) -> None:
        self._n += 1
        self.index = self._n - 1
        self._win.append((high, low, rsi))
        if len(self._win) < 2 * self.k + 1:
            return
        k, w = self.k, list(self._win)
        mid_h, mid_l, mid_r = w[k]
        at = self.index - k
        if all(mid_h > w[i][0] for i in range(2 * k + 1) if i != k):
            self.highs.append((at, mid_h, mid_r))
        if all(mid_l < w[i][1] for i in range(2 * k + 1) if i != k):
            self.lows.append((at, mid_l, mid_r))

    @property
    def last_high(self) -> float | None:
        return self.highs[-1][1] if self.highs else None

    @property
    def last_low(self) -> float | None:
        return self.lows[-1][1] if self.lows else None

    def divergence(self, within: int) -> tuple[bool, bool]:
        """(bullish, bearish): price made a lower low / higher high but RSI did not, between the last two pivots
        that are at most `within` bars apart, the newer one at most `within` bars old."""
        def check(pts, worse) -> bool:  # noqa: ANN001
            if len(pts) < 2:
                return False
            (i1, p1, r1), (i2, p2, r2) = pts[0], pts[1]
            if None in (r1, r2) or i2 - i1 > within or self.index - i2 > within:
                return False
            return worse(p2, p1) and (r2 > r1 if worse is _lower else r2 < r1)  # type: ignore[operator]
        return check(self.lows, _lower), check(self.highs, _higher)


def _lower(a: float, b: float) -> bool:
    return a < b


def _higher(a: float, b: float) -> bool:
    return a > b


def candle_patterns(bars: list[tuple[float, float, float, float]]) -> dict[str, float]:
    """1.0/0.0 flags for classic candlestick patterns. `bars` = last up to 3 closed (open, high, low, close), oldest
    first. Each pattern is only the shape of the candles; combine it with a trend condition to give it meaning."""
    out = dict.fromkeys(("bull_engulfing", "bear_engulfing", "doji", "hammer", "shooting_star",
                         "morning_star", "evening_star"), 0.0)
    if not bars:
        return out
    o, h, l, c = bars[-1]
    rng = h - l
    body = abs(c - o)
    if rng > 0:
        upper, lower = h - max(o, c), min(o, c) - l
        out["doji"] = float(body <= 0.1 * rng)
        out["hammer"] = float(body <= 0.4 * rng and lower >= 0.6 * rng and upper <= 0.15 * rng)
        out["shooting_star"] = float(body <= 0.4 * rng and upper >= 0.6 * rng and lower <= 0.15 * rng)
    if len(bars) >= 2:
        po, _, _, pc = bars[-2]
        out["bull_engulfing"] = float(pc < po and c > o and o <= pc and c >= po)
        out["bear_engulfing"] = float(pc > po and c < o and o >= pc and c <= po)
    if len(bars) >= 3:
        o1, h1, l1, c1 = bars[-3]
        o2, h2, l2, c2 = bars[-2]
        r1, r2 = h1 - l1, h2 - l2
        if r1 > 0 and r2 > 0 and rng > 0:
            small = abs(c2 - o2) <= 0.3 * r2
            out["morning_star"] = float(c1 < o1 and abs(c1 - o1) >= 0.5 * r1 and small and c > o and c > (o1 + c1) / 2)
            out["evening_star"] = float(c1 > o1 and abs(c1 - o1) >= 0.5 * r1 and small and c < o and c < (o1 + c1) / 2)
    return out
