from datetime import date, datetime, time, timedelta, timezone

import pytest

from trader.backtest.costs import CostModel, CostSchedule, default_cost_model
from trader.backtest.engine import BacktestEngine, EngineConfig
from trader.backtest.metrics import compute_metrics
from trader.backtest.pricing import Contract, ExecutionModel, PremiumBar, SyntheticPricer, expiry_for
from trader.instruments.calendar import IST, TradingCalendar
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle
from trader.strategy.base import Direction, MarketState, NoTrade, Signal, Strategy
from trader.strategy.momentum import MomentumParams, MomentumStrategy
from trader.features import FeatureEngine

CAL = TradingCalendar()
TF = timedelta(minutes=5)


def bars_for(day: date, closes: list[float], start=time(9, 15)) -> list[Candle]:
    t0 = datetime.combine(day, start, tzinfo=IST).astimezone(timezone.utc)
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append(Candle("NIFTY", "5m", t0 + i * TF, o, max(o, c) + 1, min(o, c) - 1, c, 0, True))
        prev = c
    return out


class Scripted(Strategy):
    """Emits a signal on chosen bar indices (by bar start time)."""

    name = "scripted"

    def __init__(self, at: dict[datetime, Direction], sl=25.0, tgt=50.0):
        self.at, self.sl, self.tgt = at, sl, tgt

    def generate_signal(self, state: MarketState):
        d = self.at.get(state.features.ts)
        if d is None or state.has_position:
            return None
        return Signal(d, 0.7, "NIFTY", "ATM", state.features.close, self.sl, self.tgt, "scripted",
                      state.features.ts, self.name)


class LinearPricer:
    """Premium = 100 + (spot - 25000) for CE (mirror for PE); exact and easy to reason about."""

    mode = "synthetic"

    def contract(self, underlying, option_type, spot, day, lot_size=None):
        return Contract(underlying, day + timedelta(days=7), 25000.0, option_type, 10)

    def _p(self, c, s):
        return max(1.0, 100 + (s - 25000) * (1 if c.option_type == OptionType.CE else -1))

    def bar(self, c, u, tf, vix):
        o, cl = self._p(c, u.open), self._p(c, u.close)
        a, b = self._p(c, u.high), self._p(c, u.low)
        return PremiumBar(o, max(o, cl, a, b), min(o, cl, a, b), cl)


FREE = CostModel([CostSchedule(date(2000, 1, 1), 0, 0, 0, 0, 0, 0, 0, 0)])
NO_FRICTION = ExecutionModel(spread_pct=0.0, slippage_ticks=0, tick=0.05)


def engine(strategy, **cfg):
    defaults = dict(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9)
    return BacktestEngine("NIFTY", strategy, LinearPricer(), FREE, 5,
                          EngineConfig(**{**defaults, **cfg}), NO_FRICTION)


D = date(2026, 10, 21)


def test_entry_fills_next_bar_open_never_same_bar():
    closes = [25000, 25010, 25020, 25030, 25040, 25050]
    bars = bars_for(D, closes)
    r = engine(Scripted({bars[1].ts: Direction.LONG})).run(bars)
    t = r.trades[0]
    assert datetime.fromisoformat(t.entry_time) == bars[2].ts
    assert t.entry_price == pytest.approx(100 + (bars[2].open - 25000))  # open of the NEXT bar


