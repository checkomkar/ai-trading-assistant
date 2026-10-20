"""End-to-end paper trading on a replayed day: ReplayProvider -> collector -> runner -> risk -> PaperBroker."""

import asyncio
import random
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from trader.backtest import data as bt_data
from trader.config import Settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.models import EventRow, PaperTradeRow
from trader.db.session import create_engine, create_sessionmaker
from trader.domain import Instrument, InstrumentType
from trader.execution.rehearsal import build, rehearse_day
from trader.execution.runner import LaneConfig
from trader.execution.summary import summarize, trade_to_dict
from trader.instruments.calendar import IST
from trader.market_data.candles import Candle, CandleAggregator
from trader.market_data.replay import ReplayProvider
from trader.strategy.spec import MOMENTUM_V1

DAYS = [date(2026, 10, d) for d in (13, 14, 15, 16, 19)]
REPLAY_DAY = date(2026, 10, 21)
IDX, FUT, VIX = "NSE:NIFTY50-INDEX", "NSE:NIFTY26OCTFUT", "NSE:INDIAVIX-INDEX"


def minute_candles(day: date, rng: random.Random, start: float, drift: float) -> list[tuple[str, Candle]]:
    out, px = [], start
    t0 = datetime.combine(day, time(9, 15), tzinfo=IST).astimezone(timezone.utc)
    for i in range(375):
        o = px
        c = round(o + drift + rng.gauss(0, 2.5), 2)
        hi, lo = round(max(o, c) + abs(rng.gauss(0, 1.2)), 2), round(min(o, c) - abs(rng.gauss(0, 1.2)), 2)
        ts = t0 + timedelta(minutes=i)
        out.append((IDX, Candle(IDX, "1m", ts, o, hi, lo, c, 0, True)))
        out.append((FUT, Candle(FUT, "1m", ts, round(o + 25, 2), round(hi + 25, 2), round(lo + 25, 2),
                                round(c + 25, 2), 1500 + rng.randint(0, 500), True)))
        out.append((VIX, Candle(VIX, "1m", ts, 14.0, 14.0, 14.0, 14.0, 0, True)))
        px = c
    return out


@pytest.fixture
async def world(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'paper.db').as_posix()}"
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    insts = [Instrument(IDX, "NIFTY", "NIFTY", "NSE", InstrumentType.INDEX),
             Instrument(FUT, "NIFTY26OCTFUT", "NIFTY", "NFO", InstrumentType.FUT, lot_size=65, expiry=date(2026, 10, 27)),
             Instrument(VIX, "INDIAVIX", "INDIAVIX", "NSE", InstrumentType.INDEX)]
    rng = random.Random(11)
    px = 25000.0
    rows = []
    for d in DAYS + [REPLAY_DAY]:
        # quiet drift on the history days; a strong, steady uptrend on the day we replay
        day_rows = minute_candles(d, rng, px, 3.0 if d == REPLAY_DAY else 0.2)
        px = day_rows[-3][1].close
        rows += day_rows
    async with sm() as s, s.begin():
        ids = await repo.upsert_instruments(s, insts)
        await repo.upsert_candles(s, [c for _, c in rows], ids)
    yield sm
    await engine.dispose()


def settings(**kw) -> Settings:
    return Settings(_env_file=None, paper_latency_ms=500, **kw)


async def run_day(sm, *, lanes=None, on_step=None, **kw):
    prov = ReplayProvider(sm, REPLAY_DAY, speed=0, underlyings=("NIFTY",))
    md, paper = build(settings(**kw), prov, sm)
    runner = await rehearse_day(md, paper, prov, lanes or [LaneConfig("NIFTY", "5m", "momentum")], on_step)
    return md, paper, runner


