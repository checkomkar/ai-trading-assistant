"""More indicators, defined like TradingView's built-ins (same formulas and default settings where there is one).

Every class is incremental: `update(o, h, l, c, v)` takes one closed bar and `values` holds its outputs (None while warming up).
Volume `v` may be None (no volume known); volume-weighted outputs are then unknown rather than invented.
"""

from __future__ import annotations

import math
from collections import deque

from trader.indicators.core import ATR, EMA, RSI, SMA, BollingerBands, _Wilder, true_range


class _Base:
    def __init__(self, *outputs: str) -> None:
        self.values: dict[str, float | None] = dict.fromkeys(outputs)

    def update(self, o: float, h: float, l: float, c: float, v: float | None) -> None:  # noqa: E741
        raise NotImplementedError


# ---- moving averages ----------------------------------------------------------------------------
class WMA:
    """Linearly weighted moving average: the newest bar weighs `period`, the oldest 1."""

    def __init__(self, period: int) -> None:
        self.period = period
        self._win: deque[float] = deque(maxlen=period)
        self._den = period * (period + 1) / 2
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        self._win.append(x)
        if len(self._win) == self.period:
            self.value = sum(w * v for w, v in enumerate(self._win, 1)) / self._den
        return self.value


class HMA(_Base):
    """Hull MA: WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._half, self._full = WMA(max(1, period // 2)), WMA(period)
        self._smooth = WMA(max(1, round(math.sqrt(period))))

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        a, b = self._half.update(c), self._full.update(c)
        self.values["value"] = self._smooth.update(2 * a - b) if a is not None and b is not None else None


class VWMA(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._win: deque[tuple[float, float]] = deque(maxlen=period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._win.append((c, v or 0.0))
        vol = sum(x[1] for x in self._win)
        full = len(self._win) == self._win.maxlen
        self.values["value"] = sum(p * w for p, w in self._win) / vol if full and vol > 0 else None


class DEMA(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._e1, self._e2 = EMA(period), EMA(period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        a = self._e1.update(c)
        b = self._e2.update(a) if a is not None else None
        self.values["value"] = 2 * a - b if a is not None and b is not None else None


class TEMA(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._e = (EMA(period), EMA(period), EMA(period))

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        a = self._e[0].update(c)
        b = self._e[1].update(a) if a is not None else None
        d = self._e[2].update(b) if b is not None else None
        self.values["value"] = 3 * a - 3 * b + d if None not in (a, b, d) else None  # type: ignore[operator]


class RMA(_Base):
    """Wilder's smoothed moving average (TradingView's `rma`)."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._w = _Wilder(period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self.values["value"] = self._w.update(c)


