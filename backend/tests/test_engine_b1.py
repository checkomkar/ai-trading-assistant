"""Backtest Engine v2, phase B1 (PRD §24.2): Real-mode correctness and multi-year readiness."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from tests.test_backtest import CAL, FREE, NO_FRICTION, Scripted, bars_for
from trader.backtest import data as bt_data
from trader.backtest.costs import default_cost_model
from trader.backtest.engine import BacktestEngine, EngineConfig
from trader.backtest.pricing import ExecutionModel, RealPricer, expiry_for, lot_size_for
from trader.instruments.calendar import get_calendar
from trader.instruments.contract_history import load_expiry_overrides
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle
from trader.strategy.base import Direction

D = date(2026, 10, 21)  # Wednesday; the Tuesday weekly expiry is the 27th, the next one 3 Nov
NEAR, LATER = date(2026, 10, 27), date(2026, 11, 3)
CLOSES = [25000, 25010, 25020, 25030, 25040, 25050, 25060, 25070]


def stored(bars, expiry, strike=25000.0, premium=120.0, volume=100_000, skip=(), lows=None):
    out = {}
    for i, b in enumerate(bars):
        if i in skip:
            continue
        lo = (lows or {}).get(i, premium - 1)
        out[b.ts] = Candle("OPT", "5m", b.ts, premium, premium + 1, lo, premium, volume, True)
    return {(expiry, strike, OptionType.CE): out}


def run(bars, candles, exe=NO_FRICTION, signal_bar=0, sl=25.0, **kw):
    eng = BacktestEngine("NIFTY", Scripted({bars[signal_bar].ts: Direction.LONG}, sl=sl), RealPricer(candles, {}, CAL),
                         FREE, 5, EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9, **kw), exe)
    return eng.run(bars)


# ---- B1.1 expiry by calendar ----------------------------------------------------------------------------------------
def test_real_mode_trades_the_calendar_expiry_not_the_nearest_one_that_happens_to_be_stored():
    bars = bars_for(D, CLOSES)
    res = run(bars, stored(bars, LATER))  # only next week's contract was downloaded
    assert res.trades == [] and res.skipped_entries == {"no option data for contract": 1}
    assert any("had no stored contracts at all" in w and str(NEAR) in w for w in res.warnings)
    ok = run(bars, {**stored(bars, LATER), **stored(bars, NEAR)})
    assert len(ok.trades) == 1 and "27OCT26" in ok.trades[0].contract


def test_expiry_overrides_move_a_rule_expiry_to_the_day_the_exchange_used(tmp_path):
    path = tmp_path / "expiry_map.json"
    path.write_text('{"NIFTY|2026-10-27": {"expiry": "2026-10-26", "monthly": false}, '
                    '"NIFTY|2026-11-03": null}', encoding="utf-8")
    ovr = load_expiry_overrides(path)
    assert expiry_for("NIFTY", D, CAL, overrides=ovr) == date(2026, 10, 26)
    # A planned expiry with no contract that week is dropped: the following week's is next.
    assert expiry_for("NIFTY", date(2026, 10, 28), CAL, overrides=ovr) == date(2026, 11, 10)
    assert load_expiry_overrides(tmp_path / "missing.json") == {}


def test_historical_expiry_rules_follow_the_exchange_changes():
    cal = get_calendar()
    assert expiry_for("NIFTY", date(2021, 11, 1), cal) == date(2021, 11, 3)  # Thursday 4 Nov 2021 was Diwali
    assert expiry_for("NIFTY", date(2025, 8, 29), cal) == date(2025, 9, 2)  # first Tuesday expiry
    assert expiry_for("BANKNIFTY", date(2023, 9, 1), cal) == date(2023, 9, 6)  # Wednesday weeklies
    assert expiry_for("BANKNIFTY", date(2024, 11, 14), cal) == date(2024, 11, 27)  # monthly only, last Wednesday
    assert expiry_for("BANKNIFTY", date(2024, 12, 26), cal) == date(2025, 1, 30)  # monthly, last Thursday


# ---- B1.2 no prints --------------------------------------------------------------------------------------------------
def test_a_position_is_carried_through_bars_without_prints_and_a_gap_through_the_stop_fills_at_the_next_open():
    bars = bars_for(D, CLOSES)
    c = stored(bars, NEAR, skip={2, 3})
    c[(NEAR, 25000.0, OptionType.CE)][bars[4].ts] = Candle("OPT", "5m", bars[4].ts, 60, 61, 59, 60, 100_000, True)
    res = run(bars, c)
    t = res.trades[0]
    assert t.exit_reason == "stop (gap)" and t.exit_price == pytest.approx(60.0) and t.no_print_bars == 2
    assert any("did not trade" in w for w in res.warnings)


# ---- B1.3 liquidity --------------------------------------------------------------------------------------------------
def test_an_entry_bar_that_traded_less_than_a_lot_is_skipped():
    bars = bars_for(D, CLOSES)
    res = run(bars, stored(bars, NEAR, volume=10))  # 10 units < one 65-unit lot
    assert res.trades == [] and res.skipped_entries == {"illiquid entry bar": 1}
    loose = run(bars, stored(bars, NEAR, volume=10), exe=ExecutionModel(0, 0, 0.05, 0, min_entry_lots=0))
    assert len(loose.trades) == 1


# ---- B1.5 lot sizes --------------------------------------------------------------------------------------------------
def test_old_contracts_use_the_lot_size_of_their_time_and_live_ones_the_brokers():
    assert lot_size_for("NIFTY", date(2020, 1, 2))[0] == 75
    assert lot_size_for("NIFTY", date(2024, 6, 6), fallback=65)[0] == 25  # table beats a wrong stored value
    assert lot_size_for("NIFTY", date(2026, 10, 27), fallback=65) == (65, True)  # broker wins for the live regime
    old = date(2021, 1, 7)
    bars = bars_for(date(2021, 1, 4), CLOSES)
    res = run(bars, stored(bars, old))
    assert res.trades[0].quantity == 75 and any("Lot sizes from config/lot_sizes.yaml" in w for w in res.warnings)


# ---- B1.6 charges ----------------------------------------------------------------------------------------------------
def test_charges_follow_the_schedule_of_the_trade_date_and_refuse_dates_before_it():
    m = default_cost_model()
    assert m.order_charges(date(2020, 1, 10), 100, 75, "SELL").stt == pytest.approx(3.75)  # 0.05%
    assert m.order_charges(date(2023, 6, 1), 100, 75, "SELL").stt == pytest.approx(4.6875)  # 0.0625%
    assert m.order_charges(date(2020, 1, 10), 100, 75, "BUY").stamp == pytest.approx(0.15)  # 0.002% before Jul 2020
    with pytest.raises(ValueError, match="no cost schedule"):
        m.order_charges(date(2018, 6, 1), 100, 75, "SELL")


# ---- B1.7 stop slippage ----------------------------------------------------------------------------------------------
def test_an_intrabar_stop_fills_below_the_level_by_a_share_of_the_bars_range():
    e = ExecutionModel(0, 0, 0.05, stop_slippage_range_pct=10)
    assert e.stop_fill(90, bar_high=110, bar_low=80) == pytest.approx(87.0)
    assert e.stop_fill(81, bar_high=180, bar_low=80) == pytest.approx(80.0)  # never below the bar's low
    bars = bars_for(D, CLOSES)
    res = run(bars, stored(bars, NEAR, lows={3: 50}), exe=e)  # bar 3 trades down to 50 through the 90 stop
    t = res.trades[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(90 - 0.1 * (121 - 50))


# ---- B1.8 coverage ---------------------------------------------------------------------------------------------------
def test_coverage_counts_the_at_the_money_contracts_that_could_have_traded():
    bars = bars_for(D, CLOSES)  # 25000-25070: ATM 25000 or 25050
    data = bt_data.BacktestData("real", bars, {}, {}, options=stored(bars, NEAR))
    cov = bt_data.option_coverage(data, "NIFTY", D, CAL)
    assert cov["expected_contracts"] == 4 and cov["stored_contracts"] == 1 and cov["pct"] == 25.0
    assert cov["expiries_without_any_contract"] == 0


# ---- holidays --------------------------------------------------------------------------------------------------------
async def test_candles_on_a_listed_holiday_are_left_out_of_backtests(world):  # noqa: F811
    from sqlalchemy import select

    from trader.db import repository as repo
    from trader.db.models import InstrumentRow
    from tests.test_paper_runner import IDX

    hol = datetime(2026, 10, 20, 8, 15, tzinfo=timezone.utc)  # 13:45 IST on a holiday (a Muhurat-style session)
    async with world() as s, s.begin():
        iid = (await s.execute(select(InstrumentRow.id).where(InstrumentRow.token == IDX))).scalar_one()
        await repo.upsert_candles(s, [Candle(IDX, "1m", hol, 1, 1, 1, 1, 0, True)], {IDX: iid})
    assert date(2026, 10, 20) in get_calendar().holidays
    async with world() as s:
        data = await bt_data.load(s, "NIFTY", "5m", date(2026, 10, 19), date(2026, 10, 21), "real")
    assert all(b.ts.date() != date(2026, 10, 20) for b in data.bars) and data.bars


from tests.test_paper_runner import world  # noqa: E402,F401 - fixture
