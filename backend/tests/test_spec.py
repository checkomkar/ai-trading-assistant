from datetime import date, datetime, time, timedelta, timezone

import pytest
from pydantic import ValidationError

from trader.backtest.data import BacktestData
from trader.backtest.runner import BacktestRequest, execute
from trader.features import FeatureEngine
from trader.instruments.calendar import IST
from trader.market_data.candles import Candle
from trader.scripts.generate_sim_history import generate, trading_days
from trader.strategy.base import Direction, MarketState, NoTrade, Signal
from trader.strategy.rule_strategy import RuleStrategy
from trader.strategy.spec import (
    MOMENTUM_V1,
    Cmp,
    StrategySpec,
    mirror_condition,
    render_english,
    resolved_entry,
    semantic_errors,
)


def spec(**over):
    return StrategySpec.model_validate({**MOMENTUM_V1, **over})


# ---- schema & semantics -------------------------------------------------------------------------
def test_momentum_reference_spec_is_valid():
    s = spec()
    assert semantic_errors(s) == [] and len(s.hash) == 16
    assert spec().hash == s.hash  # stable


@pytest.mark.parametrize("bad", [
    {"ind": "ema"},  # period missing
    {"ind": "ema", "period": 1},  # out of bounds
    {"ind": "close", "period": 5},  # takes no period
    {"ind": "future_close"},  # not in whitelist
    {"ind": "close", "shift": -1},  # look-ahead is unrepresentable
])
def test_operand_validation(bad):
    with pytest.raises(ValidationError):
        Cmp.model_validate({"cmp": [bad, ">", {"const": 1}]})


def test_structural_errors():
    with pytest.raises(ValidationError):  # extra keys forbidden
        spec(unknown=1)
    with pytest.raises(ValidationError):  # no sides
        spec(sides={})
    with pytest.raises(ValidationError):  # mirror of a missing side
        spec(sides={"short": {"option": "PE", "mirror_of": "long"}})
    with pytest.raises(ValidationError):  # bad time window
        spec(filters={"time_window": ["15:00", "10:00"]})
    with pytest.raises(ValidationError):
        spec(exits={"stop_loss_pct": 120, "target_pct": 50})


def test_semantic_errors():
    s = spec(sides={"long": {"option": "CE", "entry": {"cmp": [{"ind": "close"}, ">", {"ind": "vwap"}]}}})
    assert any("futures basis" in e for e in semantic_errors(s))  # index price vs futures VWAP
    s = spec(sides={"long": {"option": "PE", "entry": {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 60}]}}})
    assert any("long side must buy CE" in e for e in semantic_errors(s))
    s = spec(sides={"long": {"option": "CE", "entry": {"cmp": [{"const": 1}, ">", {"const": 0}]}}})
    assert any("two constants" in e for e in semantic_errors(s))


# ---- mirroring & english ---------------------------------------------------------------------------
def test_mirror_flips_and_reflects():
    long = resolved_entry(spec(), "long")
    short = resolved_entry(spec(), "short")
    assert short == mirror_condition(long)
    parts = {(c.cmp[0].ind if hasattr(c.cmp[0], "ind") else "const", c.cmp[1],
              c.cmp[2].const if hasattr(c.cmp[2], "const") else c.cmp[2].ind) for c in short.all}
    assert ("rsi", "<", 45.0) in parts  # 55 reflected around 50
    assert ("close", "<", "lowest") in parts  # highest -> lowest
    assert ("fut_close", "<", "vwap") in parts
    assert mirror_condition(short) == long  # involution


def test_english_rendering_is_deterministic_and_complete():
    text = render_english(spec())
    assert text == render_english(spec())
    for phrase in ("Buy the ATM call (CE) when ALL of these are true", "RSI(14) is above 55",
                   "highest high of the previous 6 bars", "mirror of the long rules", "RSI(14) is below 45",
                   "stop loss 25%", "trail the stop 15%", "at most 3 trades per day", "ADX(14) is at or above 18"):
        assert phrase in text, phrase


