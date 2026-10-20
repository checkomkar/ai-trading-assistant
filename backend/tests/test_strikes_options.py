import math
from datetime import date, datetime

import pytest

from trader.instruments.calendar import IST
from trader.instruments.strikes import OptionType, atm_strike, moneyness_offset, strike_at, strike_ladder
from trader.options import black_scholes as bs


@pytest.mark.parametrize(
    "u,price,atm",
    [("NIFTY", 25012, 25000), ("NIFTY", 25025, 25050), ("NIFTY", 25024.95, 25000),
     ("BANKNIFTY", 56049, 56000), ("BANKNIFTY", 56050, 56100)],
)
def test_atm(u, price, atm):
    assert atm_strike(u, price) == atm


def test_offsets_and_ladder():
    assert strike_at("NIFTY", 25012, 2) == 25100
    assert strike_at("BANKNIFTY", 56012, -1) == 55900
    assert strike_ladder("NIFTY", 25000, 2) == [24900, 24950, 25000, 25050, 25100]
    assert moneyness_offset(OptionType.CE, 1) == -1  # 1 ITM call = lower strike
    assert moneyness_offset(OptionType.PE, 1) == 1


def test_put_call_parity():
    s, k, t, r, v = 25000, 25100, 7 / 365, 0.065, 0.14
    c = bs.price(s, k, t, r, v, OptionType.CE)
    p = bs.price(s, k, t, r, v, OptionType.PE)
    assert c - p == pytest.approx(s - k * math.exp(-r * t), abs=1e-6)


@pytest.mark.parametrize("opt", list(OptionType))
@pytest.mark.parametrize("k", [24000, 25000, 26000])
def test_iv_roundtrip(opt, k):
    s, t, r, v = 25000, 10 / 365, 0.065, 0.17
    px = bs.price(s, k, t, r, v, opt)
    assert bs.implied_vol(px, s, k, t, r, opt) == pytest.approx(v, abs=1e-4)


def test_iv_none_below_intrinsic_or_expired():
    assert bs.implied_vol(50, 25000, 24800, 5 / 365, 0.065, OptionType.CE) is None
    assert bs.implied_vol(100, 25000, 25000, 0, 0.065, OptionType.CE) is None


def test_greek_signs():
    g_c = bs.greeks(25000, 25000, 7 / 365, 0.065, 0.14, OptionType.CE)
    g_p = bs.greeks(25000, 25000, 7 / 365, 0.065, 0.14, OptionType.PE)
    assert 0.5 < g_c.delta < 0.6 and -0.5 < g_p.delta < -0.4
    assert g_c.gamma > 0 and g_c.vega > 0 and g_c.theta < 0 and g_p.theta < 0


def test_time_to_expiry_measured_to_1530_ist():
    now = datetime(2026, 10, 27, 14, 30, tzinfo=IST)
    assert bs.time_to_expiry(date(2026, 10, 27), now) == pytest.approx(3600 / bs.SECONDS_PER_YEAR)
    assert bs.time_to_expiry(date(2026, 10, 26), now) == 0


def test_moneyness():
    assert bs.moneyness(25000, 24900, OptionType.CE) == "ITM"
    assert bs.moneyness(25000, 24900, OptionType.PE) == "OTM"
    assert bs.moneyness(25010, 25000, OptionType.CE, atm_band=25) == "ATM"
