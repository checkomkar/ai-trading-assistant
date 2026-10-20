from datetime import date, datetime, timedelta, timezone

import pytest

from trader.domain import Instrument, InstrumentType, Tick
from trader.execution import OrderRequest, OrderStatus, PaperBroker, Side
from trader.instruments.strikes import OptionType
from trader.risk import OrderIntent, RiskContext, RiskEngine, RiskLimits

T0 = datetime(2026, 10, 21, 4, 0, tzinfo=timezone.utc)  # 09:30 IST
TOKEN = "NSE:NIFTY25000CE"
INST = Instrument(TOKEN, "NIFTY25000CE", "NIFTY", "NFO", InstrumentType.OPT, lot_size=65,
                  expiry=date(2026, 10, 27), strike=25000.0, option_type=OptionType.CE)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


def make(latency_ms=500, quotes=None, **kw):
    clock = Clock()
    quotes = quotes if quotes is not None else {TOKEN: Tick(TOKEN, T0, 100.0, bid=99.5, ask=100.5)}
    broker = PaperBroker(quotes.get, clock, instrument_fn=lambda t: INST if t == TOKEN else None,
                         latency=timedelta(milliseconds=latency_ms), **kw)
    return broker, clock, quotes


def buy(qty=65, cid="a"):
    return OrderRequest(TOKEN, INST.symbol, Side.BUY, qty, cid, "corr1")


def sell(qty=65, cid="b"):
    return OrderRequest(TOKEN, INST.symbol, Side.SELL, qty, cid, "corr1")


def test_buy_fills_at_ask_after_latency_using_the_quote_at_fill_time():
    b, clock, quotes = make()
    o = b.submit(buy())
    assert o.status == OrderStatus.PENDING
    b.process(clock())  # latency not yet elapsed
    assert o.status == OrderStatus.PENDING
    clock.advance(seconds=1)
    quotes[TOKEN] = Tick(TOKEN, clock(), 101.0, bid=100.8, ask=101.2)  # market moved while in flight
    assert b.process(clock()) == [o]
    assert o.status == OrderStatus.FILLED and o.fill_price == 101.2  # the ask at fill time, not 100.5
    assert b.position(TOKEN).quantity == 65 and o.charges > 0


def test_sell_fills_at_bid_and_round_trip_pnl_includes_charges():
    b, clock, quotes = make(latency_ms=0)
    b.submit(buy())
    quotes[TOKEN] = Tick(TOKEN, clock(), 120.0, bid=119.5, ask=120.5)
    s = b.submit(sell())
    assert s.status == OrderStatus.FILLED and s.fill_price == 119.5
    assert b.position(TOKEN) is None
    gross = (119.5 - 100.5) * 65
    assert b.realized_pnl == pytest.approx(gross - sum(o.charges for o in b.orders()), abs=0.01)
    assert b.realized_pnl < gross


def test_no_bid_ask_falls_back_to_modelled_spread():
    b, clock, _ = make(latency_ms=0, quotes={TOKEN: Tick(TOKEN, T0, 100.0)})
    o = b.submit(buy())
    assert o.fill_price == b.exe.buy(100.0) > 100.0


def test_idempotent_client_order_id():
    b, _, _ = make(latency_ms=0)
    a, again = b.submit(buy(cid="same")), b.submit(buy(cid="same"))
    assert a is again and len(b.orders()) == 1 and b.position(TOKEN).quantity == 65


def test_rejections():
    b, clock, quotes = make(latency_ms=0)
    assert b.submit(buy(qty=70, cid="1")).reason.startswith("quantity 70 is not a multiple")
    assert "short selling" in b.submit(sell(cid="2")).reason
    quotes[TOKEN] = Tick(TOKEN, T0 - timedelta(seconds=60), 100.0, bid=99.5, ask=100.5)
    assert "stale quote" in b.submit(buy(cid="3")).reason
    assert "no quote" in b.submit(OrderRequest("X", "X", Side.BUY, 1, "4")).reason
    rich, _, _ = make(latency_ms=0, capital=1000)
    assert "insufficient funds" in rich.submit(buy()).reason


def test_exit_still_fills_on_a_stale_quote_but_is_flagged():
    b, clock, quotes = make(latency_ms=0)
    b.submit(buy())
    clock.advance(minutes=5)  # the feed went quiet while we held the position
    o = b.submit(sell())
    assert o.status == OrderStatus.FILLED and "stale quote" in o.reason