# ---- interpreter -------------------------------------------------------------------------------------
def bars(closes, day=date(2026, 10, 21)):
    t0 = datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(timezone.utc)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        out.append(Candle("N", "5m", t0 + timedelta(minutes=5 * i), prev, max(prev, c) + 1, min(prev, c) - 1, c, 0, True))
        prev = c
    return out


def run_rule(s, closes):
    rs, fe, out = RuleStrategy(s), FeatureEngine(5), []
    for b in bars(closes):
        out.append(rs.generate_signal(MarketState("NIFTY", fe.update(b))))
    return rs, out


def test_cross_fires_once_and_warmup_is_unknown():
    s = spec(sides={"long": {"option": "CE", "entry": {"cross": [{"ind": "close"}, "above", {"ind": "sma", "period": 3}]}}},
             filters={"time_window": ["09:15", "15:00"]})
    closes = [100, 100, 100, 99, 98, 97, 103, 104, 105, 106]  # dips then crosses above once
    _, out = run_rule(s, closes)
    sig_idx = [i for i, o in enumerate(out) if isinstance(o, Signal)]
    assert sig_idx == [6]  # only on the crossing bar, not while it stays above
    assert out[0] is None and out[1] is None  # SMA(3) warming up: unknown, never a signal


def test_filters_produce_no_trade_reasons():
    s = spec(filters={"time_window": ["09:15", "15:00"], "all": [{"cmp": [{"ind": "rsi", "period": 2}, ">", {"const": 99}]}]},
             sides={"long": {"option": "CE", "entry": {"cmp": [{"ind": "close"}, ">", {"const": 0}]}}})
    _, out = run_rule(s, [100, 99, 98, 97, 96])
    reasons = {o.reason for o in out if isinstance(o, NoTrade)}
    assert any(r.startswith("filter: RSI(2) is above 99") for r in reasons)


def test_rule_exit_mirrors_for_short():
    s = spec(exits={"stop_loss_pct": 25, "target_pct": 50,
                    "conditions": {"cmp": [{"ind": "close"}, "<", {"ind": "ema", "period": 3}]}})
    rs, fe = RuleStrategy(s), FeatureEngine(5)
    falling = bars([100, 101, 102, 103, 100])
    for b in falling:
        st = MarketState("NIFTY", fe.update(b), has_position=True)
        rs.generate_signal(st)
    assert rs.exit_signal(st, Direction.LONG) == "rule exit"  # close fell below EMA(3)
    assert rs.exit_signal(st, Direction.SHORT) is None  # mirrored: close > EMA(3) is false


# ---- parity with the coded strategy ---------------------------------------------------------------------
def test_spec_matches_coded_momentum_trade_for_trade():
    tokens = {"NIFTY": "I", "NIFTY-FUT": "F", "BANKNIFTY": "BI", "BANKNIFTY-FUT": "BF", "INDIAVIX": "V"}
    cs = generate(trading_days(25, date(2026, 10, 16)), tokens, seed=3, timeframes=("5m",))
    data = BacktestData("simulated", [c for c in cs if c.token == "I"],
                        {c.ts: c for c in cs if c.token == "F"}, {c.ts: c.close for c in cs if c.token == "V"})
    coded = execute(BacktestRequest("NIFTY", source="simulated", strategy_params={"adx_min": 10}), data)
    s = dict(MOMENTUM_V1)
    s["filters"] = {**MOMENTUM_V1["filters"], "all": [
        {"cmp": [{"ind": "adx", "period": 14}, ">=", {"const": 10}]},
        {"cmp": [{"ind": "atr_pct", "period": 14}, ">=", {"const": 0.04}]}]}
    ruled = execute(BacktestRequest("NIFTY", source="simulated", strategy="spec", spec=s), data)
    key = lambda t: (t["entry_time"], t["contract"], t["exit_time"], t["exit_reason"], t["net_pnl"])  # noqa: E731
    assert len(coded["trades"]) > 10
    assert [key(t) for t in coded["trades"]] == [key(t) for t in ruled["trades"]]
    assert ruled["params"]["strategy"]["spec_hash"]
