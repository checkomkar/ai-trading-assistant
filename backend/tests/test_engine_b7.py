"""B7 (PRD §24.8): strike choice (ATM / ITM / OTM / delta) and exits on the index, the same in backtest and paper."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from tests.test_backtest import CAL, FREE, NO_FRICTION, Scripted, bars_for
from trader.backtest.engine import BacktestEngine, EngineConfig
from trader.backtest.pricing import SyntheticPricer
from trader.execution.runner import PaperRunner, _Live, _live_from_dict, _live_to_dict
from trader.instruments.calendar import IST
from trader.instruments.strikes import OptionType
from trader.options.selection import choose_strike
from trader.strategy.base import Direction, Signal
from trader.strategy.spec import MOMENTUM_V1, StrategySpec, render_english

D = date(2026, 10, 21)  # Wednesday; next NIFTY expiry Tuesday 27 Oct
AT = datetime.combine(D, time(10, 0), tzinfo=IST)


# ---- strike choice -----------------------------------------------------------------------------------------------
def test_strike_choice_rounds_like_the_exchange_and_moves_the_right_way_for_calls_and_puts():
    ce, pe = OptionType.CE, OptionType.PE
    assert choose_strike("NIFTY", ce, 25025, "ATM") == 25050  # a tie rounds up
    assert choose_strike("NIFTY", ce, 25010, "ITM1") == 24950 and choose_strike("NIFTY", ce, 25010, "OTM2") == 25100
    assert choose_strike("NIFTY", pe, 25010, "ITM1") == 25050 and choose_strike("NIFTY", pe, 25010, "OTM2") == 24900
    kw = dict(expiry=date(2026, 10, 27), at=AT, vix=14.0)
    d50, d40, d25 = (choose_strike("NIFTY", ce, 25010, s, **kw) for s in ("D50", "D40", "D25"))
    assert d50 <= d40 <= d25 and d25 > 25100  # lower delta = further out of the money
    assert choose_strike("NIFTY", pe, 25010, "D25", **kw) < 24900
    assert choose_strike("NIFTY", ce, 25010, "D40") is None  # a delta target needs expiry and time
    assert choose_strike("NIFTY", ce, 25010, "XYZ") is None
    assert choose_strike("NIFTY", ce, 25010, "OTM1", available=[25000.0, 25100.0]) is None  # not listed


@dataclass
class FakeRegistry:
    strikes_: list[float]
    chosen: list = field(default_factory=list)

    def expiries(self, und, now):
        return [date(2026, 10, 27), date(2026, 11, 3)]

    def strikes(self, und, exp):
        return self.strikes_

    def option(self, und, exp, k, ot):
        self.chosen.append((exp, k, ot))
        return SimpleNamespace(token=f"{und}{k}{ot.value}", strike=k)


@pytest.mark.parametrize("sel", ["ATM", "ITM2", "OTM1", "D40"])
@pytest.mark.parametrize("spot", [25025.0, 25010.0, 24987.5])
def test_paper_and_backtest_buy_the_same_strike(sel, spot):
    reg = FakeRegistry([24500.0 + 50 * i for i in range(30)])
    md = SimpleNamespace(registry=reg, spot=lambda s: 14.0 if s == "INDIAVIX" else spot)
    sig = Signal(Direction.LONG, 0.5, "NIFTY", sel, spot, 25, 50, "", AT, "t")
    PaperRunner._select_option(SimpleNamespace(md=md), SimpleNamespace(roll_on_expiry=True), sig, AT)
    bt = SyntheticPricer(CAL).contract("NIFTY", OptionType.CE, spot, D, None, sel, AT, 14.0)
    assert reg.chosen[-1][1] == bt.strike and reg.chosen[-1][0] == bt.expiry


# ---- spec ------------------------------------------------------------------------------------------------------------
def test_spec_accepts_strike_choice_and_index_exits_and_keeps_old_hashes():
    old = StrategySpec.model_validate(MOMENTUM_V1)
    s = copy.deepcopy(MOMENTUM_V1)
    s["option_selection"] = {"strike": "OTM1"}
    s["exits"]["index_stop"] = {"atr_mult": 1.0}
    s["exits"]["index_target"] = {"points": 60}
    new = StrategySpec.model_validate(s)
    assert new.option_selection.code == "OTM1" and new.hash != old.hash
    assert StrategySpec.model_validate(copy.deepcopy(MOMENTUM_V1)).hash == old.hash  # unchanged for existing specs
    text = render_english(new)
    assert "1-strike out of the money call" in text and "1 x ATR(14)" in text and "60 points" in text
    s["option_selection"] = {"delta": 0.4}
    assert StrategySpec.model_validate(s).option_selection.code == "D40"
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(copy.deepcopy(MOMENTUM_V1) | {"option_selection": {"strike": "OTM9"}})
    bad = copy.deepcopy(MOMENTUM_V1)
    bad["exits"]["index_stop"] = {"points": 50, "pct": 0.2}
    with pytest.raises(ValidationError, match="exactly one"):
        StrategySpec.model_validate(bad)


# ---- engine index exits ------------------------------------------------------------------------------------------------
class IndexScripted(Scripted):
    def __init__(self, at, stop=None, target=None, sel="ATM"):
        super().__init__(at, sl=90.0, tgt=900.0)  # premium exits far away: only the index levels act
        self.stop, self.target, self.sel = stop, target, sel

    def generate_signal(self, state):
        sig = super().generate_signal(state)
        if isinstance(sig, Signal):
            from dataclasses import replace

            return replace(sig, index_stop_dist=self.stop, index_target_dist=self.target, option_selection=self.sel)
        return sig


def _run(bars, strat):
    eng = BacktestEngine("NIFTY", strat, SyntheticPricer(CAL), FREE, 5,
                         EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9), NO_FRICTION)
    return eng.run(bars)


def test_index_stop_and_target_close_the_trade_on_the_index_level():
    down = bars_for(D, [25000, 25000, 24990, 24975, 24940, 24930, 24920, 24910])
    t = _run(down, IndexScripted({down[0].ts: Direction.LONG}, stop=40)).trades[0]
    assert t.exit_reason == "index stop" and t.underlying_entry == 25000  # entry at bar 1's open
    up = bars_for(D, [25000, 25000, 25010, 25030, 25070, 25080, 25090, 25100])
    t = _run(up, IndexScripted({up[0].ts: Direction.LONG}, target=50)).trades[0]
    assert t.exit_reason == "index target"
    put = _run(up, IndexScripted({up[0].ts: Direction.SHORT}, stop=50)).trades[0]
    assert put.exit_reason == "index stop"  # a rising index hurts a put


def test_a_gap_through_the_index_stop_fills_at_the_open_and_a_stop_wins_a_tie():
    bars = bars_for(D, [25000, 25000, 25005])
    bars.append(type(bars[0])("NIFTY", "5m", bars[-1].ts + timedelta(minutes=5), 24900, 24910, 24890, 24900, 0, True))
    bars += bars_for(D, [24900, 24900], start=time(9, 35))
    t = _run(bars, IndexScripted({bars[0].ts: Direction.LONG}, stop=30)).trades[0]
    assert t.exit_reason == "index stop (gap)"
    wide = bars_for(D, [25000, 25000, 25000])
    wide.append(type(wide[0])("NIFTY", "5m", wide[-1].ts + timedelta(minutes=5), 25000, 25080, 24950, 25000, 0, True))
    wide += bars_for(D, [25000, 25000], start=time(9, 35))
    t = _run(wide, IndexScripted({wide[0].ts: Direction.LONG}, stop=40, target=60)).trades[0]
    assert t.exit_reason == "index stop"  # both levels inside one bar: assume the worse


def test_backtest_buys_the_selected_strike():
    bars = bars_for(D, [25010] * 6)
    t = _run(bars, IndexScripted({bars[0].ts: Direction.LONG}, sel="OTM2")).trades[0]
    assert "25100 CE" in t.contract


# ---- paper runner index exits --------------------------------------------------------------------------------------
def _runner_with(spot: float, bid: float):
    now = datetime.combine(D, time(11, 0), tzinfo=IST)
    md = SimpleNamespace(latest={"OPT": SimpleNamespace(bid=bid, ltp=bid)}, spot=lambda s: spot)
    exits: list[str] = []
    fake = SimpleNamespace(md=md, _request_exit=lambda lane, reason, n: exits.append(reason))
    return fake, now, exits


def _live(stop=None, target=None, direction=Direction.LONG):
    sig = Signal(direction, 0.5, "NIFTY", "ATM", 25000, 25, 50, "", AT, "t")
    lv = _Live("c1", sig, SimpleNamespace(token="OPT"), 65, "OPEN", SimpleNamespace(), entry_price=100, stop=75,
               initial_stop=75, target=150, high_water=100, low_water=100, last_bid=100)
    lv.index_stop, lv.index_target = stop, target
    return lv


@pytest.mark.parametrize("spot,expect", [(24955.0, "index stop"), (25065.0, "index target"), (25010.0, None)])
async def test_paper_runner_exits_on_the_index_levels(spot, expect):
    fake, now, exits = _runner_with(spot, bid=100.0)
    lane = SimpleNamespace(live=_live(stop=24960.0, target=25060.0),
                           eng=SimpleNamespace(eod_exit=time(15, 15), trail_activate_pct=None, trail_pct=None))
    await PaperRunner._manage(fake, lane, now)
    assert exits == ([expect] if expect else [])


def test_index_levels_survive_a_restart():
    lv = _live(stop=24960.0, target=25060.0)
    lv.entry_order = SimpleNamespace(to_dict=lambda: {"id": "o1"}, charges=1.0)
    lv.inst = SimpleNamespace(token="OPT", symbol="NIFTY")
    d = _live_to_dict(SimpleNamespace(cfg=SimpleNamespace(underlying="NIFTY"), label="t"), lv)
    assert d["index_stop"] == 24960.0 and d["index_target"] == 25060.0
    from unittest.mock import patch

    with patch("trader.execution.runner.Order.from_dict", lambda x: x):
        back = _live_from_dict(d, lv.inst)
    assert back.index_stop == 24960.0 and back.index_target == 25060.0