def test_cancel_pending_order():
    import asyncio
    b, clock, _ = make()
    o = b.submit(buy())
    asyncio.run(b.cancel_order(o.id))
    clock.advance(seconds=5)
    assert b.process(clock()) == [] and o.status == OrderStatus.CANCELLED and b.position(TOKEN) is None


# ---- risk engine ------------------------------------------------------------------------------
def intent(**kw):
    base = dict(strategy="momentum", underlying="NIFTY", token=TOKEN, symbol=INST.symbol, quantity=65,
                lot_size=65, price=100.0, stop_loss_pct=25.0, correlation_id="c1")
    return OrderIntent(**{**base, **kw})


def ctx(**kw):
    base = dict(now=T0, market_open=True, instrument_valid=True, underlying_age_s=1.0, option_age_s=1.0,
                available_cash=100_000.0)
    return RiskContext(**{**base, **kw})


def failed(engine, i=None, c=None):
    return {x.name for x in engine.check(i or intent(), c or ctx()).checks if not x.passed}


def test_clean_order_is_approved_and_every_check_is_reported():
    d = RiskEngine().check(intent(), ctx())
    assert d.approved and len(d.checks) >= 15 and not d.reasons


def test_each_limit_blocks_independently():
    e = RiskEngine(RiskLimits(max_daily_loss=1000, max_trades_per_day=2, max_consecutive_losses=2))
    assert failed(e, c=ctx(market_open=False)) == {"market_open"}
    assert failed(e, c=ctx(underlying_age_s=60)) == {"data_fresh"}
    assert failed(e, c=ctx(option_age_s=None)) == {"data_fresh"}  # a missing quote is stale too
    assert failed(e, c=ctx(instrument_valid=False)) == {"instrument_valid"}
    assert failed(e, c=ctx(available_cash=500)) == {"margin"}
    assert failed(e, intent(quantity=130)) >= {"lots_per_trade"}
    assert failed(e, intent(quantity=100)) >= {"quantity_valid"}
    assert failed(e, intent(price=300.0)) == {"risk_per_trade"}  # 19500 premium x 25% = 4875 > 4000
    assert failed(e, c=ctx(unrealized_pnl=-1500)) == {"daily_loss"}
    e.set_strategy_enabled("momentum", False)
    assert failed(e) == {"strategy_enabled"}


def test_counters_and_exposure():
    e = RiskEngine(RiskLimits(max_consecutive_losses=2, max_trades_per_day=3, max_open_positions=1))
    e.on_entry("a", "NIFTY", 6500)
    assert failed(e) >= {"open_positions"}
    e.on_exit("a", -500)
    e.on_entry("b", "NIFTY", 6500)
    e.on_exit("b", -500)
    assert failed(e) == {"consecutive_losses"}
    e.on_entry("c", "NIFTY", 6500)
    e.on_exit("c", +900)  # a win resets the streak
    assert e.consecutive_losses == 0 and failed(e) == {"trades_per_day"}
    e.roll_day(date(2026, 10, 22))
    assert failed(e) == set() and e.day_pnl == 0


def test_failed_entry_releases_its_reservation():
    e = RiskEngine()
    e.on_entry("a", "NIFTY", 6500)
    e.on_entry_failed("a")
    assert e.open_positions == 0 and e.trades_today == 0 and e.exposure() == 0


def test_kill_switch_blocks_entries_but_never_exits():
    e = RiskEngine()
    e.trip_kill_switch("test", T0)
    assert failed(e) == {"kill_switch"}
    assert e.check(intent(reduce_only=True), ctx(market_open=False)).approved
    e.reset_kill_switch()
    assert failed(e) == set()


def test_exposure_limits():
    e = RiskEngine(RiskLimits(max_open_positions=5, max_underlying_exposure=10_000, max_premium_exposure=15_000))
    e.on_entry("a", "NIFTY", 6500)
    assert failed(e) == {"underlying_exposure"}  # 6500 + 6500 > 10k
    assert failed(e, intent(underlying="BANKNIFTY")) == set()
    e.on_entry("b", "BANKNIFTY", 6500)
    assert "premium_exposure" in failed(e, intent(underlying="BANKNIFTY"))
