"""Run one stored day through the full paper pipeline in-process, deterministically (PRD §14.1, M4).

Unlike the API's replay mode (paced by wall-clock time), this drives the clock by hand: every 5 s
of market time it ingests the replayed ticks, hands closed candles to the runner, and calls
`runner.tick(now)`. There are no background tasks, so the same day always produces the same trades.
"""

from __future__ import annotations

from datetime import datetime
from typing import Awaitable, Callable

from trader.config import Settings
from trader.execution.runner import LaneConfig, PaperRunner
from trader.execution.service import PaperService
from trader.market_data.replay import ReplayProvider
from trader.market_data.service import TOPIC_CANDLES, MarketDataService


async def start_rehearsal(md: MarketDataService, paper: PaperService, lanes: list[LaneConfig] | None = None,
                          *, restore: bool = False) -> PaperRunner:
    """Wire a runner the way `PaperService.start` does, minus the background tasks. With `restore`, saved
    paper state (kill switch, same-day counters, open positions) is loaded and adopted first: a restart."""
    await md.discover()
    if restore:
        await paper.restore()
    runner = PaperRunner(md, paper.broker, paper.risk, paper.store, lanes or paper.default_lanes(),
                         sessionmaker=paper.sessionmaker, tick_interval=1.0)
    runner.restore_report = paper.restore_report
    paper.runner = runner
    if restore and paper.carried:
        runner.adopt(paper.carried)
        paper.carried = []
    for lane in runner.lanes:
        runner._bind_tokens(lane)  # noqa: SLF001 - same wiring `start()` does, minus the background tasks
        await runner.warmup(lane)
    runner.running = True
    runner.started_at = md.now()
    runner.store.emit("runner.started", None, {"mode": "rehearsal", "data_source": runner.data_source,
                                               "option_pricing": runner.option_pricing})
    return runner


async def play(md: MarketDataService, runner: PaperRunner, provider: ReplayProvider,
               on_step: Callable[[datetime, PaperRunner], Awaitable[bool | None]] | None = None,
               *, resume_after: datetime | None = None) -> bool:
    """Feed the replayed day to `runner`, one 5 s step at a time. Returns True if `on_step` halted it."""
    with md.bus.subscribe(TOPIC_CANDLES) as q:
        for t, ticks in provider.steps():
            if resume_after is not None and t <= resume_after:
                continue
            provider.clock = t
            for tick in ticks:
                md.ingest(tick)
            for c in md.candles.close_due(t):  # instruments that stopped ticking
                md.bus.publish(TOPIC_CANDLES, c)
            while not q.empty():
                runner.on_candle(q.get_nowait())
            await runner.tick(t)
            if on_step and await on_step(t, runner):
                return True
        provider.clock = provider.end
        await runner.tick(provider.end)
    return False


async def rehearse_day(md: MarketDataService, paper: PaperService, provider: ReplayProvider,
                       lanes: list[LaneConfig] | None = None,
                       on_step: Callable[[datetime, PaperRunner], Awaitable[bool | None]] | None = None,
                       *, restore: bool = False, resume_after: datetime | None = None) -> PaperRunner:
    """`md` must have been built with `provider` and `now_fn=provider.clock_now`.
    `on_step(t, runner)` runs after each step, e.g. for a test to trip the kill switch at a given time; if it
    returns True the day stops there, without flattening or a final flush (a simulated crash).
    `resume_after` skips the steps up to and including that time, for the restarted half of such a day."""
    runner = await start_rehearsal(md, paper, lanes, restore=restore)
    halted = await play(md, runner, provider, on_step, resume_after=resume_after)
    runner.running = False
    if not halted:
        runner.store.emit("runner.stopped", None, {"mode": "rehearsal"})
        await runner.store.flush()
    return runner


def build(settings: Settings, provider: ReplayProvider, sessionmaker,  # noqa: ANN001
          *, persist_state: bool = False) -> tuple[MarketDataService, PaperService]:
    md = MarketDataService(provider, None, settings, now_fn=provider.clock_now)
    return md, PaperService(md, sessionmaker, settings, persist_state=persist_state)
