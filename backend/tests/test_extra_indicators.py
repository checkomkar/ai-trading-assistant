"""The extra (TradingView-style) indicators and their use in strategy specs."""

from __future__ import annotations

import math
import random
from datetime import date, timedelta

import pytest

from trader.backtest.equity import EquityConfig, EquityEngine
from trader.backtest.equity_costs import EquityCosts
from trader.indicators import extra as X
from trader.instruments import equities as eq
from trader.market_data.candles import Candle
from trader.strategy.spec import INDICATORS, StrategySpec, render_english, semantic_errors


def bars(n=300, seed=3):
    rnd, px, out = random.Random(seed), 100.0, []
    for _ in range(n):
        o = px
        c = o * (1 + rnd.uniform(-0.02, 0.02))
        out.append((o, max(o, c) * (1 + rnd.uniform(0, 0.01)), min(o, c) * (1 - rnd.uniform(0, 0.01)), c, rnd.randint(100, 1000)))
        px = c
    return out


def run(ind, data):
    outs = []
    for b in data:
        ind.update(*b)
        outs.append(dict(ind.values))
    return outs


# ---- averages -----------------------------------------------------------------------------------------
def wma_ref(xs, n):
    return sum(w * x for w, x in enumerate(xs[-n:], 1)) / (n * (n + 1) / 2)