class LinReg(_Base):
    """Value of the least-squares line through the last `period` closes, at the last bar."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._win: deque[float] = deque(maxlen=period)
        self._n = period

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._win.append(c)
        n = self._n
        if len(self._win) < n:
            return
        sx, sxx = n * (n - 1) / 2, (n - 1) * n * (2 * n - 1) / 6
        sy = sum(self._win)
        sxy = sum(i * y for i, y in enumerate(self._win))
        denom = n * sxx - sx * sx
        slope = (n * sxy - sx * sy) / denom if denom else 0.0
        self.values["value"] = (sy - slope * sx) / n + slope * (n - 1)


# ---- trend ---------------------------------------------------------------------------------------
class Supertrend(_Base):
    """TradingView's Supertrend(period, factor) on hl2 with Wilder ATR. `dir` is +1 in an uptrend (line under price)
    and -1 in a downtrend (line over price)."""

    def __init__(self, period: int, factor: float) -> None:
        super().__init__("line", "dir")
        self._atr, self._f = ATR(period), factor
        self._pc: float | None = None
        self._pu = self._pl = self._pline = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        atr = self._atr.update(h, l, c)
        pc, self._pc = self._pc, c
        if atr is None:
            return
        mid = (h + l) / 2
        upper, lower = mid + self._f * atr, mid - self._f * atr
        if self._pl is not None:
            lower = lower if lower > self._pl or pc < self._pl else self._pl
            upper = upper if upper < self._pu or pc > self._pu else self._pu
        if self._pline is None:
            down = True  # TradingView starts bearish
        elif self._pline == self._pu:
            down = not (c > upper)
        else:
            down = c < lower
        line = upper if down else lower
        self._pu, self._pl, self._pline = upper, lower, line
        self.values["line"], self.values["dir"] = line, -1.0 if down else 1.0


class ParabolicSAR(_Base):
    """Wilder's parabolic SAR with TradingView's defaults (start 0.02, increment 0.02, maximum 0.2)."""

    def __init__(self, start: float = 0.02, inc: float = 0.02, maximum: float = 0.2) -> None:
        super().__init__("value")
        self._start, self._inc, self._max = start, inc, maximum
        self._prev: tuple[float, float, float] | None = None  # (high, low, close) of the previous bar
        self._prev2: tuple[float, float] | None = None
        self._up = True
        self._sar = self._ep = self._af = 0.0

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        if self._prev is None:
            self._prev = (h, l, c)
            return
        ph, pl, pc = self._prev
        if self._prev2 is None:  # second bar: pick the first trend
            self._up = c >= pc
            self._sar, self._ep, self._af = (pl, h, self._start) if self._up else (ph, l, self._start)
        else:
            sar = self._sar + self._af * (self._ep - self._sar)
            if self._up:
                sar = min(sar, pl, self._prev2[0])
                if l < sar:
                    self._up, sar, self._ep, self._af = False, self._ep, l, self._start
                elif h > self._ep:
                    self._ep, self._af = h, min(self._af + self._inc, self._max)
            else:
                sar = max(sar, ph, self._prev2[1])
                if h > sar:
                    self._up, sar, self._ep, self._af = True, self._ep, h, self._start
                elif l < self._ep:
                    self._ep, self._af = l, min(self._af + self._inc, self._max)
            self._sar = sar
        self._prev2, self._prev = (ph, pl), (h, l, c)
        self.values["value"] = self._sar


class Aroon(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("up", "down", "osc")
        self._p = period
        self._hi: deque[float] = deque(maxlen=period + 1)
        self._lo: deque[float] = deque(maxlen=period + 1)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._hi.append(h)
        self._lo.append(l)
        if len(self._hi) < self._p + 1:
            return
        hi, lo = list(self._hi), list(self._lo)
        since_hi = len(hi) - 1 - max(i for i, x in enumerate(hi) if x == max(hi))  # the most recent extreme
        since_lo = len(lo) - 1 - max(i for i, x in enumerate(lo) if x == min(lo))
        up, down = 100.0 * (self._p - since_hi) / self._p, 100.0 * (self._p - since_lo) / self._p
        self.values.update(up=up, down=down, osc=up - down)


class Vortex(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("plus", "minus")
        self._vp: deque[float] = deque(maxlen=period)
        self._vm: deque[float] = deque(maxlen=period)
        self._tr: deque[float] = deque(maxlen=period)
        self._prev: tuple[float, float, float] | None = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        if self._prev is not None:
            ph, pl, pc = self._prev
            self._vp.append(abs(h - pl))
            self._vm.append(abs(l - ph))
            self._tr.append(true_range(h, l, pc))
            if len(self._tr) == self._tr.maxlen and sum(self._tr) > 0:
                self.values.update(plus=sum(self._vp) / sum(self._tr), minus=sum(self._vm) / sum(self._tr))
        self._prev = (h, l, c)


class Ichimoku(_Base):
    """Standard (9, 26, 52, displacement 26). The cloud values are those computed 25 bars ago, as TradingView plots them."""

    def __init__(self) -> None:
        super().__init__("tenkan", "kijun", "span_a", "span_b")
        self._h: deque[float] = deque(maxlen=52)
        self._l: deque[float] = deque(maxlen=52)
        self._cloud: deque[tuple[float | None, float | None]] = deque(maxlen=26)

    @staticmethod
    def _mid(hs: deque, ls: deque, n: int) -> float | None:
        return (max(list(hs)[-n:]) + min(list(ls)[-n:])) / 2 if len(hs) >= n else None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._h.append(h)
        self._l.append(l)
        t, k, b = self._mid(self._h, self._l, 9), self._mid(self._h, self._l, 26), self._mid(self._h, self._l, 52)
        self._cloud.append(((t + k) / 2 if t is not None and k is not None else None, b))
        self.values.update(tenkan=t, kijun=k)
        if len(self._cloud) == self._cloud.maxlen:
            self.values["span_a"], self.values["span_b"] = self._cloud[0]


# ---- oscillators ---------------------------------------------------------------------------------
class Stochastic(_Base):
    """Stoch(period, 3, 3): %K is the 3-bar average of the raw value, %D the 3-bar average of %K."""

    def __init__(self, period: int) -> None:
        super().__init__("k", "d")
        self._hi: deque[float] = deque(maxlen=period)
        self._lo: deque[float] = deque(maxlen=period)
        self._k, self._d = SMA(3), SMA(3)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._hi.append(h)
        self._lo.append(l)
        if len(self._hi) < self._hi.maxlen:
            return
        hh, ll = max(self._hi), min(self._lo)
        raw = 100.0 * (c - ll) / (hh - ll) if hh > ll else 50.0
        k = self._k.update(raw)
        self.values["k"] = k
        self.values["d"] = self._d.update(k) if k is not None else None


class StochRSI(_Base):
    """Stoch RSI(period, period, 3, 3): the stochastic of the RSI."""

    def __init__(self, period: int) -> None:
        super().__init__("k", "d")
        self._rsi, self._win = RSI(period), deque(maxlen=period)
        self._k, self._d = SMA(3), SMA(3)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        r = self._rsi.update(c)
        if r is None:
            return
        self._win.append(r)
        if len(self._win) < self._win.maxlen:
            return
        lo, hi = min(self._win), max(self._win)
        raw = 100.0 * (r - lo) / (hi - lo) if hi > lo else 50.0
        k = self._k.update(raw)
        self.values["k"] = k
        self.values["d"] = self._d.update(k) if k is not None else None


class CCI(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._win: deque[float] = deque(maxlen=period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._win.append((h + l + c) / 3)
        if len(self._win) < self._win.maxlen:
            return
        mean = sum(self._win) / len(self._win)
        dev = sum(abs(x - mean) for x in self._win) / len(self._win)
        self.values["value"] = (self._win[-1] - mean) / (0.015 * dev) if dev > 0 else 0.0


class WilliamsR(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._hi: deque[float] = deque(maxlen=period)
        self._lo: deque[float] = deque(maxlen=period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._hi.append(h)
        self._lo.append(l)
        if len(self._hi) == self._hi.maxlen:
            hh, ll = max(self._hi), min(self._lo)
            self.values["value"] = -100.0 * (hh - c) / (hh - ll) if hh > ll else -50.0


class MFI(_Base):
    """Money Flow Index: an RSI-like oscillator weighted by volume."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._pos: deque[float] = deque(maxlen=period)
        self._neg: deque[float] = deque(maxlen=period)
        self._prev: float | None = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        tp = (h + l + c) / 3
        if self._prev is not None:
            flow = tp * (v or 0.0)
            self._pos.append(flow if tp > self._prev else 0.0)
            self._neg.append(flow if tp < self._prev else 0.0)
            if len(self._pos) == self._pos.maxlen:
                p, n = sum(self._pos), sum(self._neg)
                self.values["value"] = 100.0 if n == 0 and p > 0 else (None if n == 0 else 100.0 - 100.0 / (1 + p / n))
        self._prev = tp


