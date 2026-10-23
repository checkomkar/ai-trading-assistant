"""Higher-timeframe operands: candles built from the strategy's own bars, completed ones only."""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from trader.features import FeatureEngine
from trader.indicators import EMA
from trader.instruments.calendar import IST
from trader.market_data.candles import Candle
from trader.strategy.base import MarketState
from trader.strategy.htf import HTFAggregator, TF_MINUTES
from trader.strategy.rule_strategy import RuleStrategy
from trader.strategy.spec import StrategySpec, render_english, semantic_errors


def day_bars(day=date(2026, 10, 19), n=375, seed=1, base_minutes=1):
    """Consecutive base candles from 09:15 IST, with volume."""
    rnd, px, out = random.Random(seed), 25000.0, []
    start = datetime.combine(day, time(9, 15), tzinfo=IST)
    for i in range(n // base_minutes):
        o = px
        c = o + rnd.uniform(-6, 6)
        out.append(Candle("NIFTY", f"{base_minutes}m", (start + timedelta(minutes=i * base_minutes)).astimezone(timezone.utc),
                          o, max(o, c) + 2, min(o, c) - 2, c, rnd.randint(50, 200), closed=True))
        px = c
    return out


def feats(bars, minutes=1):
    fe = FeatureEngine(minutes)
    return [fe.update(b, future=b) for b in bars]  # the futures bar doubles as a volume source


# ---- building the candles -------------------------------------------------------------------------------------
def test_candles_are_bucketed_from_the_open_and_appear_only_when_their_last_bar_closes():
    bars = day_bars()
    agg, got = HTFAggregator(9, 1), []
    for i, f in enumerate(feats(bars)):
        for c in agg.update(f):
            got.append((i, c))
    first_i, first = got[0]
    assert first_i == 8  # nine 1-minute bars (index 0-8) make the first 9-minute candle: it exists at the close of bar 9, not earlier
    assert first.open == bars[0].open and first.close == bars[8].close
    assert first.high == max(b.high for b in bars[:9]) and first.low == min(b.low for b in bars[:9])
    assert first.volume == sum(b.volume for b in bars[:9]) and first.time_ist == time(9, 15)
    assert [i for i, _ in got][:3] == [8, 17, 26]
    # 375 minutes is 41 candles of 9 plus a short one of 6: it closes with the session
    last_i, last = got[-1]
    assert last_i == 374 and len(got) == 42 and last.open == bars[369].open  # 41 * 9 = 369


def test_a_missing_bar_does_not_lose_the_candle_it_belonged_to():
    bars = day_bars()
    f = feats(bars)
    del f[7:9]  # the last two bars of the first 9-minute bucket never arrive
    agg, out = HTFAggregator(9, 1), []
    for x in f:
        out += agg.update(x)
    assert out[0].close == bars[6].close  # the candle is closed with what there was, at the next bucket's first bar
    assert len(out) == 42


def test_a_new_day_starts_a_new_candle():
    agg, out = HTFAggregator(15, 1), []
    for d, seed in ((date(2026, 10, 19), 1), (date(2026, 10, 20), 2)):
        for f in feats(day_bars(d, 40, seed)):
            out += agg.update(f)
    days = [c.day for c in out]
    # day 1: 40 bars = two full candles, and the 10-bar remainder is closed with what there was once the next day begins;
    # day 2: two full candles (its own remainder is still forming, so it is not visible yet)
    assert days.count(date(2026, 10, 19)) == 3 and days.count(date(2026, 10, 20)) == 2 and out[3].time_ist == time(9, 15)


# ---- in a strategy --------------------------------------------------------------------------------------------
def spec(entry, tf="1m", exits=None):
    return StrategySpec.model_validate({
        "name": "mtf", "underlyings": ["NIFTY"], "timeframe": tf, "sides": {"long": {"option": "CE", "entry": entry}},
        "exits": exits or {"stop_loss_pct": 20, "target_pct": 40}})


def values(strategy, fs):
    out = []
    for f in fs:
        strategy._observe(MarketState("NIFTY", f))
        out.append(strategy.hist[0])
    return out


def test_a_higher_timeframe_ema_matches_the_ema_of_the_completed_candles_only():
    bars = day_bars()
    fs = feats(bars)
    st = RuleStrategy(spec({"cmp": [{"ind": "ema", "period": 3, "tf": "9m"}, ">", {"const": 0}]}))
    hist = values(st, fs)
    closes = [bars[i].close for i in range(8, 375, 9)]  # the close of every completed 9-minute candle
    ema, want = EMA(3), []
    for c in closes:
        want.append(ema.update(c))
    key = ("ema", 3, None, "9m")
    # 3 candles are needed before the EMA exists: its first value is at the close of bar index 26, and it holds until the next
    # candle completes (index 35)
    assert hist[25][key][0] is None and hist[26][key][0] == pytest.approx(want[2])
    for i in range(26, 35):
        assert hist[i][key][0] == pytest.approx(want[2])
    assert hist[35][key][0] == pytest.approx(want[3])  # the 4th candle is visible at the close of its last bar, not before


def test_shift_counts_higher_timeframe_candles_and_price_operands_work_on_them():
    bars = day_bars()
    st = RuleStrategy(spec({"all": [{"cmp": [{"ind": "close", "tf": "5m"}, ">", {"const": 0}]},
                                    {"cmp": [{"ind": "close", "tf": "5m", "shift": 1}, ">", {"const": 0}]}]}))
    hist = values(st, feats(bars))
    k0 = ("close", None, None, "5m")
    assert hist[4][k0] == [bars[4].close]  # the first 5-minute candle closed with bar index 4
    assert hist[9][k0][:2] == [bars[9].close, bars[4].close]  # newest first: shift 1 is the previous candle
    from trader.strategy.base import Direction

    assert st.eval(st.entries[Direction.LONG]) is True


def test_conditions_mixing_base_and_higher_timeframes_evaluate_and_stay_unknown_until_warm():
    bars = day_bars()
    st = RuleStrategy(spec({"all": [{"cmp": [{"ind": "close"}, ">", {"ind": "ema", "period": 3, "tf": "9m"}]},
                                    {"cmp": [{"ind": "close", "tf": "3m"}, ">", {"const": 0}]}]}))
    from trader.strategy.base import Direction

    cond = st.entries[Direction.LONG]
    seen = []
    for f in feats(bars):
        st._observe(MarketState("NIFTY", f))
        seen.append(st.eval(cond))
    assert all(v is None for v in seen[:26])  # the 9-minute EMA does not exist yet: unknown, never a trade
    assert any(v is True for v in seen) and any(v is False for v in seen)


def test_a_cross_between_a_base_value_and_a_higher_timeframe_value_fires_once():
    bars = day_bars(seed=4)
    st = RuleStrategy(spec({"cross": [{"ind": "close"}, "above", {"ind": "ema", "period": 3, "tf": "9m"}]}))
    from trader.strategy.base import Direction

    cond = st.entries[Direction.LONG]
    fires = []
    for f in feats(bars):
        st._observe(MarketState("NIFTY", f))
        fires.append(st.eval(cond))
    assert fires.count(True) >= 2 and all(v is not None for v in fires[30:])
    for i in range(31, len(fires)):
        assert not (fires[i] is True and fires[i - 1] is True)  # a cross is a single bar, not a state


def test_two_timeframes_at_once_and_the_mirror_for_puts():
    sp = StrategySpec.model_validate({
        "name": "m", "underlyings": ["NIFTY"], "timeframe": "1m",
        "sides": {"long": {"option": "CE", "entry": {"all": [
            {"cmp": [{"ind": "close", "tf": "9m"}, ">", {"ind": "ema", "period": 11, "tf": "9m"}]},
            {"cmp": [{"ind": "close", "tf": "3m"}, ">", {"ind": "ema", "period": 11, "tf": "3m"}]}]}},
            "short": {"option": "PE", "mirror_of": "long"}},
        "exits": {"stop_loss_pct": 20, "target_pct": 40}})
    assert semantic_errors(sp) == []
    text = render_english(sp)
    assert "close on 9m candles is above EMA(11) on 9m candles" in text and "close on 3m candles is below EMA(11) on 3m candles" in text
    st = RuleStrategy(sp)
    assert {k[3] for k in st.bank.htf_keys} == {"9m", "3m"}
    values(st, feats(day_bars()))  # runs without error


# ---- the rules about what is allowed ----------------------------------------------------------------------------
def test_which_higher_timeframes_and_operands_are_allowed():
    ok = lambda tf, base="1m", ind="close": semantic_errors(spec({"cmp": [{"ind": ind, "tf": tf}, ">", {"const": 1}]}, tf=base))  # noqa: E731
    assert ok("9m") == [] and ok("60m") == []
    assert any("whole multiple" in e for e in ok("9m", base="5m"))  # 9 is not a multiple of 5
    assert any("whole multiple" in e for e in ok("5m", base="5m"))  # not larger than the base
    assert ok("15m", base="5m") == []
    assert any("cannot be used on another timeframe" in e for e in semantic_errors(spec(
        {"cmp": [{"ind": "prev_day_high", "tf": "9m"}, ">", {"const": 1}]})))
    with pytest.raises(Exception):
        spec({"cmp": [{"ind": "close", "tf": "7m"}, ">", {"const": 1}]})  # not a supported timeframe
    eq = StrategySpec.model_validate({"market": "equity", "name": "e", "underlyings": ["TCS"], "timeframe": "1d",
                                      "sides": {"long": {"entry": {"cmp": [{"ind": "close", "tf": "9m"}, ">", {"const": 1}]}}},
                                      "exits": {"stop_loss_pct": 5, "target_pct": 10}})
    assert any("intraday" in e for e in semantic_errors(eq))
    assert set(TF_MINUTES) >= {"3m", "9m", "15m"}


def test_existing_strategies_and_their_hashes_are_untouched():
    from trader.strategy.spec import MOMENTUM_V1

    sp = StrategySpec.model_validate(MOMENTUM_V1)
    assert '"tf"' not in sp.canonical_json() and sp.hash == "c427b87ca7bd7791"  # the hash recorded before this feature existed


def test_the_translator_knows_about_tf():
    from trader.ai.strategy_translator import SYSTEM_PROMPT

    assert '"tf"' in SYSTEM_PROMPT and "9m" in SYSTEM_PROMPT and "completed" in SYSTEM_PROMPT


# ---- end to end: a backtest on stored candles ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    import asyncio

    from fastapi.testclient import TestClient

    from tests.test_backtest_api import _seed
    from trader.api.main import create_app
    from trader.config import Settings
    from trader.db.migrate import upgrade_db

    url = f"sqlite+aiosqlite:///{(tmp_path_factory.mktemp('mtf') / 'm.db').as_posix()}"
    upgrade_db(url)
    asyncio.run(_seed(url))
    settings = Settings(_env_file=None, database_url=url, sim_seed=2, sim_tick_interval_seconds=0.2, sim_backfill_minutes=0)
    with TestClient(create_app(settings)) as c:
        yield c


def test_a_backtest_runs_a_strategy_that_reads_a_higher_timeframe(client):
    from tests.test_backtest_api import END

    mtf = {"name": "Trend on 15m, entry on 5m", "underlyings": ["NIFTY"], "timeframe": "5m",
           "sides": {"long": {"option": "CE", "entry": {"all": [
               {"cmp": [{"ind": "close", "tf": "15m"}, ">", {"ind": "ema", "period": 5, "tf": "15m"}]},
               {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 50}]}]}},
               "short": {"option": "PE", "mirror_of": "long"}},
           "filters": {"time_window": ["09:30", "14:45"]}, "exits": {"stop_loss_pct": 25, "target_pct": 50}}
    saved = client.post("/strategies", json={"spec": mtf})
    assert saved.status_code == 200, saved.text
    assert "on 15m candles" in saved.json()["english"]
    r = client.post("/backtests", json={"strategy_id": saved.json()["id"], "start": (END - timedelta(days=30)).isoformat(),
                                        "end": END.isoformat(), "timeframe": "5m"})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["signals"] > 0 and res["metrics"]["trades"] == len(res["trades"])
    # the same rules with the trend read on the base timeframe give a different set of signals: the higher timeframe is really used
    plain = {**mtf, "name": "Trend on 5m", "sides": {"long": {"option": "CE", "entry": {"all": [
        {"cmp": [{"ind": "close"}, ">", {"ind": "ema", "period": 5}]}, {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 50}]}]}},
        "short": {"option": "PE", "mirror_of": "long"}}}
    other = client.post("/backtests", json={"strategy_id": client.post("/strategies", json={"spec": plain}).json()["id"],
                                             "start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "timeframe": "5m"}).json()
    assert [t["signal_time"] for t in other["trades"]] != [t["signal_time"] for t in res["trades"]]  # (signal counts are capped per day)
    bad = client.post("/strategies/validate", json={"spec": {**mtf, "timeframe": "5m", "sides": {"long": {"option": "CE", "entry": {
        "cmp": [{"ind": "close", "tf": "9m"}, ">", {"const": 1}]}}}}}).json()
    assert not bad["valid"] and any("whole multiple" in e for e in bad["errors"])


# ---- "crossed recently" and warm-up ------------------------------------------------------------------------------------
def test_a_cross_within_n_bars_stays_true_for_n_bars_then_expires():
    from trader.strategy.base import Direction

    ramp = [(100 + (i if i < 10 else 10), 0, 0, 100 + (i if i < 10 else 10), 1) for i in range(30)]  # close rises through a flat 105
    base = {"cross": [{"ind": "close"}, "above", {"ind": "sma", "period": 2}]}  # placeholder so the spec validates
    now = spec({"cross": [{"ind": "close"}, "above", {"const": 104.5}]})
    rec = spec({"cross": [{"ind": "close"}, "above", {"const": 104.5}], "within": 4})
    seen = {"now": [], "rec": []}
    s_now, s_rec = RuleStrategy(now), RuleStrategy(rec)
    for b in ramp:
        o, h, l, c, v = b
        f = FeatureEngine(1).update(Candle("NIFTY", "1m", datetime(2026, 10, 19, 3, 45, tzinfo=timezone.utc) + timedelta(minutes=len(seen["now"])),
                                           c, c + 1, c - 1, c, 1, closed=True))
        for key, st in (("now", s_now), ("rec", s_rec)):
            st._observe(MarketState("NIFTY", f))
            seen[key].append(st.eval(st.entries[Direction.LONG]))
    cross_at = seen["now"].index(True)
    assert seen["now"].count(True) == 1  # the plain cross is a single bar
    assert [i for i, v in enumerate(seen["rec"]) if v is True] == list(range(cross_at, cross_at + 4))  # this bar and the next three
    assert render_english(rec).count("any of the previous 3 bars") == 1
    from trader.strategy.spec import mirror_condition

    assert mirror_condition(rec.sides.long.entry).within == 4
    assert base  # (kept: shows the operand form the spec accepts)


def test_cross_within_is_bounded():
    for bad in (0, 1, 500):
        with pytest.raises(Exception):
            spec({"cross": [{"ind": "close"}, "above", {"const": 1}], "within": bad})


def test_backtest_warm_up_days_prime_the_indicators_but_never_trade():
    from tests.test_backtest import FREE, NO_FRICTION, LinearPricer, Scripted, bars_for
    from trader.backtest.engine import BacktestEngine, EngineConfig
    from trader.strategy.base import Direction

    d1, d2 = date(2026, 10, 20), date(2026, 10, 21)
    closes = [25000, 25010, 25020, 25030, 25040, 25050]
    bars = bars_for(d1, closes) + bars_for(d2, closes)
    calls = []

    class Counting(Scripted):
        def generate_signal(self, state):
            calls.append(state.features.day)
            return super().generate_signal(state)

    strat = Counting({bars[0].ts: Direction.LONG, bars[len(closes)].ts: Direction.LONG})  # a signal on each day
    eng = BacktestEngine("NIFTY", strat, LinearPricer(), FREE, 5, EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9),
                         NO_FRICTION)
    res = eng.run(bars, trade_from=d2)
    assert [t.entry_time[:10] for t in res.trades] == ["2026-10-21"] and res.days == 1 and res.bars == len(closes) and res.signals == 1
    assert calls.count(d1) == len(closes)  # the strategy still saw every warm-up bar
