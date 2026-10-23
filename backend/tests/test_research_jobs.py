"""B4 (PRD §24.5): many runs over one loaded dataset, in parallel, as background jobs."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from tests.test_backtest_api import END, _seed
from trader.backtest.runner import BacktestRequest, load_for
from trader.db.migrate import upgrade_db
from trader.db.models import ResearchJobRow
from trader.db.session import create_engine, create_sessionmaker
from trader.research.jobs import JobManager
from trader.research.parallel import run_many

START = END - timedelta(days=20)


@pytest.fixture
async def sm(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'r.db').as_posix()}"
    await asyncio.to_thread(upgrade_db, url)
    await _seed(url)
    engine = create_engine(url)
    yield create_sessionmaker(engine)
    await engine.dispose()


def reqs(n: int) -> list[BacktestRequest]:
    return [BacktestRequest(start=START, end=END, strategy_params={"adx_min": 5, "stop_loss_pct": 15 + 10 * i})
            for i in range(n)]


async def test_parallel_runs_match_serial_runs_and_report_progress(sm):
    data = await load_for(sm, reqs(1)[0])
    seen: list[tuple[int, int]] = []
    serial = run_many(reqs(4), data, workers=1, progress=lambda d, t: seen.append((d, t)))
    par = await asyncio.to_thread(run_many, reqs(4), data, workers=2)
    assert seen[-1] == (4, 4)
    assert [r["metrics"] for r in serial] == [r["metrics"] for r in par]
    assert len({r["metrics"]["total_net_pnl"] for r in serial}) > 1  # the settings really differ


async def test_a_bad_combination_is_reported_without_sinking_the_rest(sm):
    data = await load_for(sm, reqs(1)[0])
    bad = BacktestRequest(start=START, end=END, pricing="nonsense")
    out = run_many([reqs(1)[0], bad], data, workers=1)
    assert "metrics" in out[0] and "error" in out[1]


async def test_cancel_stops_a_serial_batch_between_runs(sm):
    data = await load_for(sm, reqs(1)[0])
    calls = {"n": 0}

    def cancelled() -> bool:
        calls["n"] += 1
        return calls["n"] > 2

    out = run_many(reqs(5), data, workers=1, cancelled=cancelled)
    assert sum(r is not None for r in out) == 2 and out[-1] is None


async def test_jobs_run_in_the_background_with_progress_and_a_stored_result(sm):
    jm = JobManager(sm, max_running=2, per_owner=1)
    release = asyncio.Event()

    async def work(ctx):
        ctx.progress(1, 2, "half way")
        await release.wait()
        ctx.progress(2, 2, "done")
        return {"answer": 42}

    a = await jm.submit("alice", "test", {"x": 1}, work, total=2)
    b = await jm.submit("alice", "test", {"x": 2}, work, total=2)  # queued: one running job per account
    c = await jm.submit("bob", "test", {"x": 3}, work, total=2)
    await asyncio.sleep(0.2)
    async with sm() as s:
        rows = {r: await s.get(ResearchJobRow, r) for r in (a, b, c)}
    assert rows[a].status == "running" and rows[b].status == "queued" and rows[c].status == "running"
    assert rows[a].note == "half way" and rows[a].done == 1
    release.set()
    for j in (a, b, c):
        await jm.wait(j, 10)
    async with sm() as s:
        row = await s.get(ResearchJobRow, b)
    assert row.status == "done" and row.result == {"answer": 42} and row.done == 2


async def test_cancel_failure_and_restart_recovery(sm):
    jm = JobManager(sm)

    async def slow(ctx):
        while not ctx.cancelled:
            await asyncio.sleep(0.01)
        return {"partial": True}

    async def boom(ctx):
        raise ValueError("bad parameter")

    j1 = await jm.submit("alice", "test", {}, slow)
    await asyncio.sleep(0.05)
    assert jm.cancel(j1, "mallory") is False  # another account cannot cancel it
    assert jm.cancel(j1, "alice") is True
    await jm.wait(j1, 5)
    j2 = await jm.submit("alice", "test", {}, boom)
    await jm.wait(j2, 5)
    async with sm() as s:
        r1, r2 = await s.get(ResearchJobRow, j1), await s.get(ResearchJobRow, j2)
    assert r1.status == "cancelled" and r2.status == "failed" and "bad parameter" in r2.error
    # a job left "running" by a crashed process is marked failed at the next start
    async with sm() as s, s.begin():
        s.add(ResearchJobRow(id="stale1", owner="alice", kind="test", status="running", done=0, total=1, note="", params={}))
    assert await JobManager(sm).recover() == 1
    async with sm() as s:
        assert (await s.get(ResearchJobRow, "stale1")).status == "failed"
