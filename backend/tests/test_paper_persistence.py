"""Restart safety of the paper account (PRD §22.3) and heartbeat hardening found in the M4 review.

A "crash" is a replayed day halted mid-trade after the state was flushed; the restart is a brand new
MarketDataService / PaperService / PaperRunner over the same database, resumed from the same step."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from tests.test_paper_runner import IDX, REPLAY_DAY, settings, world  # noqa: F401 - `world` is a fixture
from trader.db.models import EventRow, PaperStateRow, PaperTradeRow
from trader.execution.paper import PaperBroker
from trader.execution.rehearsal import build, play, rehearse_day, start_rehearsal
from trader.execution.runner import LaneConfig, PaperRunner
from trader.execution.state import restore_snapshot
from trader.execution.types import OrderRequest, Side
from trader.market_data.candles import Candle
from trader.market_data.replay import ReplayProvider

LANE = LaneConfig("NIFTY", "5m", "momentum")


def fresh(sm, **kw):
    prov = ReplayProvider(sm, REPLAY_DAY, speed=0, underlyings=("NIFTY",))
    md, paper = build(settings(**kw), prov, sm, persist_state=True)
    return prov, md, paper


async def crash_while(world, when, **kw):
    """Run the day until `when(t, runner)`, flush what the heartbeat would have, then stop dead."""
    prov, md, paper = fresh(world, **kw)
    await paper.restore()  # what the API does at start-up
    halted = {}

    async def on_step(t, runner):
        if await when(t, runner):
            halted["t"] = t
            await runner.store.flush()
            return True

    runner = await rehearse_day(md, paper, prov, [LANE], on_step)
    assert halted, "the crash condition never happened"
    return prov, paper, runner, halted["t"]


async def open_for_two_minutes(t, runner):
    live = runner.lanes[0].live
    return live is not None and live.state == "OPEN" and t - live.entry_time >= timedelta(minutes=2)


async def restart(world, prov, t, on_step=None, **kw):
    md2, paper2 = build(settings(**kw), prov, world, persist_state=True)
    runner2 = await rehearse_day(md2, paper2, prov, [LANE], on_step, restore=True, resume_after=t)
    return paper2, runner2


async def test_restart_in_the_middle_of_an_open_trade_still_exits_and_journals(world):
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes)
    before = runner.export_lives()[0]
    assert before["state"] == "OPEN" and before["high_water"] >= before["entry_price"]

    seen = {}

    async def on_step(ts, r2):
        if not seen:  # the first step after the restart
            seen["lives"] = r2.export_lives()
            seen["positions"] = [p.quantity for p in r2.broker.positions()]
            seen["open"], seen["trades"] = r2.risk.open_positions, r2.risk.trades_today
            seen["realized"] = r2.broker.realized_pnl

    paper2, runner2 = await restart(world, prov, t, on_step)

    [after] = seen["lives"]
    for k in ("cid", "token", "quantity", "entry_price", "entry_time", "initial_stop", "target", "signal"):
        assert after[k] == before[k], k
    assert after["entry_order"]["id"] == before["entry_order"]["id"]
    assert after["stop"] >= before["initial_stop"] and after["high_water"] >= before["high_water"]
    assert seen["positions"] == [65] and seen["open"] == 1 and seen["trades"] >= 1

    # it kept being managed and closed exactly once, under the same correlation id
    assert runner2.lanes[0].live is None and paper2.broker.positions() == [] and paper2.risk.open_positions == 0
    async with world() as s:
        rows = (await s.execute(select(PaperTradeRow).order_by(PaperTradeRow.id))).scalars().all()
        mine = [r for r in rows if r.correlation_id == before["cid"]]
        assert len(mine) == 1 and len({r.correlation_id for r in rows}) == len(rows)
        ev = (await s.execute(select(EventRow).where(EventRow.correlation_id == before["cid"])
                              .order_by(EventRow.id))).scalars().all()
    kinds = [e.event_type for e in ev]
    assert kinds == ["signal.generated", "risk.approved", "order.placed", "order.filled", "position.restored",
                     "exit.triggered", "order.placed", "order.filled", "trade.closed"], kinds
    row = mine[0]
    buy, sell = ev[3].payload, ev[7].payload
    assert row.entry_price == buy["fill_price"] == before["entry_price"] and row.exit_price == sell["fill_price"]
    assert row.charges == pytest.approx(buy["charges"] + sell["charges"], abs=0.02)  # entry charges survived
    assert row.net_pnl == pytest.approx((row.exit_price - row.entry_price) * row.quantity - row.charges, abs=0.02)
    # account and risk counters carried straight through: the day's P&L is the journal's, not a fresh zero
    assert paper2.risk.day_pnl == pytest.approx(sum(r.net_pnl for r in rows), abs=0.05)
    assert paper2.broker.realized_pnl == pytest.approx(sum(r.net_pnl for r in rows), abs=0.05)
    assert paper2.risk.trades_today == len(rows)


async def test_restart_does_not_reset_the_days_counters(world):
    """A restart must not hand a bot that has hit its trade limit a fresh allowance."""
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes, risk_max_trades_per_day=1)
    paper2, _ = await restart(world, prov, t, risk_max_trades_per_day=1)
    async with world() as s:
        n = len((await s.execute(select(PaperTradeRow))).scalars().all())
    assert n == 1 and paper2.risk.trades_today == 1  # the one trade the limit allows; nothing new after it
    rejected = [e for e in paper2.store.events if e["event_type"] == "risk.rejected"]
    assert rejected and all(any(c["name"] == "trades_per_day" and not c["passed"] for c in e["payload"]["checks"])
                            for e in rejected)


async def test_kill_switch_survives_a_restart_and_keeps_blocking_entries(world):
    async def kill_then_flat(t, runner):
        live = runner.lanes[0].live
        if live is not None and live.state == "OPEN" and not runner.risk.kill_switch.active:
            await runner.kill("test STOP")
        return runner.risk.kill_switch.active and runner.lanes[0].live is None

    prov, paper, _, t = await crash_while(world, kill_then_flat)
    paper2, runner2 = await restart(world, prov, t)
    ks = paper2.risk.kill_switch
    assert ks.active and ks.reason == "test STOP" and ks.since is not None
    assert paper2.restore_report["status"] == "restored" and paper2.restore_report["kill_switch"] is True
    async with world() as s:
        assert len((await s.execute(select(PaperTradeRow))).scalars().all()) == 1  # nothing traded after the restart
    later = [e for e in paper2.store.events if e["event_type"] == "risk.rejected"]
    assert later and all(any(c["name"] == "kill_switch" and not c["passed"] for c in e["payload"]["checks"])
                         for e in later)


async def test_kill_switch_set_without_a_runner_is_saved_and_reset_is_saved_too(world):
    prov, md, paper = fresh(world)
    await paper.restore()
    paper.risk.trip_kill_switch("api STOP", md.now())
    paper.risk.set_strategy_enabled("momentum", False)
    await paper.persist()

    _, md2, paper2 = fresh(world)
    report = await paper2.restore()
    assert report["status"] == "restored"
    assert paper2.risk.kill_switch.active and paper2.risk.kill_switch.reason == "api STOP"
    assert paper2.risk.disabled_strategies == {"momentum"}

    paper2.risk.reset_kill_switch()
    await paper2.persist()
    _, _, paper3 = fresh(world)
    await paper3.restore()
    assert not paper3.risk.kill_switch.active


async def test_state_from_a_previous_day_is_flagged_and_not_resumed(world):
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes)
    symbol = runner.lanes[0].live.inst.symbol
    paper.risk.trip_kill_switch("stale STOP", t)
    await paper.persist()

    prov.clock = t + timedelta(days=1)  # the next IST trading day
    md2, paper2 = build(settings(), prov, world, persist_state=True)
    report = await paper2.restore()
    assert report["status"] == "stale" and report["saved_trading_day"] == REPLAY_DAY.isoformat()
    assert len(report["stale_positions"]) == 1 and symbol in report["stale_positions"][0]
    assert paper2.broker.positions() == [] and paper2.risk.open_positions == 0 and paper2.carried == []
    assert paper2.risk.trades_today == 0 and paper2.risk.day_pnl == 0
    assert paper2.risk.kill_switch.active  # the latch is not cleared by a restart, only reported
    stale = [e for e in paper2.store.events if e["event_type"] == "state.stale"]
    assert len(stale) == 1 and stale[0]["payload"]["stale_positions"]

    await md2.discover()
    runner2 = PaperRunner(md2, paper2.broker, paper2.risk, paper2.store, [LANE])
    runner2.adopt(paper2.carried)  # nothing to adopt
    runner2.restore_report = paper2.restore_report
    assert runner2.status()["restore"]["status"] == "stale" and runner2.lanes[0].live is None


async def test_a_replay_snapshot_never_touches_the_live_one_and_a_foreign_row_is_ignored(world):
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes)
    async with world() as s:
        keys = [r.key for r in (await s.execute(select(PaperStateRow))).scalars().all()]
    assert keys == ["paper-replay"]  # keyed by data source

    row = await paper.store.load_state("replay")
    assert await paper.store.load_state("fyers") is None
    _, md2, paper2 = fresh(world)
    report, lives = restore_snapshot(paper2.risk, paper2.broker, row, today=REPLAY_DAY.isoformat(), data_source="fyers")
    assert report["status"] == "ignored" and lives == [] and paper2.broker.positions() == []


async def test_restart_is_refused_when_no_lane_can_manage_the_restored_position(world):
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes)
    md2, paper2 = build(settings(), prov, world, persist_state=True)
    with pytest.raises(RuntimeError, match="no lane"):
        await rehearse_day(md2, paper2, prov, [LaneConfig("BANKNIFTY", "5m", "momentum")], None, restore=True,
                           resume_after=t)
    assert paper2.carried and paper2.broker.positions()  # still held, not silently dropped


async def test_stop_then_start_keeps_managing_the_open_position(world):
    """Before: a stopped runner left the broker position and risk reservation behind with nobody to exit it."""
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes)
    cid = runner.lanes[0].live.cid
    await runner.stop()
    assert runner.lanes[0].live.state == "OPEN" and (await paper.store.load_state("replay"))["state"]["lives"]

    runner2 = PaperRunner(paper.runner.md, paper.broker, paper.risk, paper.store, [LANE], sessionmaker=world)
    runner2.adopt(runner.export_lives())
    runner2._bind_tokens(runner2.lanes[0])  # noqa: SLF001
    runner2.running = True
    assert await play(paper.runner.md, runner2, prov, resume_after=t) is False
    assert runner2.lanes[0].live is None and paper.broker.positions() == [] and paper.risk.open_positions == 0
    assert [r["correlation_id"] for r in paper.store.trades].count(cid) == 1


async def test_stopping_withdraws_an_entry_that_has_not_filled(world):
    async def entry_pending(t, runner):
        live = runner.lanes[0].live
        return live is not None and live.state == "ENTRY"

    prov, paper, runner, t = await crash_while(world, entry_pending)
    assert paper.risk.open_positions == 1 and paper.risk.trades_today == 1
    await runner.stop()
    assert runner.lanes[0].live is None and paper.risk.open_positions == 0 and paper.risk.trades_today == 0
    assert paper.broker.positions() == [] and all(o.status.value != "PENDING" for o in paper.broker.orders())
    assert any(e["event_type"] == "order.cancelled" for e in paper.store.events)
    assert (await paper.store.load_state("replay"))["state"]["lives"] == []


async def test_an_unfilled_entry_is_not_part_of_a_snapshot(world):
    async def entry_pending(t, runner):
        live = runner.lanes[0].live
        return live is not None and live.state == "ENTRY"

    _, paper, runner, _ = await crash_while(world, entry_pending)
    snap = (await paper.store.load_state("replay"))["state"]
    assert snap["lives"] == [] and snap["risk"]["open"] == {} and snap["risk"]["trades_today"] == 0
    assert snap["broker"]["positions"] == []


# ---- heartbeat hardening ------------------------------------------------------------------------------------
async def test_a_failing_strategy_cannot_stop_the_open_position_being_managed(world):
    """Before: an exception in bar handling aborted the whole tick, so no stop or fill was processed on it."""
    prov, paper, runner, t = await crash_while(world, open_for_two_minutes)

    def boom(state):
        raise RuntimeError("strategy blew up")

    runner.lanes[0].strategy.generate_signal = boom
    runner.lanes[0].strategy.exit_signal = boom
    assert await play(paper.runner.md, runner, prov, resume_after=t) is False
    assert runner.errors > 0 and any(e["event_type"] == "runner.error" for e in paper.store.events)
    assert runner.lanes[0].live is None and paper.broker.positions() == []  # stop/target/EOD still closed it
    assert len(paper.store.trades) == 1


async def test_bars_arriving_between_heartbeats_are_all_processed_in_order(world):
    prov, md, paper = fresh(world)
    runner = await start_rehearsal(md, paper, [LANE])
    t0 = prov.start + timedelta(hours=1)
    prov.clock = t0
    seen = []

    async def on_bar(lane, bar, fut, vix, now):
        seen.append(bar.ts)

    runner.on_bar = on_bar  # type: ignore[method-assign]
    for i in range(3):
        runner.on_candle(Candle("REPLAY:NIFTY", "5m", t0 + timedelta(minutes=5 * i), 1, 1, 1, 1, 0, True))
    await runner.tick(t0 + timedelta(seconds=10))  # past the futures grace period
    assert seen == [t0 + timedelta(minutes=5 * i) for i in range(3)]


async def test_a_second_start_on_the_same_day_does_not_reset_the_days_pnl(world):
    prov, md, paper = fresh(world)
    paper.risk.roll_day(REPLAY_DAY)
    paper.broker.realized_pnl = -1234.0
    runner = PaperRunner(md, paper.broker, paper.risk, paper.store, [LANE])
    await runner.tick(prov.start)
    assert paper.broker.realized_pnl == -1234.0


def test_a_pending_order_cannot_be_enlarged_after_the_risk_check():
    from datetime import datetime as dt
    from trader.domain import Tick

    now = dt(2026, 10, 21, 5, tzinfo=timezone.utc)
    b = PaperBroker(lambda tok: Tick(tok, now, 100.0, bid=99.5, ask=100.5), lambda: now,
                    latency=timedelta(seconds=5))
    o = b.submit(OrderRequest("T", "SYM", Side.BUY, 65, "c1"))

    import asyncio
    with pytest.raises(ValueError, match="cannot raise"):
        asyncio.run(b.modify_order(o.id, quantity=650))
    assert o.request.quantity == 65


def test_kill_switch_set_through_the_api_survives_an_app_restart(tmp_path):
    import time
    from fastapi.testclient import TestClient
    from trader.api.main import create_app
    from trader.config import Settings

    cfg = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}", sim_seed=5,
                   sim_tick_interval_seconds=0.05)

    def boot(c):
        deadline = time.time() + 10
        while time.time() < deadline and c.get("/market/NIFTY").status_code != 200:
            time.sleep(0.1)

    with TestClient(create_app(cfg)) as c:
        boot(c)
        assert c.get("/paper/status").json()["restore"]["status"] == "none"
        c.post("/paper/kill", json={"reason": "before restart"})
    with TestClient(create_app(cfg)) as c:  # a new process over the same database
        boot(c)
        s = c.get("/paper/status").json()
        assert s["restore"]["status"] == "restored" and s["restore"]["kill_switch"] is True
        assert s["risk"]["kill_switch"]["active"] and s["risk"]["kill_switch"]["reason"] == "before restart"
        c.post("/paper/kill/reset")
    with TestClient(create_app(cfg)) as c:
        boot(c)
        assert not c.get("/paper/risk").json()["kill_switch"]["active"]


async def test_kill_switch_keeps_an_entry_that_filled_before_it_could_be_cancelled(world):
    """Before: kill() dropped the lane's entry as 'cancelled' even if the broker had just filled it."""
    async def entry_pending(t, runner):
        live = runner.lanes[0].live
        return live is not None and live.state == "ENTRY"

    prov, paper, runner, t = await crash_while(world, entry_pending)
    filled = paper.broker.process(t + timedelta(seconds=2))  # the fill lands; the runner has not seen it yet
    assert filled and runner.lanes[0].live.state == "ENTRY" and paper.broker.positions()
    prov.clock = t + timedelta(seconds=2)
    await runner.kill("race STOP")
    await runner.tick(t + timedelta(seconds=4))
    assert runner.lanes[0].live is None and paper.broker.positions() == [] and paper.risk.open_positions == 0
    assert [tr["exit_reason"] for tr in paper.store.trades] == ["kill switch: race STOP"]