async def test_replay_rebuilds_the_stored_candles_exactly(world):
    prov = ReplayProvider(world, REPLAY_DAY, speed=0, underlyings=("NIFTY",))
    await prov.load()
    agg, built = CandleAggregator(("1m", "5m")), []
    for _, ticks in prov.steps():
        for t in ticks:
            if t.token in (f"REPLAY:NIFTY", "REPLAY:NIFTY-FUT"):
                built += agg.update(t.token, t.ts, t.ltp, t.volume)
    built += agg.flush()
    async with world() as s:
        for tf in ("1m", "5m"):
            stored = (await bt_data.load(s, "NIFTY", tf, REPLAY_DAY, REPLAY_DAY, "real")).bars
            got = {c.ts: c for c in built if c.token == "REPLAY:NIFTY" and c.timeframe == tf}
            assert len(stored) == len(got) == (375 if tf == "1m" else 75)
            for b in stored:
                g = got[b.ts]
                assert (g.open, g.high, g.low, g.close) == (b.open, b.high, b.low, b.close), (tf, b.ts)
        stored_fut = (await bt_data.load(s, "NIFTY", "5m", REPLAY_DAY, REPLAY_DAY, "real")).futures
    for c in (c for c in built if c.token == "REPLAY:NIFTY-FUT" and c.timeframe == "5m"):
        assert c.volume == stored_fut[c.ts].volume  # VWAP inputs are exact too


async def test_full_day_trades_are_traceable_signal_to_exit(world):
    md, paper, runner = await run_day(world)
    trades = paper.store.trades
    assert trades, "an uptrend day should trigger the momentum strategy"
    assert paper.broker.positions() == [] and all(ln.live is None for ln in runner.lanes)  # flat at the end
    assert {t["data_source"] for t in trades} == {"replay"}
    assert {t["option_pricing"] for t in trades} == {ReplayProvider.option_pricing}

    async with world() as s:
        rows = (await s.execute(select(PaperTradeRow))).scalars().all()
        assert len(rows) == len(trades)
        for row in rows:
            ev = (await s.execute(select(EventRow).where(EventRow.correlation_id == row.correlation_id)
                                  .order_by(EventRow.id))).scalars().all()
            kinds = [e.event_type for e in ev]
            assert kinds == ["signal.generated", "risk.approved", "order.placed", "order.filled",
                             "exit.triggered", "order.placed", "order.filled", "trade.closed"], kinds
            sig = ev[0].payload
            assert sig["features"] and sig["reason"] and sig["data_source"] == "replay"
            assert ev[1].payload["approved"] is True and len(ev[1].payload["checks"]) > 10
            buy, sell = ev[3].payload, ev[6].payload
            assert buy["side"] == "BUY" and sell["side"] == "SELL"
            assert buy["fill_price"] == row.entry_price and sell["fill_price"] == row.exit_price
            assert row.gross_pnl == pytest.approx((row.exit_price - row.entry_price) * row.quantity, abs=0.01)
            assert row.net_pnl == pytest.approx(row.gross_pnl - row.charges, abs=0.01)
            assert row.charges == pytest.approx(buy["charges"] + sell["charges"], abs=0.02)
            assert row.quantity == 65  # one lot, from the instrument's lot size
    s = summarize([trade_to_dict(r) for r in rows])
    assert s["trades"] == len(trades) and s["net_pnl"] == pytest.approx(paper.broker.realized_pnl, abs=0.05)
    assert s["wins"] + s["losses"] == s["trades"]


async def test_runs_are_deterministic(world):
    _, a, _ = await run_day(world)
    _, b, _ = await run_day(world)
    key = lambda t: (t["entry_time"], t["entry_price"], t["exit_time"], t["exit_price"], t["exit_reason"])  # noqa: E731
    assert [key(t) for t in a.store.trades] == [key(t) for t in b.store.trades]


async def test_first_signal_matches_the_backtest_on_the_same_bars(world):
    """Parity (M3 DoD): while flat, live-pipeline signals equal backtest signals on identical bars."""
    from trader.backtest.costs import default_cost_model
    from trader.backtest.engine import BacktestEngine
    from trader.backtest.pricing import SyntheticPricer
    from trader.instruments.calendar import get_calendar
    from trader.strategy import MomentumStrategy

    async with world() as s:
        d = await bt_data.load(s, "NIFTY", "5m", DAYS[0], REPLAY_DAY, "real")
    bt = BacktestEngine("NIFTY", MomentumStrategy(), SyntheticPricer(get_calendar()), default_cost_model(), 5)
    res = bt.run(d.bars, d.futures, d.vix)
    first_bt = min(t.signal_time for t in res.trades if t.signal_time.startswith("2026-10-21"))

    _, paper, _ = await run_day(world)
    sigs = [e for e in paper.store.events if e["event_type"] == "signal.generated"]
    assert sigs
    assert sigs[0]["payload"]["features"] == pytest.approx(
        next(t.features for t in res.trades if t.signal_time == first_bt))
    first_paper = (datetime.fromisoformat(first_bt).astimezone(timezone.utc))
    assert sigs[0]["ts"] >= first_paper  # emitted at the close of that same bar
    assert sigs[0]["ts"] - first_paper < timedelta(minutes=5, seconds=10)