class Momentum(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._win: deque[float] = deque(maxlen=period + 1)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._win.append(c)
        if len(self._win) == self._win.maxlen:
            self.values["value"] = c - self._win[0]


class CMO(_Base):
    """Chande Momentum Oscillator."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._up: deque[float] = deque(maxlen=period)
        self._dn: deque[float] = deque(maxlen=period)
        self._prev: float | None = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        if self._prev is not None:
            self._up.append(max(c - self._prev, 0.0))
            self._dn.append(max(self._prev - c, 0.0))
            su, sd = sum(self._up), sum(self._dn)
            if len(self._up) == self._up.maxlen and su + sd > 0:
                self.values["value"] = 100.0 * (su - sd) / (su + sd)
        self._prev = c


class AwesomeOscillator(_Base):
    def __init__(self) -> None:
        super().__init__("value")
        self._fast, self._slow = SMA(5), SMA(34)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        a, b = self._fast.update((h + l) / 2), self._slow.update((h + l) / 2)
        self.values["value"] = a - b if a is not None and b is not None else None


class TRIX(_Base):
    """TradingView's TRIX: 10000 x the one-bar change of ln(close) smoothed three times with EMA(period)."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._e = (EMA(period), EMA(period), EMA(period))
        self._prev: float | None = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        x = self._e[0].update(math.log(c)) if c > 0 else None
        y = self._e[1].update(x) if x is not None else None
        z = self._e[2].update(y) if y is not None else None
        if z is not None:
            if self._prev is not None:
                self.values["value"] = 10000.0 * (z - self._prev)
            self._prev = z


class UltimateOscillator(_Base):
    """Ultimate Oscillator (7, 14, 28)."""

    def __init__(self) -> None:
        super().__init__("value")
        self._bp: deque[float] = deque(maxlen=28)
        self._tr: deque[float] = deque(maxlen=28)
        self._pc: float | None = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        if self._pc is not None:
            self._bp.append(c - min(l, self._pc))
            self._tr.append(max(h, self._pc) - min(l, self._pc))
            if len(self._tr) == 28:
                def avg(n: int) -> float:
                    t = sum(list(self._tr)[-n:])
                    return sum(list(self._bp)[-n:]) / t if t else 0.0

                self.values["value"] = 100.0 * (4 * avg(7) + 2 * avg(14) + avg(28)) / 7
        self._pc = c


# ---- volume ---------------------------------------------------------------------------------------
class OBV(_Base):
    def __init__(self) -> None:
        super().__init__("value")
        self._prev: float | None = None
        self._obv = 0.0

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        if self._prev is not None:
            self._obv += (v or 0.0) * (1 if c > self._prev else -1 if c < self._prev else 0)
        self._prev = c
        self.values["value"] = self._obv


class ADLine(_Base):
    """Accumulation / Distribution line."""

    def __init__(self) -> None:
        super().__init__("value")
        self._ad = 0.0

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._ad += ((c - l) - (h - c)) / (h - l) * (v or 0.0) if h > l else 0.0
        self.values["value"] = self._ad


class CMF(_Base):
    """Chaikin Money Flow."""

    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._mfv: deque[float] = deque(maxlen=period)
        self._vol: deque[float] = deque(maxlen=period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        vol = v or 0.0
        self._mfv.append(((c - l) - (h - c)) / (h - l) * vol if h > l else 0.0)
        self._vol.append(vol)
        if len(self._vol) == self._vol.maxlen and sum(self._vol) > 0:
            self.values["value"] = sum(self._mfv) / sum(self._vol)


# ---- channels and bands --------------------------------------------------------------------------
class Keltner(_Base):
    """Keltner channels: EMA(period) +/- factor x ATR(10), TradingView's defaults."""

    def __init__(self, period: int, factor: float) -> None:
        super().__init__("mid", "upper", "lower")
        self._ema, self._atr, self._f = EMA(period), ATR(10), factor

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        mid, atr = self._ema.update(c), self._atr.update(h, l, c)
        if mid is not None and atr is not None:
            self.values.update(mid=mid, upper=mid + self._f * atr, lower=mid - self._f * atr)


class Donchian(_Base):
    """Highest high / lowest low of the last `period` bars, the current bar included."""

    def __init__(self, period: int) -> None:
        super().__init__("high", "low", "mid")
        self._hi: deque[float] = deque(maxlen=period)
        self._lo: deque[float] = deque(maxlen=period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._hi.append(h)
        self._lo.append(l)
        if len(self._hi) == self._hi.maxlen:
            hi, lo = max(self._hi), min(self._lo)
            self.values.update(high=hi, low=lo, mid=(hi + lo) / 2)


class BollingerExtra(_Base):
    """%B (0 at the lower band, 1 at the upper) and bandwidth ((upper - lower) / middle)."""

    def __init__(self, period: int, factor: float) -> None:
        super().__init__("pctb", "width")
        self._bb = BollingerBands(period, factor)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        if self._bb.update(c) is None:
            return
        up, lo, mid = self._bb.upper, self._bb.lower, self._bb.mid
        if up is not None and lo is not None and mid:
            self.values.update(pctb=(c - lo) / (up - lo) if up > lo else 0.5, width=(up - lo) / mid)


class StdDev(_Base):
    def __init__(self, period: int) -> None:
        super().__init__("value")
        self._win: deque[float] = deque(maxlen=period)

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self._win.append(c)
        if len(self._win) == self._win.maxlen:
            m = sum(self._win) / len(self._win)
            self.values["value"] = math.sqrt(sum((x - m) ** 2 for x in self._win) / len(self._win))


class PriceTransforms(_Base):
    def __init__(self) -> None:
        super().__init__("hl2", "hlc3", "ohlc4", "tr")
        self._pc: float | None = None

    def update(self, o, h, l, c, v) -> None:  # noqa: ANN001, E741
        self.values.update(hl2=(h + l) / 2, hlc3=(h + l + c) / 3, ohlc4=(o + h + l + c) / 4, tr=true_range(h, l, self._pc))
        self._pc = c


def pivot_levels(high: float, low: float, close: float) -> dict[str, float]:
    """Classic floor-trader pivots from the previous day's range."""
    p = (high + low + close) / 3
    return {"pivot": p, "r1": 2 * p - low, "s1": 2 * p - high, "r2": p + (high - low), "s2": p - (high - low)}
