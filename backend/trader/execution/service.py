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
from trader.execution.state import build_snapshot, restore_snapshot
from trader.execution.store import PaperStore
from trader.instruments.calendar import IST
from trader.logging import get_logger
from trader.market_data.service import MarketDataService
from trader.risk import RiskEngine, RiskLimits

log = get_logger("paper")


def risk_limits(s: Settings) -> RiskLimits:
    return RiskLimits(
        max_daily_loss=s.risk_max_daily_loss, max_consecutive_losses=s.risk_max_consecutive_losses,
        max_trades_per_day=s.risk_max_trades_per_day, max_open_positions=s.risk_max_open_positions,
        max_lots_per_trade=s.risk_max_lots_per_trade, max_risk_per_trade=s.risk_max_risk_per_trade,
        max_premium_exposure=s.risk_max_premium_exposure, max_underlying_exposure=s.risk_max_underlying_exposure,
        max_data_age_seconds=s.risk_max_data_age_seconds)


class PaperService:
    def __init__(self, md: MarketDataService, sessionmaker: async_sessionmaker | None, settings: Settings,
                 *, persist_state: bool | None = None) -> None:
        """`persist_state` (PRD §22.3): save and restore positions, counters and the kill switch. On by default
        for live and simulated data; off for a replay, whose clock restarts at the open, so a saved position
        would come back from the future."""
        self.md, self.sessionmaker, self.settings = md, sessionmaker, settings
        self.persist_state = (not md.provider.has_own_clock) if persist_state is None else persist_state
        self.store = PaperStore(sessionmaker, md.now, persist_state=self.persist_state)
        self.risk = RiskEngine(risk_limits(settings))
        self.broker = PaperBroker(md.latest.get, md.now, instrument_fn=md.registry.by_token,
                                  latency=timedelta(milliseconds=settings.paper_latency_ms),
                                  max_quote_age=timedelta(seconds=settings.risk_max_data_age_seconds),
                                  capital=settings.paper_capital)
        self.runner: PaperRunner | None = None
        self.restore_report: dict | None = None
        self.carried: list[dict] = []  # restored open positions waiting for a runner to adopt them
        self._restored = False

    # ---- persistence (PRD §22.3) ---------------------------------------------------------
    async def restore(self) -> dict | None:
        """Load the saved paper state once per process: kill switch, same-day risk counters, broker cash and
        positions. Runs at API start-up, before anything can trade or trip the switch."""
        if self._restored:
            return self.restore_report
        self._restored = True
        if not self.persist_state:
            self.restore_report = {"status": "disabled", "detail": "state persistence is off for this data source"}
            return self.restore_report
        today = self.md.now().astimezone(IST).date().isoformat()
        try:
            row = await self.store.load_state(self.md.provider.name)
            self.restore_report, self.carried = restore_snapshot(
                self.risk, self.broker, row, today=today, data_source=self.md.provider.name)
        except Exception as exc:  # noqa: BLE001 - unreadable state means we cannot vouch for the kill switch
            log.exception("paper state restore failed", event="paper.restore_failed")
            reason = f"paper state could not be restored ({type(exc).__name__}: {exc})"
            self.risk.trip_kill_switch(reason, self.md.now())
            self.restore_report, self.carried = {"status": "error", "detail": reason + "; kill switch ON"}, []
        if self.restore_report["status"] != "none":
            self.store.emit(f"state.{self.restore_report['status']}", None, self.restore_report)
        await self.store.flush()
        return self.restore_report

    def snapshot(self) -> dict:
        if self.runner is not None:
            return self.runner.snapshot()
        return build_snapshot(self.risk, self.broker, self.carried, set())

    async def persist(self) -> None:
        """Save now. For changes made outside the runner's heartbeat (kill switch, strategy toggles)."""
        if not self._restored:  # saving first would overwrite what has not been read yet
            raise RuntimeError("PaperService.restore() must run before the first save")
        self.store.set_state(self.md.now().astimezone(IST).date().isoformat(), self.md.provider.name,
                             self.snapshot())
        await self.store.flush()

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
        await self.restore()
        restored = self.runner.export_lives() if self.runner is not None else self.carried
        runner = PaperRunner(self.md, self.broker, self.risk, self.store, lanes or self.default_lanes(),
                             sessionmaker=self.sessionmaker, tick_interval=tick)
        runner.restore_report = self.restore_report
        await runner.start(restored=restored)  # refuses (RuntimeError) if an open position has no lane
        self.runner, self.carried = runner, []
        return runner

    async def stop(self) -> None:
        if self.runner is not None and self.runner.running:
            await self.runner.stop()