def test_wma_and_hma_match_their_definitions():
    w = X.WMA(3)
    assert [w.update(x) for x in (1, 2, 3)] == [None, None, pytest.approx(14 / 6)]
    data = bars(120)
    closes = [b[3] for b in data]
    n = 21
    got = run(X.HMA(n), data)
    raw = [2 * wma_ref(closes[: i + 1], n // 2) - wma_ref(closes[: i + 1], n) for i in range(n - 1, len(closes))]
    assert got[-1]["value"] == pytest.approx(wma_ref(raw, round(math.sqrt(n)))) and got[n - 2]["value"] is None


def test_dema_tema_rma_linreg_vwma():
    data = bars(200)
    for ind in (X.DEMA(10), X.TEMA(10), X.RMA(10)):
        assert run(ind, data)[-1]["value"] is not None
    line = [(i, i, i, 10 + 2 * i, 1) for i in range(30)]  # a perfect line: the regression value is the last close
    assert run(X.LinReg(10), line)[-1]["value"] == pytest.approx(10 + 2 * 29)
    v = run(X.VWMA(3), [(1, 1, 1, 10, 1), (1, 1, 1, 20, 1), (1, 1, 1, 30, 2)])[-1]["value"]
    assert v == pytest.approx((10 + 20 + 60) / 4)
    assert run(X.VWMA(3), [(1, 1, 1, 10, None)] * 3)[-1]["value"] is None  # no volume known: unknown, not invented


# ---- trend ----------------------------------------------------------------------------------------------
def test_supertrend_follows_trends_and_only_ratchets_one_way():
    up = [(100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i in range(40)]
    down = [(140 - 2 * i, 141 - 2 * i, 138 - 2 * i, 139 - 2 * i, 10) for i in range(30)]
    outs = run(X.Supertrend(10, 3), up + down)
    assert outs[5]["dir"] is None  # warming up
    assert outs[39]["dir"] == 1 and outs[39]["line"] < up[-1][3]
    lines = [o["line"] for o in outs[20:40]]
    assert lines == sorted(lines)  # the support line never drops while the trend is up
    assert outs[-1]["dir"] == -1 and outs[-1]["line"] > down[-1][3]


def test_parabolic_sar_flips_when_price_breaks_it():
    up = [(100 + i, 101 + i, 99.5 + i, 100.5 + i, 1) for i in range(25)]
    crash = [(124 - 3 * i, 124.5 - 3 * i, 121 - 3 * i, 122 - 3 * i, 1) for i in range(12)]
    outs = [o["value"] for o in run(X.ParabolicSAR(), up + crash)]
    assert outs[20] < up[20][2]  # under the lows in an uptrend
    assert outs[-1] > crash[-1][1]  # over the highs after the reversal
    assert outs[0] is None


def test_aroon_vortex_ichimoku():
    rising = [(i, i + 1, i - 1, i, 1) for i in range(40)]
    a = run(X.Aroon(14), rising)[-1]
    assert a["up"] == 100 and a["down"] < 100 and a["osc"] > 0
    v = run(X.Vortex(14), rising)[-1]
    assert v["plus"] > v["minus"]
    data = bars(120)
    hist = run(X.Ichimoku(), data)
    now, past = hist[-1], hist[-26]
    # span A today = the (tenkan + kijun) / 2 of 25 bars ago; span B = the 52-bar midpoint of 25 bars ago
    assert now["span_a"] == pytest.approx((past["tenkan"] + past["kijun"]) / 2)
    window = data[len(data) - 25 - 52: len(data) - 25]
    assert now["span_b"] == pytest.approx((max(b[1] for b in window) + min(b[2] for b in window)) / 2)
    assert hist[40]["span_a"] is None  # the cloud needs 25 + 26 bars


# ---- oscillators ----------------------------------------------------------------------------------------
def test_stochastic_cci_williams_and_the_rest():
    rising = [(i, i + 1, i - 1, i + 0.9, 5) for i in range(50)]
    s = run(X.Stochastic(14), rising)[-1]
    assert 90 < s["k"] <= 100 and 90 < s["d"] <= 100
    assert run(X.Stochastic(14), [(10, 10, 10, 10, 1)] * 40)[-1]["k"] == 50
    data = bars(80)
    tps = [(h + l + c) / 3 for _, h, l, c, _ in data[-20:]]
    m = sum(tps) / 20
    assert run(X.CCI(20), data)[-1]["value"] == pytest.approx((tps[-1] - m) / (0.015 * sum(abs(t - m) for t in tps) / 20))
    wr = run(X.WilliamsR(14), data)[-1]["value"]
    hh, ll = max(b[1] for b in data[-14:]), min(b[2] for b in data[-14:])
    assert wr == pytest.approx(-100 * (hh - data[-1][3]) / (hh - ll)) and -100 <= wr <= 0
    for ind in (X.MFI(14), X.CMO(14), X.UltimateOscillator(), X.StochRSI(14), X.TRIX(15), X.AwesomeOscillator(), X.Momentum(10)):
        out = run(ind, bars(250))[-1]
        assert all(v is not None for v in out.values()), type(ind).__name__
    assert 0 <= run(X.MFI(14), bars(100))[-1]["value"] <= 100
    assert run(X.Momentum(5), [(0, 0, 0, i, 1) for i in range(10)])[-1]["value"] == 5


def test_volume_indicators_use_volume_and_stay_unknown_without_it():
    assert run(X.OBV(), [(1, 1, 1, 1, 10), (1, 2, 1, 2, 10), (1, 2, 1, 1.5, 20)])[-1]["value"] == 10 - 20
    assert run(X.ADLine(), [(1, 2, 1, 2, 10)])[-1]["value"] == 10  # close at the high: full positive flow
    assert run(X.CMF(2), [(1, 2, 1, 2, 10), (1, 2, 1, 2, 10)])[-1]["value"] == 1
    assert run(X.CMF(2), [(1, 2, 1, 2, None), (1, 2, 1, 2, None)])[-1]["value"] is None


# ---- bands ---------------------------------------------------------------------------------------------------
def test_channels_and_prices():
    data = bars(100)
    d = run(X.Donchian(20), data)[-1]
    assert d["high"] == max(b[1] for b in data[-20:]) and d["low"] == min(b[2] for b in data[-20:])
    k = run(X.Keltner(20, 2), data)[-1]
    assert k["lower"] < k["mid"] < k["upper"] and (k["upper"] - k["mid"]) == pytest.approx(k["mid"] - k["lower"])
    closes = [x[3] for x in data[-20:]]
    m = sum(closes) / 20
    sd = math.sqrt(sum((c - m) ** 2 for c in closes) / 20)
    assert run(X.StdDev(20), data)[-1]["value"] == pytest.approx(sd)
    b = run(X.BollingerExtra(20, 2), data)[-1]
    assert b["pctb"] == pytest.approx((closes[-1] - (m - 2 * sd)) / (4 * sd)) and b["width"] == pytest.approx(4 * sd / m)
    p = run(X.PriceTransforms(), [(10, 14, 8, 12, 1), (12, 15, 11, 13, 1)])[-1]
    assert p["hl2"] == 13 and p["hlc3"] == pytest.approx((15 + 11 + 13) / 3) and p["ohlc4"] == 12.75 and p["tr"] == 4
    lv = X.pivot_levels(110, 90, 100)
    assert lv["pivot"] == 100 and lv["r1"] == 110 and lv["s1"] == 90 and lv["r2"] == 120 and lv["s2"] == 80


# ---- in strategy specs ----------------------------------------------------------------------------------------
def spec(entry, market="options"):
    base = {"name": "x", "sides": {"long": {"entry": entry}}, "exits": {"stop_loss_pct": 5, "target_pct": 10}}
    if market == "equity":
        base |= {"market": "equity", "underlyings": ["TCS"], "timeframe": "1d"}
    else:
        base["sides"]["long"]["option"] = "CE"
    return StrategySpec.model_validate(base)


def test_operand_rules_period_mult_and_names():
    ok = spec({"cmp": [{"ind": "supertrend", "period": 10, "mult": 2}, "<", {"ind": "close"}]})
    assert semantic_errors(ok) == [] and "Supertrend(10, 2)" in render_english(ok)
    assert "Supertrend(10, 3)" in render_english(spec({"cmp": [{"ind": "supertrend", "period": 10}, "<", {"ind": "close"}]}))
    for bad in ({"ind": "hma"}, {"ind": "hma", "period": 1}, {"ind": "hma", "period": 20, "mult": 2}, {"ind": "psar", "period": 5}):
        with pytest.raises(Exception):
            spec({"cmp": [bad, ">", {"const": 1}]})
    for name in ("hma", "supertrend", "psar", "stoch_k", "cci", "ichi_span_a", "pivot_r1", "obv", "keltner_upper", "donchian_high"):
        assert name in INDICATORS


def mirrored(cond):
    text = render_english(StrategySpec.model_validate({
        "name": "m", "sides": {"long": {"option": "CE", "entry": cond}, "short": {"option": "PE", "mirror_of": "long"}},
        "exits": {"stop_loss_pct": 5, "target_pct": 10}}))
    return text.split("put (PE)")[1]


def test_mirroring_the_new_operands_reads_correctly():
    t = mirrored({"cmp": [{"ind": "supertrend_dir", "period": 10}, ">", {"const": 0}]})
    assert "is below 0" in t and "-0" not in t
    assert "is below 20" in mirrored({"cmp": [{"ind": "stoch_k", "period": 14}, ">", {"const": 80}]})
    assert "below lower Keltner" in mirrored({"cmp": [{"ind": "close"}, ">", {"ind": "keltner_upper", "period": 20}]})
    assert "is below -80" in mirrored({"cmp": [{"ind": "williams_r", "period": 14}, ">", {"const": -20}]})


def daily(n=400, drift=0.0006, seed=5):
    rnd, px, out, d = random.Random(seed), 100.0, [], date(2024, 1, 1)
    for _ in range(n):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = px
        c = o * (1 + rnd.uniform(-0.02, 0.02) + drift)
        out.append(Candle(eq.token("TCS"), "1d", eq.day_stamp(d), o, max(o, c) * 1.004, min(o, c) * 0.996, c,
                          rnd.randint(1000, 5000), closed=True))
        px, d = c, d + timedelta(days=1)
    return out


@pytest.mark.parametrize("cond", [
    {"all": [{"cmp": [{"ind": "close"}, ">", {"ind": "hma", "period": 21}]}, {"cmp": [{"ind": "supertrend_dir", "period": 10}, ">", {"const": 0}]}]},
    {"all": [{"cmp": [{"ind": "stoch_k", "period": 14}, "<", {"const": 20}]}, {"cmp": [{"ind": "cci", "period": 20}, "<", {"const": -100}]}]},
    {"all": [{"cmp": [{"ind": "close"}, ">", {"ind": "keltner_upper", "period": 20}]}, {"cmp": [{"ind": "mfi", "period": 14}, ">", {"const": 50}]}]},
    {"all": [{"cmp": [{"ind": "close"}, ">", {"ind": "ichi_span_a"}]}, {"cmp": [{"ind": "close"}, ">", {"ind": "psar"}]},
             {"cmp": [{"ind": "aroon_osc", "period": 25}, ">", {"const": 0}]}, {"cmp": [{"ind": "pivot_r1"}, ">", {"ind": "pivot"}]}]},
])
def test_new_operands_drive_the_stock_engine_end_to_end(cond):
    sp = spec(cond, market="equity")
    assert semantic_errors(sp) == []
    res = EquityEngine(sp, EquityConfig(100_000, 0.0, EquityCosts(0, 0, 0, 0, 0, 0, 0, 0))).run({"TCS": daily()}, date(2024, 9, 2))
    assert res.days and (res.signals > 0 or res.trades or res.open_positions)  # the rules fired on a 400-day drifting series


def test_the_translator_is_told_about_the_new_operands():
    from trader.ai.strategy_translator import SYSTEM_PROMPT

    for name in ("supertrend", "hma", "stoch_k", "ichi_span_a", "keltner_upper", "psar", "mult"):
        assert name in SYSTEM_PROMPT