def test_stop_and_target_levels():
    # Entry at bar 1's open (spot 25000) -> premium 100: stop 25% = 75, target 50% = 150.
    up = bars_for(D, [25000, 25010, 25030, 25070, 25080])
    t = engine(Scripted({up[0].ts: Direction.LONG})).run(up).trades[0]
    paid = NO_FRICTION.buy(100)
    assert t.entry_price == pytest.approx(paid)
    assert t.exit_reason == "target" and t.exit_price == pytest.approx(NO_FRICTION.sell(paid * 1.5))
    down = bars_for(D, [25000, 25010, 25000, 24970, 24960])
    t = engine(Scripted({down[0].ts: Direction.LONG})).run(down).trades[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(NO_FRICTION.sell(paid * 0.75))


def test_both_levels_in_one_bar_assumes_stop():
    bars = bars_for(D, [25000, 25010, 25010, 25010])
    wide = bars[2]
    bars[2] = Candle(wide.token, "5m", wide.ts, 25010, 25100, 24900, 25010, 0, True)  # touches both
    r = engine(Scripted({bars[0].ts: Direction.LONG})).run(bars)
    assert r.trades[0].exit_reason == "stop"


def test_gap_through_stop_fills_at_open():
    bars = bars_for(D, [25000, 25010, 25010])
    b = bars[2]
    bars[2] = Candle(b.token, "5m", b.ts, 24950, 24960, 24940, 24955, 0, True)  # opens below stop
    r = engine(Scripted({bars[0].ts: Direction.LONG})).run(bars)
    t = r.trades[0]
    assert t.exit_reason == "stop (gap)" and t.exit_price == pytest.approx(50)  # 100 + (24950-25000)


def test_eod_exit_and_no_overnight():
    closes = [25000 + i for i in range(75)]  # 09:15 .. 15:25, gentle drift, no stop/target
    bars = bars_for(D, closes) + bars_for(D + timedelta(days=1), [25100] * 5)
    r = engine(Scripted({bars[60].ts: Direction.LONG})).run(bars)  # 14:15 signal
    t = r.trades[0]
    assert t.exit_reason == "eod"
    assert datetime.fromisoformat(t.exit_time).astimezone(IST).time() == time(15, 15)
    assert len(r.trades) == 1


def test_signal_on_last_bar_is_not_carried_to_next_day():
    bars = bars_for(D, [25000, 25010]) + bars_for(D + timedelta(days=1), [25100, 25110, 25120])
    r = engine(Scripted({bars[1].ts: Direction.LONG})).run(bars)
    assert r.trades == [] and r.skipped_entries == {"signal on last bar of day": 1}


def test_trailing_stop_locks_profit():
    closes = [25000, 25010, 25050, 25080, 25060, 25040, 25030]  # +70 run-up then fade
    bars = bars_for(D, closes)
    r = engine(Scripted({bars[0].ts: Direction.LONG}, tgt=200), trail_activate_pct=30, trail_pct=15).run(bars)
    t = r.trades[0]
    assert t.exit_reason == "trailing stop" and t.net_pnl > 0


def test_daily_loss_blocks_further_trades():
    bars = bars_for(D, [25000, 25010, 24960, 24950, 24950, 24940, 24930, 24920])
    strat = Scripted({bars[0].ts: Direction.LONG, bars[4].ts: Direction.LONG})
    r = engine(strat, max_daily_loss=100).run(bars)
    # The first trade loses ~250 (> 100). The scripted strategy ignores daily P&L, so the second
    # signal must be stopped by the engine's own risk gate.
    assert len(r.trades) == 1 and r.skipped_entries.get("daily loss limit") == 1


def test_costs_schedule_by_date():
    m = default_cost_model()
    old = m.order_charges(date(2025, 1, 10), 100, 75, "SELL")
    new = m.order_charges(date(2026, 6, 1), 100, 75, "SELL")
    assert old.stt == pytest.approx(7.5) and new.stt == pytest.approx(11.25)  # 0.10% -> 0.15% of 7,500
    buy = m.order_charges(date(2026, 6, 1), 100, 75, "BUY")
    assert buy.stt == 0 and buy.stamp == pytest.approx(0.225) and buy.brokerage == 20
    assert buy.gst == pytest.approx((buy.brokerage + buy.exchange + buy.clearing + buy.sebi + buy.ipft) * 0.18, abs=1e-3)


def test_expiry_rules_over_time():
    assert expiry_for("NIFTY", date(2026, 10, 21), CAL) == date(2026, 10, 27)  # Tuesday weekly
    assert expiry_for("NIFTY", date(2026, 10, 27), CAL) == date(2026, 11, 3)  # roll on expiry day
    assert expiry_for("NIFTY", date(2026, 10, 27), CAL, roll_on_expiry_day=False) == date(2026, 10, 27)
    assert expiry_for("NIFTY", date(2025, 3, 3), CAL) == date(2025, 3, 6)  # Thursday era
    assert expiry_for("BANKNIFTY", date(2026, 10, 21), CAL) == date(2026, 10, 27)  # monthly, last Tuesday
    hol = TradingCalendar(holidays={date(2026, 11, 24): "x"})
    assert expiry_for("BANKNIFTY", date(2026, 10, 28), hol) == date(2026, 11, 23)


def test_synthetic_pricer_ce_pe_symmetry():
    p = SyntheticPricer(CAL)
    c = p.contract("NIFTY", OptionType.CE, 25012, date(2026, 10, 21))
    assert c.strike == 25000 and c.expiry == date(2026, 10, 27) and c.lot_size == 65
    bar = Candle("NIFTY", "5m", datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc), 25000, 25040, 24980, 25020, 0)
    ce = p.bar(c, bar, TF, 13.0)
    pe = p.bar(Contract("NIFTY", c.expiry, 25000, OptionType.PE, 65), bar, TF, 13.0)
    assert ce.low <= ce.open <= ce.high and ce.close > ce.open  # CE gains as spot rises
    assert pe.close < pe.open
    assert 80 < ce.open < 250  # sane ATM weekly premium at ~13% IV with 6 days left


def test_execution_model_spread_and_ticks():
    e = ExecutionModel(spread_pct=0.3, slippage_ticks=1, tick=0.05)
    assert e.buy(100) == pytest.approx(100.2) and e.sell(100) == pytest.approx(99.8)
    assert e.sell(0.05) == 0.05


def test_metrics():
    from trader.backtest.engine import Trade

    def tr(pnl, day):
        ts = f"2026-10-{day:02d}T05:00:00+00:00"
        return Trade("t", "c", "X", "LONG", ts, "", 0.5, ts, 100, ts, ts, 100, "x", 65, pnl, 0, pnl, 0, 0, 1, 5, 0, 0)

    trades = [tr(1000, 1), tr(-500, 2), tr(2000, 5), tr(-1500, 6)]
    m = compute_metrics(trades, 100_000, ["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"])
    assert m["total_net_pnl"] == 1000 and m["win_rate"] == 50 and m["profit_factor"] == 1.5
    assert m["max_drawdown"] == 1500 and m["expectancy"] == 250
    assert len(m["daily_pnl"]) == 5 and m["daily_pnl"][-1]["pnl"] == 0  # no-trade day counted


def test_momentum_strategy_no_trade_states_and_signal():
    s = MomentumStrategy(MomentumParams(adx_min=0, atr_pct_min=0, use_vwap=False))
    fe = FeatureEngine(5)
    closes = [25000 + i * 3 for i in range(40)]  # steady uptrend from 09:15
    out = []
    for b in bars_for(D, closes):
        f = fe.update(b)
        out.append(s.generate_signal(MarketState("NIFTY", f)))
    assert isinstance(out[0], NoTrade) and out[0].reason == "outside trading window"
    sigs = [o for o in out if isinstance(o, Signal)]
    assert sigs and all(x.direction == Direction.LONG for x in sigs)
    assert sigs[0].features["rsi"] > 55
    blocked = s.generate_signal(MarketState("NIFTY", f, has_position=True))
    assert isinstance(blocked, NoTrade) and blocked.reason == "existing position"