async def test_risk_engine_blocks_entries_independently_of_the_strategy(world):
    _, paper, _ = await run_day(world, risk_max_trades_per_day=0)
    assert paper.store.trades == [] and paper.broker.orders() == []
    rejected = [e for e in paper.store.events if e["event_type"] == "risk.rejected"]
    assert rejected and all("trades_per_day" in " ".join(c["name"] for c in e["payload"]["checks"] if not c["passed"])
                            for e in rejected)


async def test_risk_per_trade_limit_blocks_oversized_entries(world):
    _, paper, _ = await run_day(world, risk_max_risk_per_trade=100)
    assert paper.store.trades == []
    assert any(not c["passed"] and c["name"] == "risk_per_trade"
               for e in paper.store.events if e["event_type"] == "risk.rejected" for c in e["payload"]["checks"])


async def test_kill_switch_flattens_and_blocks_further_entries(world):
    killed = {}

    async def on_step(t, runner):
        live = runner.lanes[0].live
        if not killed and live is not None and live.state == "OPEN":
            killed["at"] = t
            await runner.kill("test STOP")

    _, paper, runner = await run_day(world, on_step=on_step)
    assert killed and paper.risk.kill_switch.active
    trades = paper.store.trades
    assert len(trades) == 1 and trades[0]["exit_reason"] == "kill switch: test STOP"
    assert paper.broker.positions() == []
    later = [e for e in paper.store.events if e["event_type"] == "risk.rejected"]
    assert later and all(any(c["name"] == "kill_switch" and not c["passed"] for c in e["payload"]["checks"])
                         for e in later)
    assert any(e["event_type"] == "kill_switch.tripped" for e in paper.store.events)


async def test_stale_option_quote_blocks_entry(world):
    prov = ReplayProvider(world, REPLAY_DAY, speed=0, underlyings=("NIFTY",))
    md, paper = build(settings(), prov, world)
    await md.discover()
    runner = __import__("trader.execution.runner", fromlist=["PaperRunner"]).PaperRunner(
        md, paper.broker, paper.risk, paper.store, [LaneConfig("NIFTY", "5m", "momentum")])
    now = prov.start + timedelta(hours=1)
    tok = next(i.token for i in md.registry.all() if i.is_option)
    idx = md.registry.index("NIFTY").token
    from trader.domain import Tick
    md.latest[tok] = Tick(tok, now - timedelta(minutes=2), 100.0, bid=99.5, ask=100.5)
    md.quality._last[idx] = type("L", (), {"ts": now, "ltp": 25000.0, "volume": None})()  # noqa: SLF001
    ctx = runner._risk_context("NIFTY", tok, now)  # noqa: SLF001
    assert ctx.option_age_s == 120 and ctx.underlying_age_s == 0
    from trader.risk import OrderIntent
    d = paper.risk.check(OrderIntent("m", "NIFTY", tok, "x", 65, 65, 100.0, 25.0, "c"), ctx)
    assert not d.approved and any(c.name == "data_fresh" and not c.passed for c in d.checks)


async def test_spec_strategy_runs_through_the_same_pipeline(world):
    from trader.strategy.spec import StrategySpec

    spec = StrategySpec.model_validate(MOMENTUM_V1).model_dump(mode="json", by_alias=True, exclude_none=True)
    _, paper, runner = await run_day(world, lanes=[LaneConfig("NIFTY", "5m", "spec", spec=spec)])
    assert runner.lanes[0].label == spec["name"]
    assert paper.store.trades and all(t["strategy"] == spec["name"] for t in paper.store.trades)
