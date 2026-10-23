"""Spec extensions: candlestick patterns, swing levels, RSI divergence, futures volume, daily context, risk sizing."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import pytest

from trader.backtest.data import BacktestData
from trader.backtest.runner import BacktestRequest, execute
from trader.features import FeatureEngine
from trader.indicators import Pivots, candle_patterns
from trader.instruments.calendar import IST
from trader.market_data.candles import Candle
from trader.scripts.generate_sim_history import generate, trading_days
from trader.strategy.base import MarketState, Signal
from trader.strategy.rule_strategy import RuleStrategy
from trader.strategy.sizing import MAX_LOTS, lots_for_risk
from trader.strategy.spec import (
    MOMENTUM_V1,
    Cmp,
    Const,
    Ind,
    StrategySpec,
    mirror_condition,
    render_condition,
    render_english,
    semantic_errors,
)


def spec(**over):
    return StrategySpec.model_validate({**MOMENTUM_V1, **over})


def long_only(entry, **over):
    return spec(sides={"long": {"option": "CE", "entry": entry}}, filters={"time_window": ["09:15", "15:00"]}, **over)


def flag(name, period=None):
    d = {"ind": name} | ({"period": period} if period else {})
    return {"cmp": [d, ">", {"const": 0}]}


# ---- candlestick patterns ---------------------------------------------------------------------------
def test_candle_patterns_recognise_the_classic_shapes():
    assert candle_patterns([(10, 10.2, 9, 9.2), (9, 11, 8.9, 10.8)])["bull_engulfing"] == 1
    assert candle_patterns([(9, 11, 8.9, 10.8), (11, 11.1, 8.8, 9)])["bear_engulfing"] == 1
    assert candle_patterns([(10, 11, 9, 10.05)])["doji"] == 1
    assert candle_patterns([(10, 10.1, 6, 10.05)])["hammer"] == 1
    assert candle_patterns([(10, 14, 9.95, 10.05)])["shooting_star"] == 1
    morning = [(20, 20.2, 14, 14.5), (14, 14.3, 13.5, 13.8), (14, 19, 13.9, 18)]
    assert candle_patterns(morning)["morning_star"] == 1
    evening = [(14, 20, 13.8, 19.5), (20, 20.5, 19.6, 20.1), (20, 20.1, 15, 15.5)]
    assert candle_patterns(evening)["evening_star"] == 1
    quiet = candle_patterns([(10, 11, 9, 10.9), (10.9, 12, 10.5, 11.8)])  # two ordinary up bars
    assert sum(quiet.values()) == 0
    assert sum(candle_patterns([]).values()) == 0


# ---- swing points and RSI divergence --------------------------------------------------------------------
def feed(p: Pivots, highs, lows, rsis=None):
    for i, (h, l) in enumerate(zip(highs, lows)):
        p.update(h, l, None if rsis is None else rsis[i])


def test_swing_low_is_only_known_k_bars_after_it_happened():
    p = Pivots(2)
    lows = [10, 9, 8, 9, 10]  # the low of 8 is at index 2, confirmed by index 4
    feed(p, [x + 1 for x in lows[:4]], lows[:4])
    assert p.last_low is None  # not yet: two bars after it are needed
    p.update(11, 10)
    assert p.last_low == 8


def test_rsi_divergence_needs_a_lower_low_with_a_higher_rsi():
    p = Pivots(1)
    highs = [11, 10, 12, 13, 12, 14]
    lows = [10, 8, 11, 12, 6, 13]  # swing lows 8 (index 1) then 6 (index 4): price makes a lower low
    feed(p, highs, lows, [50, 30, 40, 45, 35, 50])  # RSI at the pivots: 30 then 35, higher
    assert p.divergence(10) == (True, False)
    q = Pivots(1)
    feed(q, highs, lows, [50, 30, 40, 45, 20, 50])  # RSI makes a lower low too: no divergence
    assert q.divergence(10) == (False, False)
    assert p.divergence(2) == (False, False)  # the pivots are further apart than the allowed window


# ---- spec: validation, mirror, text, sizing rules -------------------------------------------------------------
NEW = [("cs_bull_engulfing", None), ("cs_doji", None), ("rsi_bull_div", 20), ("swing_low", 3), ("volume", None),
       ("rel_volume", 20), ("daily_ema", 20), ("daily_rsi", 14)]


@pytest.mark.parametrize("name,period", NEW)
def test_new_operands_validate(name, period):
    long_only(flag(name, period))


def test_new_operands_check_their_period():
    with pytest.raises(Exception):
        long_only(flag("swing_low"))  # needs a period
    with pytest.raises(Exception):
        long_only(flag("cs_doji", 3))  # takes none


def test_pattern_mirrors_to_the_opposite_pattern_and_reads_as_english():
    c = Cmp(cmp=(Ind(ind="cs_bull_engulfing"), ">", Const(const=0)))
    m = mirror_condition(c)
    assert "cs_bear_engulfing" in repr(m)
    assert render_condition(c).strip("- ") == "bullish engulfing candle is present"
    assert render_condition(m).strip("- ") == "bearish engulfing candle is present"
    s = spec(sides={"long": {"option": "CE", "entry": flag("swing_low", 3)}, "short": {"option": "PE", "mirror_of": "long"}})
    assert "swing high" in render_english(s)  # the short side uses the mirrored level


def test_min_reward_risk_is_enforced_and_sizing_shows_in_the_text():
    ok = spec(exits={"stop_loss_pct": 20, "target_pct": 60}, risk={"max_trades_per_day": 2, "min_reward_risk": 3, "risk_per_trade_pct": 1})
    assert semantic_errors(ok) == []
    assert "1% of capital" in render_english(ok)
    bad = spec(exits={"stop_loss_pct": 25, "target_pct": 50}, risk={"min_reward_risk": 3})
    assert any("reward-to-risk" in e for e in semantic_errors(bad))
    with pytest.raises(Exception):
        spec(risk={"risk_per_trade_pct": 50})


def test_lots_for_risk():
    # 1% of 100,000 = 1,000 budget; premium 100 x 25% stop x lot 75 = 1,875 per lot -> not even one lot
    assert lots_for_risk(100_000, 1, 100, 25, 75) == 0
    assert lots_for_risk(1_000_000, 1, 100, 25, 75) == 5  # 10,000 / 1,875
    assert lots_for_risk(1e12, 5, 100, 25, 75) == MAX_LOTS
    assert lots_for_risk(100_000, 1, 0, 25, 75) == 0


# ---- running the operands -----------------------------------------------------------------------------------------
def bar(day, minute, o, h, l, c):
    t = datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(timezone.utc) + timedelta(minutes=minute)
    return Candle("N", "5m", t, o, h, l, c, 0, True)


def run(s, candles, futures=None):
    rs, fe, out = RuleStrategy(s), FeatureEngine(5), []
    for b in candles:
        out.append(rs.generate_signal(MarketState("NIFTY", fe.update(b, (futures or {}).get(b.ts)))))
    return [i for i, o in enumerate(out) if isinstance(o, Signal)]


def test_engulfing_pattern_triggers_an_entry_on_its_bar():
    d = date(2026, 10, 21)
    cs = [bar(d, 0, 100, 101, 99, 100), bar(d, 5, 100, 100.5, 98, 98.5), bar(d, 10, 98, 101.5, 97.9, 101)]
    assert run(long_only(flag("cs_bull_engulfing")), cs) == [2]


def test_swing_low_acts_as_a_dynamic_support_level():
    d = date(2026, 10, 21)
    closes = [(105, 104), (104, 103), (103, 100), (104, 103), (105, 104), (106, 105), (105, 99)]  # (high, low)
    cs = [bar(d, 5 * i, h, h + 0.5, lo, lo + 0.2) for i, (h, lo) in enumerate(closes)]
    below = {"cmp": [{"ind": "close"}, "<", {"ind": "swing_low", "period": 2}]}
    assert run(long_only(below), cs) == [6]  # only the last bar closes under the confirmed swing low (100)


def test_volume_filters_use_futures_volume_and_never_fire_without_it():
    d = date(2026, 10, 21)
    cs = [bar(d, 5 * i, 100, 101, 99, 100) for i in range(8)]
    vols = [100, 100, 100, 100, 100, 100, 100, 400]
    fut = {c.ts: Candle("F", "5m", c.ts, 100, 101, 99, 100, v, True) for c, v in zip(cs, vols)}
    heavy = {"cmp": [{"ind": "rel_volume", "period": 5}, ">=", {"const": 3}]}
    assert run(long_only(heavy), cs, fut) == [7]  # 400 against a 100 average
    assert run(long_only(heavy), cs, None) == []  # no futures data: unknown, so no trade


def test_daily_context_uses_only_completed_days():
    days = [date(2026, 10, 12) + timedelta(days=i) for i in range(4)]
    cs = [bar(d, 5 * k, 100 + 10 * i, 101 + 10 * i, 99 + 10 * i, 100 + 10 * i) for i, d in enumerate(days) for k in range(2)]
    above = {"cmp": [{"ind": "close"}, ">", {"ind": "daily_sma", "period": 2}]}
    got = run(long_only(above), cs)
    # the third day (index 4) is the first with two completed days behind it: SMA(2) of 100 and 110 = 105 < 120
    assert got and min(got) == 4  # never earlier: the first two days have no complete daily average


# ---- backtest sizing ---------------------------------------------------------------------------------------------
def sim_data():
    tokens = {"NIFTY": "I", "NIFTY-FUT": "F", "BANKNIFTY": "BI", "BANKNIFTY-FUT": "BF", "INDIAVIX": "V"}
    cs = generate(trading_days(15, date(2026, 10, 16)), tokens, seed=3, timeframes=("5m",))
    return BacktestData("simulated", [c for c in cs if c.token == "I"],
                        {c.ts: c for c in cs if c.token == "F"}, {c.ts: c.close for c in cs if c.token == "V"})


def momentum(**risk):
    s = dict(MOMENTUM_V1)
    s["filters"] = {**MOMENTUM_V1["filters"], "all": [{"cmp": [{"ind": "adx", "period": 14}, ">=", {"const": 10}]}]}
    s["risk"] = {"max_trades_per_day": 3, **risk}
    return s


def test_backtest_sizes_by_risk_and_skips_when_one_lot_is_too_big():
    data = sim_data()
    fixed = execute(BacktestRequest("NIFTY", source="simulated", strategy="spec", spec=momentum()), data)
    assert fixed["trades"]
    tiny = execute(BacktestRequest("NIFTY", source="simulated", strategy="spec", spec=momentum(risk_per_trade_pct=0.01)), data)
    assert tiny["trades"] == [] and tiny["skipped_entries"].get("risk budget below one lot", 0) > 0
    sized = execute(BacktestRequest("NIFTY", source="simulated", strategy="spec", spec=momentum(risk_per_trade_pct=5)), data)
    lots = {t["quantity"] // fixed["trades"][0]["quantity"] for t in sized["trades"]}
    assert sized["trades"] and max(lots) >= 1
