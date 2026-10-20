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


async def rehearse_day(md: MarketDataService, paper: PaperService, provider: ReplayProvider,
                       lanes: list[LaneConfig] | None = None,
                       on_step: Callable[[datetime, PaperRunner], Awaitable[None]] | None = None) -> PaperRunner:
    """`md` must have been built with `provider` and `now_fn=provider.clock_now`.
    `on_step(t, runner)` runs after each step, e.g. for a test to trip the kill switch at a given time."""
    await md.discover()
    runner = PaperRunner(md, paper.broker, paper.risk, paper.store, lanes or paper.default_lanes(),
                         sessionmaker=paper.sessionmaker, tick_interval=1.0)
    paper.runner = runner
    for lane in runner.lanes:
        runner._bind_tokens(lane)  # noqa: SLF001 - same wiring `start()` does, minus the background tasks
        await runner.warmup(lane)
    runner.running = True
    runner.started_at = md.now()
    runner.store.emit("runner.started", None, {"mode": "rehearsal", "day": provider.day.isoformat(),
                                               "data_source": runner.data_source,
                                               "option_pricing": runner.option_pricing})
    with md.bus.subscribe(TOPIC_CANDLES) as q:
        for t, ticks in provider.steps():
            provider.clock = t
            for tick in ticks:
                md.ingest(tick)
            for c in md.candles.close_due(t):  # instruments that stopped ticking
                md.bus.publish(TOPIC_CANDLES, c)
            while not q.empty():
                runner.on_candle(q.get_nowait())
            await runner.tick(t)
            if on_step:
                await on_step(t, runner)
        provider.clock = provider.end
        await runner.tick(provider.end)
    runner.running = False
    runner.store.emit("runner.stopped", None, {"mode": "rehearsal"})
    await runner.store.flush()
    return runner


def build(settings: Settings, provider: ReplayProvider, sessionmaker) -> tuple[MarketDataService, PaperService]:  # noqa: ANN001
    md = MarketDataService(provider, None, settings, now_fn=provider.clock_now)
    return md, PaperService(md, sessionmaker, settings)
