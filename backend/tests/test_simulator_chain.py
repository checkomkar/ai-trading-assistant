import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from trader.domain import InstrumentType
from trader.instruments.calendar import IST, TradingCalendar
from trader.instruments.registry import InstrumentRegistry
from trader.instruments.strikes import OptionType
from trader.market_data.simulated import SimConfig, SimulatedMarketDataProvider
from trader.options.chain import build_chain

NOW = datetime(2026, 10, 21, 10, 30, tzinfo=IST).astimezone(timezone.utc)


def make(seed=1, **kw):
    return SimulatedMarketDataProvider(SimConfig(seed=seed, **kw), now_fn=lambda: NOW, calendar=TradingCalendar())


async def test_discovery():
    sim = make()
    insts = await sim.discover_instruments()
    types = {i.instrument_type for i in insts}
    assert types == {InstrumentType.INDEX, InstrumentType.FUT, InstrumentType.OPT}
    reg = InstrumentRegistry(insts)
    assert len(reg.expiries("NIFTY")) == 2 and len(reg.expiries("BANKNIFTY")) == 2
    assert len(reg.strikes("NIFTY", reg.expiries("NIFTY")[0])) == 41


def test_deterministic_with_seed():
    a, b = make(seed=42), make(seed=42)
    ta, tb = a.step(NOW), b.step(NOW)
    assert [(t.token, t.ltp) for t in ta] == [(t.token, t.ltp) for t in tb]


async def test_chain_is_sane():
    sim = make()
    reg = InstrumentRegistry(await sim.discover_instruments())
    latest = {}
    t = NOW
    for _ in range(3):
        for tick in sim.step(t):
            latest[tick.token] = tick
        t += timedelta(seconds=1)
    exp = reg.expiries("NIFTY")[0]
    spot = latest["SIM:NIFTY"].ltp
    chain = build_chain("NIFTY", exp, reg.options_for_expiry("NIFTY", exp), latest.get, spot, t, 0.065, 5)
    assert len(chain.rows) == 11
    ce = [r.ce.ltp for r in chain.rows]
    pe = [r.pe.ltp for r in chain.rows]
    assert ce == sorted(ce, reverse=True)  # calls get cheaper as strike rises
    assert pe == sorted(pe)
    atm = next(r for r in chain.rows if r.strike == chain.atm_strike)
    assert 0.08 < atm.ce.iv < 0.30
    assert chain.summary.pcr is not None and chain.summary.pcr > 0
    for r in chain.rows:
        assert r.ce.bid <= r.ce.ltp <= r.ce.ask


async def test_registry_resolver():
    reg = InstrumentRegistry(await make().discover_instruments())
    inst = reg.resolve_option("NIFTY", OptionType.CE, NOW, spot=25012, offset=1, expiry="nearest")
    assert inst is not None and inst.strike == 25050 and inst.option_type == OptionType.CE
    monthly = reg.resolve_expiry("NIFTY", "monthly", NOW)
    assert monthly is not None and monthly.weekday() == 1
    assert reg.resolve_option("BANKNIFTY", OptionType.PE, NOW, strike=56000).strike == 56000
    assert reg.future("NIFTY").instrument_type == InstrumentType.FUT


async def test_stream_reconnects_after_disconnect():
    sim = make(tick_interval=0.001, disconnect_probability=0.3)
    sim._policy.initial_delay = 0.001
    await sim.subscribe(i.token for i in await sim.discover_instruments())
    n = 0

    async def consume():
        nonlocal n
        async for _ in sim.stream():
            n += 1
            if sim.health.reconnects >= 2 and n > 50:
                return

    await asyncio.wait_for(consume(), timeout=10)
    assert sim.health.reconnects >= 2
    assert sim.health.connected


def test_backfill_ends_at_spot_and_is_continuous():
    sim = make(seed=5)
    candles = sim.backfill(60)
    nifty_1m = [c for c in candles if c.token == "SIM:NIFTY" and c.timeframe == "1m"]
    assert 58 <= len(nifty_1m) <= 60
    assert all(b.ts > a.ts for a, b in zip(nifty_1m, nifty_1m[1:]))
    assert all(c.low <= min(c.open, c.close) and c.high >= max(c.open, c.close) for c in nifty_1m)
    assert abs(nifty_1m[-1].close / sim._und["NIFTY"].spot - 1) < 0.003
    fut_5m = [c for c in candles if "FUT" in c.token and c.timeframe == "5m"]
    assert fut_5m and all(c.volume > 0 for c in fut_5m)
