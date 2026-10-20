"""FastAPI application. Run: `uvicorn trader.api.main:app --reload` from `backend/`."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from trader.api.routes import backtests, health, market, options, strategies, ws
from trader.config import Settings, get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.logging import configure_logging, get_logger
from trader.market_data.factory import create_provider
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
        md = MarketDataService(create_provider(settings), app.state.sessionmaker, settings)
        app.state.md = md
        if start_collector:
            await md.start()
        log.info("api started", event="api.started", mode=settings.trading_mode.value,
                 provider=settings.market_data_provider)
        try:
            yield
        finally:
            if start_collector:
                await md.stop()
            await engine.dispose()

    app = FastAPI(title="NIFTY AI Trader", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for r in (health.router, market.router, options.router, backtests.router, strategies.router, ws.router):
        app.include_router(r)

    @app.get("/", include_in_schema=False)
    async def root() -> dict:
        return {"name": app.title, "version": app.version, "docs": "/docs", "health": "/health"}

    return app


app = create_app()
