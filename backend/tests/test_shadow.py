import pytest

from trader.execution.shadow import intended_live_entry, shadow_order


def test_limit_is_ask_plus_cap_rounded_up_to_tick():
    assert intended_live_entry(100.0, 0.05, 1.0) == 101.0
    assert intended_live_entry(100.02, 0.05, 1.0) == 101.05  # 101.0202 rounds UP so it stays marketable
    with pytest.raises(ValueError):
        intended_live_entry(0, 0.05, 1.0)


def test_shadow_order_is_never_sent():
    o = shadow_order(symbol="X", quantity=75, ask=100.0, tick_size=0.05, slippage_pct=1.0, paper_price=100.0, correlation_id="c")
    assert o["sent"] is False and o["limit_price"] == 101.0 and o["worst_case_extra_cost"] == 75.0
