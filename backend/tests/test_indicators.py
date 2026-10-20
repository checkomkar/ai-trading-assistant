import math
import random
from datetime import date

import pytest

from trader.indicators import ADX, ATR, EMA, MACD, ROC, RSI, SMA, BollingerBands, RollingExtreme, SessionVWAP

rng = random.Random(7)
CLOSES = [100.0]
for _ in range(300):
    CLOSES.append(CLOSES[-1] * math.exp(rng.gauss(0, 0.01)))
BARS = [(c * (1 + abs(rng.gauss(0, 0.004))), c * (1 - abs(rng.gauss(0, 0.004))), c) for c in CLOSES]


# ---- independent batch reference implementations --------------------------------------
def ref_ema(xs, n):
    out, v = [], None
    for i, x in enumerate(xs):
        if i + 1 < n:
            out.append(None)
            continue
        v = sum(xs[:n]) / n if i + 1 == n else x * 2 / (n + 1) + v * (1 - 2 / (n + 1))
        out.append(v)
    return out


def ref_rma(xs, n):
    out, v = [], None
    for i, x in enumerate(xs):
        if i + 1 < n:
            out.append(None)
            continue
        v = sum(xs[:n]) / n if i + 1 == n else (v * (n - 1) + x) / n
        out.append(v)
    return out


def ref_rsi(closes, n=14):
    ch = [b - a for a, b in zip(closes, closes[1:])]
    g, l = ref_rma([max(c, 0) for c in ch], n), ref_rma([max(-c, 0) for c in ch], n)
    return [None] + [None if a is None else (100 if b == 0 else 100 - 100 / (1 + a / b)) for a, b in zip(g, l)]


def ref_atr(bars, n=14):
    trs = []
    for i, (h, l, c) in enumerate(bars):
        pc = bars[i - 1][2] if i else None
        trs.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
    return ref_rma(trs, n)


def approx_series(inc, ref):
    for a, b in zip(inc, ref):
        assert (a is None) == (b is None)
        if a is not None:
            assert a == pytest.approx(b, rel=1e-9, abs=1e-9)


def test_sma_ema_match_reference():
    sma, ema = SMA(10), EMA(9)
    s_out = [sma.update(x) for x in CLOSES]
    e_out = [ema.update(x) for x in CLOSES]
    approx_series(e_out, ref_ema(CLOSES, 9))
    assert s_out[9] == pytest.approx(sum(CLOSES[:10]) / 10) and s_out[8] is None
    assert s_out[-1] == pytest.approx(sum(CLOSES[-10:]) / 10)


def test_rsi_and_atr_match_wilder_reference():
    rsi, atr = RSI(14), ATR(14)
    approx_series([rsi.update(c) for c in CLOSES], ref_rsi(CLOSES))
    approx_series([atr.update(*b) for b in BARS], ref_atr(BARS))


def test_rsi_extremes():
    up = RSI(14)
    for x in range(1, 30):
        up.update(float(x))
    assert up.value == 100.0
    down = RSI(14)
    for x in range(30, 1, -1):
        down.update(float(x))
    assert down.value == pytest.approx(0.0)


def test_adx_trending_vs_flat():
    trend, flat = ADX(14), ADX(14)
    for i in range(100):
        trend.update(100 + i + 0.5, 100 + i - 0.5, 100 + i)
        flat.update(100.5 + (i % 2), 99.5 + (i % 2), 100 + (i % 2))
    assert trend.value > 50 and trend.plus_di > trend.minus_di
    assert flat.value < 25


def test_macd_bollinger_roc():
    m = MACD()
    for c in CLOSES:
        m.update(c)
    ema12, ema26 = ref_ema(CLOSES, 12)[-1], ref_ema(CLOSES, 26)[-1]
    assert m.macd == pytest.approx(ema12 - ema26)
    bb = BollingerBands(20, 2)
    for c in CLOSES:
        bb.update(c)
    w = CLOSES[-20:]
    mu = sum(w) / 20
    sd = math.sqrt(sum((x - mu) ** 2 for x in w) / 20)
    assert (bb.mid, bb.upper) == (pytest.approx(mu), pytest.approx(mu + 2 * sd))
    roc = ROC(10)
    for c in CLOSES:
        roc.update(c)
    assert roc.value == pytest.approx((CLOSES[-1] / CLOSES[-11] - 1) * 100)


def test_rolling_extreme_excludes_current_bar():
    r = RollingExtreme(3)
    for h, l in [(10, 5), (12, 6), (11, 4)]:
        r.update(h, l)
    assert r.high is None  # needs a full window of *previous* bars first
    r.update(20, 1)
    assert r.high == 12 and r.low == 4  # the current bar (20 / 1) is not included


def test_session_vwap_resets_daily():
    v = SessionVWAP()
    v.update(date(2026, 1, 1), 11, 9, 10, 100)
    assert v.update(date(2026, 1, 1), 21, 19, 20, 300) == pytest.approx((10 * 100 + 20 * 300) / 400)
    assert v.update(date(2026, 1, 2), 51, 49, 50, 10) == pytest.approx(50)
    assert v.update(date(2026, 1, 2), 60, 60, 60, 0) == pytest.approx(50)  # zero volume ignored
