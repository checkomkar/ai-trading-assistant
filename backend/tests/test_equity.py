"""Stock (daily-bar, delivery) strategies: spec rules, engine fill/exit rules, costs."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from trader.backtest.equity import EquityConfig, EquityEngine
from trader.backtest.equity_costs import EquityCosts
from trader.instruments import equities as eq
from trader.market_data.candles import Candle
from trader.strategy.spec import StrategySpec, render_english, semantic_errors

D0 = date(2026, 1, 5)  # a Monday


def bars(sym: str, rows: list[tuple[float, float, float, float]], first: date = D0) -> list[Candle]:
    """Consecutive daily candles (o, h, l, c), one per weekday."""
    out, d = [], first
    for o, h, l, c in rows:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(Candle(eq.token(sym), "1d", eq.day_stamp(d), o, h, l, c, 1000, closed=True))
        d += timedelta(days=1)
    return out


def spec(entry=None, stop=5.0, target=10.0, **extra) -> StrategySpec:
    entry = entry or {"cmp": [{"ind": "close"}, ">", {"const": 100}]}  # close above 100
    raw = {"market": "equity", "name": "t", "underlyings": ["TCS"], "timeframe": "1d",
           "sides": {"long": {"entry": entry}}, "exits": {"stop_loss_pct": stop, "target_pct": target},
           "risk": extra.pop("risk", {"max_positions": 2})}
    raw["exits"].update(extra.pop("exits", {}))
    raw.update(extra)
    return StrategySpec.model_validate(raw)


FREE = EquityConfig(capital=100_000, slippage_pct=0.0, costs=EquityCosts(0, 0, 0, 0, 0, 0, 0, 0))


def run(sp: StrategySpec, data: dict[str, list[Candle]], cfg: EquityConfig = FREE, start: date = D0):
    e = EquityEngine(sp, cfg)
    res = e.run(data, start)
    return e, res


# ---- the spec ---------------------------------------------------------------------------------
def test_stock_spec_is_valid_and_reads_in_english():
    s = spec()
    assert semantic_errors(s) == []
    text = render_english(s)
    assert "daily candles" in text and "Buy the shares" in text and "1/2 share" in text


def test_stock_spec_rules():
    assert any("daily candles" in e for e in semantic_errors(spec(timeframe="5m")))
    short = spec(sides={"long": {"entry": {"cmp": [{"ind": "close"}, ">", {"const": 1}]}},
                        "short": {"entry": {"cmp": [{"ind": "close"}, "<", {"const": 1}]}}})
    assert any("long-only" in e for e in semantic_errors(short))
    vw = spec(entry={"cmp": [{"ind": "close"}, ">", {"ind": "vwap"}]})
    assert any("vwap" in e for e in semantic_errors(vw))
    with pytest.raises(ValidationError):
        spec(underlyings=["NOTASTOCK"])
    with pytest.raises(ValidationError):
        StrategySpec.model_validate({"name": "x", "underlyings": ["TCS"], "sides": {"long": {"option": "CE", "entry": {
            "cmp": [{"ind": "close"}, ">", {"const": 1}]}}}, "exits": {"stop_loss_pct": 5, "target_pct": 10}})


def test_options_spec_hash_ignores_the_stock_fields():
    from trader.strategy.spec import MOMENTUM_V1

    s = StrategySpec.model_validate(MOMENTUM_V1)
    assert '"market"' not in s.canonical_json() and "max_positions" not in s.canonical_json()
    assert semantic_errors(StrategySpec.model_validate({**MOMENTUM_V1, "timeframe": "1d"}))  # 1d is for stocks


# ---- fills and exits -------------------------------------------------------------------------------
def test_entry_fills_at_the_next_open_and_target_exits():
    rows = [(99, 99, 99, 99), (101, 102, 100, 101),  # day 2 closes above 100: signal
            (103, 104, 102, 103),  # day 3 open = entry 103
            (104, 120, 103, 119)]  # target 103*1.10 = 113.3 hit
    _, res = run(spec(), {"TCS": bars("TCS", rows)})
    [t] = res.trades
    assert t.entry_price == 103.0 and t.exit_reason == "target" and t.exit_price == pytest.approx(113.3)
    assert t.quantity == int(50_000 // 103)  # two slots: half of the capital each
    assert t.net_pnl == pytest.approx((113.3 - 103) * t.quantity, abs=0.05)


def test_stop_wins_when_one_day_touches_both():
    rows = [(99, 99, 99, 99), (101, 102, 100, 101), (103, 104, 102, 103), (103, 130, 90, 100)]
    _, res = run(spec(), {"TCS": bars("TCS", rows)})
    assert res.trades[0].exit_reason == "stop loss" and res.trades[0].exit_price == pytest.approx(103 * 0.95)


def test_gap_through_the_stop_fills_at_the_open():
    rows = [(99, 99, 99, 99), (101, 102, 100, 101), (103, 104, 102, 103), (80, 85, 79, 82)]
    _, res = run(spec(), {"TCS": bars("TCS", rows)})
    t = res.trades[0]
    assert t.exit_reason == "stop loss (gap)" and t.exit_price == 80.0


def test_time_exit_and_rule_exit_fill_at_the_next_open():
    rows = [(99, 99, 99, 99), (101, 102, 100, 101)] + [(103 + i, 104 + i, 102 + i, 103 + i) for i in range(6)]
    _, res = run(spec(exits={"time_exit_bars": 3}), {"TCS": bars("TCS", rows)})
    t = res.trades[0]
    assert t.exit_reason.startswith("time exit") and t.bars_held == 3
    # entry day 3 (open 103); 3 bars held after days 3, 4, 5 -> sells at day 6's open
    assert t.exit_price == 106.0
    rule = spec(exits={"conditions": {"cmp": [{"ind": "close"}, ">", {"const": 104}]}})
    _, res = run(rule, {"TCS": bars("TCS", rows)})
    assert res.trades[0].exit_reason == "rule exit" and res.trades[0].exit_price == 106.0  # close 105 on day 5 -> open day 6


def test_trailing_stop_ratchets_after_the_high():
    sp = spec(stop=10.0, target=100.0, exits={"trail": {"activate_pct": 5, "trail_pct": 5}})
    rows = [(99, 99, 99, 99), (101, 102, 100, 101), (100, 101, 99, 100),  # enter 100
            (101, 120, 101, 118),  # high 120 -> stop trails to 114
            (118, 119, 110, 112)]  # low 110 <= 114 -> stopped at 114
    _, res = run(sp, {"TCS": bars("TCS", rows)})
    assert res.trades[0].exit_reason == "stop loss" and res.trades[0].exit_price == pytest.approx(114.0)


def test_slots_and_alphabetical_priority():
    up = [(99, 99, 99, 99), (101, 102, 100, 101), (103, 104, 102, 103), (103, 104, 102, 103)]
    data = {s: bars(s, up) for s in ("TCS", "INFY", "WIPRO")}
    sp = spec(underlyings=["TCS", "INFY", "WIPRO"], risk={"max_positions": 2})
    _, res = run(sp, data)
    held = sorted(p.symbol for p in res.open_positions)
    assert held == ["INFY", "TCS"] and "no free position slot" in res.skipped  # WIPRO signals every day and waits for a slot


def test_risk_sized_position():
    sp = spec(stop=5.0, risk={"max_positions": 1, "risk_per_trade_pct": 1.0})
    rows = [(99, 99, 99, 99), (101, 102, 100, 101), (100, 101, 99, 100), (100, 101, 99, 100)]
    _, res = run(sp, {"TCS": bars("TCS", rows)})
    # 1% of 100,000 = 1,000 at risk; a 5% stop on a 100 share loses 5 -> 200 shares (less than the 1000 the slot allows)
    assert res.open_positions[0].quantity == 200


def test_costs_are_charged_on_both_legs_and_slippage_hurts():
    rows = [(99, 99, 99, 99), (101, 102, 100, 101), (100, 101, 99, 100), (100, 120, 99, 119)]
    sp = spec(risk={"max_positions": 1})
    _, free = run(sp, {"TCS": bars("TCS", rows)})
    _, paid = run(sp, {"TCS": bars("TCS", rows)}, EquityConfig(capital=100_000))
    assert paid.trades[0].charges > 0 and paid.trades[0].net_pnl < free.trades[0].net_pnl
    c = EquityCosts()
    assert c.order_charges(100, 10, "BUY") > c.order_charges(100, 10, "BUY") - c.stt_pct  # sanity
    assert c.order_charges(1000, 100, "SELL") >= 1000 * 100 * c.stt_pct / 100 + c.dp_charge_per_sell


def test_nothing_trades_before_the_start_date_and_open_positions_close_at_the_end():
    rows = [(99, 99, 99, 99), (101, 102, 100, 101), (103, 104, 102, 103), (103, 106, 102, 105)]
    data = {"TCS": bars("TCS", rows)}
    e = EquityEngine(spec(), FREE)
    res = e.run(data, start=D0 + timedelta(days=4))  # the signal day is before the start: no trade
    assert res.trades == [] and res.open_positions == []
    res = e.run(data, start=D0)
    assert len(res.open_positions) == 1
    [t] = e.finish_open(res, data)
    assert t.exit_reason.startswith("still open") and t.exit_price == 105.0
    assert res.equity_curve[-1]["equity"] > 100_000
