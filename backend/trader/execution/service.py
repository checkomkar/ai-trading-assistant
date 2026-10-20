"""Wires the paper-trading pieces to the market-data service (PRD M4).

The broker, risk engine and store exist from API start-up so the kill switch and the journal work
even when no strategy is running. Only the runner is started and stopped on demand.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.config import Settings
from trader.execution.paper import PaperBroker
from trader.execution.runner import LaneConfig, PaperRunner
from trader.execution.store import PaperStore
from trader.market_data.service import MarketDataService
from trader.risk import RiskEngine, RiskLimits


def risk_limits(s: Settings) -> RiskLimits:
    return RiskLimits(
        max_daily_loss=s.risk_max_daily_loss, max_consecutive_losses=s.risk_max_consecutive_losses,
        max_trades_per_day=s.risk_max_trades_per_day, max_open_positions=s.risk_max_open_positions,
        max_lots_per_trade=s.risk_max_lots_per_trade, max_risk_per_trade=s.risk_max_risk_per_trade,
        max_premium_exposure=s.risk_max_premium_exposure, max_underlying_exposure=s.risk_max_underlying_exposure,
        max_data_age_seconds=s.risk_max_data_age_seconds)


class PaperService:
    def __init__(self, md: MarketDataService, sessionmaker: async_sessionmaker | None, settings: Settings) -> None:
        self.md, self.sessionmaker, self.settings = md, sessionmaker, settings
        self.store = PaperStore(sessionmaker, md.now)
        self.risk = RiskEngine(risk_limits(settings))
        self.broker = PaperBroker(md.latest.get, md.now, instrument_fn=md.registry.by_token,
                                  latency=timedelta(milliseconds=settings.paper_latency_ms),
                                  max_quote_age=timedelta(seconds=settings.risk_max_data_age_seconds),
                                  capital=settings.paper_capital)
        self.runner: PaperRunner | None = None

    def default_lanes(self) -> list[LaneConfig]:
        return [LaneConfig(underlying=u.strip().upper(), timeframe=self.settings.paper_timeframe,
                           strategy=self.settings.paper_strategy)
                for u in self.settings.paper_underlyings.split(",") if u.strip()]

    async def start(self, lanes: list[LaneConfig] | None = None) -> PaperRunner:
        if self.runner is not None and self.runner.running:
            raise RuntimeError("the paper runner is already running")
        tick = 1.0  # one heartbeat per second of market time
        if self.md.provider.name == "replay":
            speed = getattr(self.md.provider, "speed", 1)
            tick = 0.02 if speed <= 0 else min(max(1.0 / speed, 0.02), 1.0)
        self.runner = PaperRunner(self.md, self.broker, self.risk, self.store, lanes or self.default_lanes(),
                                  sessionmaker=self.sessionmaker, tick_interval=tick)
        await self.runner.start()
        return self.runner

    async def stop(self) -> None:
        if self.runner is not None and self.runner.running:
            await self.runner.stop()
