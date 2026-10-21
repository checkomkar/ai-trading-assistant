"""FastAPI application. Run: `uvicorn trader.api.main:app --reload` from `backend/`."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from trader.api.deps import current_principal, require_admin
from trader.api.routes import auth, backtests, billing, brokers, chat, health, market, options, paper, speech, strategies, ws
from trader.accounts.sms import build_sender
from trader.billing.meter import Meter, QuotaExceeded
from trader.config import Settings, get_settings
from trader.db.migrate import upgrade_db
from trader.execution.service import PaperService
from trader.db.session import create_engine, create_sessionmaker
from trader.logging import configure_logging, get_logger
from trader.market_data.factory import create_provider
from trader.market_data.feeds import FeedManager
from trader.market_data.service import MarketDataService

log = get_logger("api")


def create_app(settings: Settings | None = None, *, start_collector: bool = True) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Schema is always Alembic-managed (SQLite and PostgreSQL alike).
        await asyncio.to_thread(upgrade_db, settings.resolved_database_url)
        engine = create_engine(settings.resolved_database_url)
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)
        app.state.meter = Meter(app.state.sessionmaker)
        app.state.sms = build_sender(settings)
        app.state.ocr_lock = asyncio.Lock()  # one OCR job at a time
        app.state.ocr_active = set()
        app.state.ocr_tasks = set()
        app.state.feeds = FeedManager(settings, app.state.sessionmaker)
        provider = create_provider(settings, app.state.sessionmaker)
        # A replay has its own clock and must never write its synthetic ticks into the real tables.
        md = MarketDataService(provider, app.state.sessionmaker if provider.persist_market_data else None,
                               settings, now_fn=provider.clock_now if provider.has_own_clock else None)
        app.state.md = md
        app.state.paper = PaperService(md, app.state.sessionmaker, settings)
        await app.state.paper.restore()  # kill switch, open positions and counters survive a restart
        if start_collector:
            await md.start()
            if settings.paper_autostart:
                try:
                    await app.state.paper.start()
                except Exception as exc:  # noqa: BLE001 - the dashboard stays up; the error shows in /paper/status
                    log.error("paper runner failed to start", event="paper.start_failed", error=str(exc))
        log.info("api started", event="api.started", mode=settings.trading_mode.value,
                 provider=settings.market_data_provider)
        try:
            yield
        finally:
            await app.state.feeds.stop_all()
            if start_collector:
                await app.state.paper.stop()
                await app.state.paper.store.flush()
                await md.stop()
            await engine.dispose()

    app = FastAPI(title="STRIKTEK", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings

    @app.exception_handler(QuotaExceeded)
    async def quota_exceeded(_: Request, exc: QuotaExceeded) -> JSONResponse:
        return JSONResponse(status_code=402, content={"detail": exc.body()})

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Public: health, sign-in, plans and the payment webhook. Market data needs a login; the paper runner is the
    # operator's single shared runner until per-account runners exist (PRD §23.4 S1), so it is admin-only.
    # Strategies, backtests, chat and speech resolve the caller themselves because their data is per account.
    for r in (health.router, auth.router, billing.router, strategies.router, backtests.router, brokers.router, chat.router, speech.router, ws.router):
        app.include_router(r)
    for r in (market.router, options.router):
        app.include_router(r, dependencies=[Depends(current_principal)])
    app.include_router(paper.router, dependencies=[Depends(require_admin)])

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {"name": app.title, "version": app.version, "docs": "/docs", "health": "/health"}

    return app


app = create_app()
