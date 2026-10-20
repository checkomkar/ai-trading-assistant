from datetime import date, datetime

import pytest

from trader.instruments.calendar import IST, SessionState, TradingCalendar
from trader.instruments.expiry import monthly_expiry, resolve_expiries, upcoming_expiries
from trader.instruments.specs import get_spec

CAL = TradingCalendar()


def ist(y, m, d, hh=10, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


@pytest.mark.parametrize(
    "hh,mm,state",
    [
        (8, 59, SessionState.CLOSED),
        (9, 0, SessionState.PRE_OPEN),
        (9, 14, SessionState.PRE_OPEN),
        (9, 15, SessionState.OPEN),
        (9, 30, SessionState.TRADING),
        (15, 29, SessionState.TRADING),
        (15, 30, SessionState.CLOSING),
        (15, 40, SessionState.CLOSED),
    ],
)
def test_session_states(hh, mm, state):
    assert CAL.session_state(ist(2026, 10, 20, hh, mm)) == state  # Tuesday


def test_weekend_and_holiday_closed():
    assert CAL.session_state(ist(2026, 10, 24, 11)) == SessionState.CLOSED  # Saturday
    cal = TradingCalendar(holidays={date(2026, 10, 21): "test holiday"})
    assert cal.session_state(ist(2026, 10, 21, 11)) == SessionState.CLOSED


def test_nifty_weekly_expiries_are_tuesdays():
    exps = upcoming_expiries("NIFTY", ist(2026, 10, 21), count=4, cal=CAL)
    assert exps == [date(2026, 10, 27), date(2026, 11, 3), date(2026, 11, 10), date(2026, 11, 17)]


def test_expiry_live_until_close_on_expiry_day():
    before = upcoming_expiries("NIFTY", ist(2026, 10, 20, 15, 29), count=1, cal=CAL)
    after = upcoming_expiries("NIFTY", ist(2026, 10, 20, 15, 30), count=1, cal=CAL)
    assert before == [date(2026, 10, 20)]
    assert after == [date(2026, 10, 27)]


def test_holiday_moves_expiry_to_previous_trading_day():
    cal = TradingCalendar(holidays={date(2026, 10, 27): "holiday"})
    exps = upcoming_expiries("NIFTY", ist(2026, 10, 21), count=2, cal=cal)
    assert exps == [date(2026, 10, 26), date(2026, 11, 3)]


def test_banknifty_is_monthly_last_tuesday():
    exps = upcoming_expiries("BANKNIFTY", ist(2026, 10, 21), count=3, cal=CAL)
    assert exps == [date(2026, 10, 27), date(2026, 11, 24), date(2026, 12, 29)]


def test_monthly_expiry_rolls_back_over_holiday():
    weekday = get_spec("NIFTY").expiry_weekday
    cal = TradingCalendar(holidays={date(2026, 12, 29): "holiday", date(2026, 12, 28): "holiday"})
    assert monthly_expiry(2026, 12, weekday, cal) == date(2026, 12, 25)
    cal.holidays[date(2026, 12, 25)] = "Christmas"
    assert monthly_expiry(2026, 12, weekday, cal) == date(2026, 12, 24)


def test_resolve_expiries_after_monthly_rolls_to_next_month():
    e = resolve_expiries("NIFTY", ist(2026, 10, 28), cal=CAL)
    assert e.nearest == date(2026, 11, 3)
    assert e.next == date(2026, 11, 10)
    assert e.monthly == date(2026, 11, 24)
