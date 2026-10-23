"""Golden values (PRD §24.4 B3.4): our incremental indicators against TA-Lib on one fixed price series.

The values compared are produced exactly as a strategy and the chart see them (FeatureEngine + the interpreter's
indicator bank, through `chart_series.compute`). TA-Lib is a test-only dependency; the test is skipped without it.
Only the last 150 bars are compared, after every indicator's warm-up, so seeding differences have decayed.
"""

from __future__ import annotations

import math
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

talib = pytest.importorskip("talib")
np = pytest.importorskip("numpy")

from trader.backtest import chart_series  # noqa: E402
from trader.instruments.calendar import IST  # noqa: E402
from trader.market_data.candles import Candle  # noqa: E402

N, TAIL = 1500, 150


def series():
    """A deterministic random walk with realistic intraday ranges and volume, over whole 5m sessions."""
    rng = random.Random(7)
    bars, fut = [], {}
    px = 25000.0
    day = date(2026, 1, 5)
    while len(bars) < N:
        if day.weekday() < 5:
            t0 = datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(timezone.utc)
            for i in range(75):
                if len(bars) == N:
                    break
                o = px
                c = o + rng.gauss(0, 18)
                h = max(o, c) + abs(rng.gauss(0, 9))
                lo = min(o, c) - abs(rng.gauss(0, 9))
                v = rng.randint(5_000, 50_000)
                ts = t0 + timedelta(minutes=5 * i)
                bars.append(Candle("IDX", "5m", ts, o, h, lo, c, 0, True))
                fut[ts] = Candle("FUT", "5m", ts, o, h, lo, c, v, True)
                px = c
        day += timedelta(days=1)
    return bars, fut


BARS, FUT = series()
O = np.array([b.open for b in BARS])
H = np.array([b.high for b in BARS])
L = np.array([b.low for b in BARS])
C = np.array([b.close for b in BARS])
V = np.array([float(FUT[b.ts].volume) for b in BARS])


def ours(name: str, period: int | None = None, mult: float | None = None) -> np.ndarray:
    spec = chart_series.SeriesSpec((name, period, mult, None), "user")
    pts = chart_series.compute(BARS, FUT, {}, [spec], 5)[spec.id]
    out = np.full(N, np.nan)
    idx = {int(b.ts.timestamp()): i for i, b in enumerate(BARS)}
    for t, v in pts:
        out[idx[t]] = v
    return out


def same(a: np.ndarray, b: np.ndarray, rel: float = 1e-3, abs_: float = 1e-3) -> None:
    a, b = a[-TAIL:], b[-TAIL:]
    assert not np.isnan(a).any() and not np.isnan(b).any()
    bad = [(i, x, y) for i, (x, y) in enumerate(zip(a, b)) if not math.isclose(x, y, rel_tol=rel, abs_tol=abs_)]
    assert not bad, f"{len(bad)} of {TAIL} differ, first: {bad[0]}"


@pytest.mark.parametrize("name,period,ref", [
    ("ema", 20, lambda: talib.EMA(C, 20)),
    ("sma", 20, lambda: talib.SMA(C, 20)),
    ("wma", 20, lambda: talib.WMA(C, 20)),
    ("dema", 20, lambda: talib.DEMA(C, 20)),
    ("tema", 20, lambda: talib.TEMA(C, 20)),
    ("rsi", 14, lambda: talib.RSI(C, 14)),
    ("atr", 14, lambda: talib.ATR(H, L, C, 14)),
    ("adx", 14, lambda: talib.ADX(H, L, C, 14)),
    ("plus_di", 14, lambda: talib.PLUS_DI(H, L, C, 14)),
    ("minus_di", 14, lambda: talib.MINUS_DI(H, L, C, 14)),
    ("roc", 10, lambda: talib.ROC(C, 10)),
    ("mom", 10, lambda: talib.MOM(C, 10)),
    ("cci", 20, lambda: talib.CCI(H, L, C, 20)),
    ("williams_r", 14, lambda: talib.WILLR(H, L, C, 14)),
    ("mfi", 14, lambda: talib.MFI(H, L, C, V, 14)),
    ("linreg", 25, lambda: talib.LINEARREG(C, 25)),
    ("stddev", 20, lambda: talib.STDDEV(C, 20, 1)),
    ("aroon_up", 14, lambda: talib.AROON(H, L, 14)[1]),
    ("aroon_down", 14, lambda: talib.AROON(H, L, 14)[0]),
    ("aroon_osc", 14, lambda: talib.AROONOSC(H, L, 14)),
    ("bb_upper", 20, lambda: talib.BBANDS(C, 20, 2, 2)[0]),
    ("bb_mid", 20, lambda: talib.BBANDS(C, 20, 2, 2)[1]),
    ("bb_lower", 20, lambda: talib.BBANDS(C, 20, 2, 2)[2]),
    ("donchian_high", 20, lambda: talib.MAX(H, 20)),
    ("donchian_low", 20, lambda: talib.MIN(L, 20)),
    ("donchian_mid", 20, lambda: talib.MIDPRICE(H, L, 20)),
])
def test_indicator_matches_talib(name, period, ref):
    same(ours(name, period), ref())


