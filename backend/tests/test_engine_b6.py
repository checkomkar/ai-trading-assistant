"""B6 (PRD §24.7): every trade is tagged with the market conditions at its signal bar, and results are broken down by them."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from tests.test_backtest import CAL, FREE, NO_FRICTION, Scripted, bars_for
from trader.backtest.engine import BacktestEngine, EngineConfig, dte_bucket, regime_tags
from trader.backtest.metrics import by_regime, compute_metrics, regime_highlights
from trader.backtest.pricing import SyntheticPricer
from trader.instruments.calendar import IST
from trader.strategy.base import Direction

D1, D2 = date(2026, 10, 19), date(2026, 10, 20)


def _run(bars, signals, vix=None):
    eng = BacktestEngine("NIFTY", Scripted(signals), SyntheticPricer(CAL), FREE, 5,
                         EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9, max_hold_bars=2), NO_FRICTION)
    return eng.run(bars, vix=vix)


def test_tags_use_only_what_was_known_at_the_signal_bar():
    day1 = bars_for(D1, [25000 + 5 * i for i in range(40)])
    day2 = bars_for(D2, [25400 + 3 * i for i in range(20)])  # opens ~0.8% above the previous close: a gap up
    bars = day1 + day2
    vix = {b.ts: 15.0 for b in bars}
    sig = {day2[2].ts: Direction.LONG}
    first = _run(bars, sig, vix).trades[0].regime
    # Change everything after the signal bar: the tags of that trade must not move.
    later = bars[: len(day1) + 3] + bars_for(D2, [20000.0] * 17, start=time(9, 30))
    vix2 = {**vix, **{b.ts: 40.0 for b in later[len(day1) + 3:]}}
    second = _run(later, sig, vix2).trades[0].regime
    assert first == second
    assert first["gap"] == "gap up 0.5%+" and first["vix"] == "13-18" and first["weekday"] == "Tue"
    assert first["time_of_day"] == "09:15-10:00" and first["days_to_expiry"] == "3+ days"  # rolled to next week on expiry day


def test_buckets():
    class F(dict):
        def __getitem__(self, k):
            return self.get(k)

    t = datetime(2026, 10, 21, 13, 5, tzinfo=IST)
    tags = regime_tags(F(vix=26.0, adx=30.0, prev_day_close=100.0), t, 99.0)
    assert tags == {"vix": "25 and above", "trend": "trending (ADX 25+)", "gap": "gap down 0.5%+",
                    "time_of_day": "12:00-14:00", "weekday": "Wed"}
    assert regime_tags(F(), t, None)["vix"] == "unknown"
    assert [dte_bucket(D1 + timedelta(days=n), D1) for n in (0, 1, 2, 5)] == ["expiry day", "1 day", "2 days", "3+ days"]


def test_breakdown_sums_to_the_whole_and_highlights_name_the_group_that_carried_it():
    day1 = bars_for(D1, [25000 + 8 * i for i in range(60)])
    day2 = bars_for(D2, [25500 - 8 * i for i in range(60)])
    bars = day1 + day2
    sig = {day1[i].ts: Direction.LONG for i in range(1, 50, 4)} | {day2[i].ts: Direction.SHORT for i in range(1, 50, 4)}
    res = _run(bars, sig, {b.ts: 15.0 for b in bars})
    m = compute_metrics(res.trades, 100_000, [D1.isoformat(), D2.isoformat()])
    br = m["by_regime"]
    assert set(br) >= {"vix", "trend", "gap", "days_to_expiry", "time_of_day", "weekday", "direction"}
    for groups in br.values():
        assert sum(g["trades"] for g in groups.values()) == len(res.trades)
        assert round(sum(g["net_pnl"] for g in groups.values()), 2) == round(m["total_net_pnl"], 2)
    assert m["regime_highlights"] == regime_highlights(res.trades)
    assert by_regime([]) == {} and regime_highlights([]) == []