@pytest.mark.parametrize("name,ref", [
    ("macd", lambda: talib.MACD(C, 12, 26, 9)[0]),
    ("macd_signal", lambda: talib.MACD(C, 12, 26, 9)[1]),
    ("macd_hist", lambda: talib.MACD(C, 12, 26, 9)[2]),
    ("obv", lambda: talib.OBV(C, V)),
    ("ad_line", lambda: talib.AD(H, L, C, V)),
    ("ult_osc", lambda: talib.ULTOSC(H, L, C, 7, 14, 28)),
    ("psar", lambda: talib.SAR(H, L, 0.02, 0.2)),
])
def test_unparameterised_indicator_matches_talib(name, ref):
    a, b = ours(name), ref()
    if name in ("obv", "ad_line"):  # cumulative from different first bars: compare the changes, not the levels
        a, b = np.diff(a), np.diff(b)
    same(a, b)


def test_stochastic_matches_talib_slow_stochastic():
    k, d = talib.STOCH(H, L, C, 14, 3, 0, 3, 0)  # %K smoothed 3, %D = SMA(3) of it: TradingView's (14, 3, 3)
    same(ours("stoch_k", 14), k)
    same(ours("stoch_d", 14), d)


def test_keltner_follows_tradingviews_default_formula():
    """TradingView's defaults: basis = EMA(close, n); bands = basis +/- mult x ATR(10). TA-Lib has no Keltner."""
    basis, atr = talib.EMA(C, 20), talib.ATR(H, L, C, 10)
    same(ours("keltner_mid", 20, 2.0), basis)
    same(ours("keltner_upper", 20, 2.0), basis + 2 * atr)
    same(ours("keltner_lower", 20, 2.0), basis - 2 * atr)


def test_every_catalogue_indicator_runs_through_the_engine():
    """Each operand a spec may name produces values when fed bars (a `wma` operand once crashed the interpreter)."""
    from trader.strategy import catalogue
    from trader.strategy.spec import Ind

    vix = {b.ts: 14.0 for b in BARS}
    silent = []
    for e in catalogue.catalogue():
        per = e["period"]["default"] if e["period"] else None
        spec = chart_series.SeriesSpec(Ind(ind=e["name"], period=per).key, "user")
        if not chart_series.compute(BARS, FUT, vix, [spec], 5)[spec.id] and not e["name"].startswith("daily_"):
            silent.append(e["name"])  # daily_* need more days of history than this series has
    assert silent == []


def test_hull_ma_follows_its_formula():
    """HMA(n) = WMA(2 x WMA(n/2) - WMA(n), sqrt(n)), TradingView's definition."""
    n = 16
    raw = 2 * talib.WMA(C, n // 2) - talib.WMA(C, n)
    first = np.where(~np.isnan(raw))[0][0]
    ref = np.full(N, np.nan)
    ref[first:] = talib.WMA(raw[first:], int(math.sqrt(n)))
    same(ours("hma", n), ref)
