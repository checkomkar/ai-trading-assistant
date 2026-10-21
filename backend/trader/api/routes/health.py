from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from trader.api.deps import get_md, get_user_md
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL
from trader.market_data.service import MarketDataService

router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health(request: Request, md: MarketDataService = Depends(get_md)) -> dict:
    db_ok = True
    try:
        async with request.app.state.sessionmaker() as s:
            await s.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    now = md.now()
    return {
        "status": "ok" if db_ok and md.provider.health.connected else "degraded",
        "trading_mode": md.settings.trading_mode.value,
        "provider": md.provider.name,
        "simulated": md.provider.is_simulated,
        "database": "ok" if db_ok else "error",
        "session": md.calendar.session_state(now).value,
        "server_time": now.isoformat(),
    }


@router.get("/feed")
async def feed(md: MarketDataService = Depends(get_user_md)) -> dict:
    h = md.provider.health
    now = md.now()
    ages = {}
    for sym in (*UNDERLYINGS, VIX_SYMBOL):
        inst = md.registry.index(sym)
        ages[sym] = md.quality.last_tick_age(inst.token, now) if inst else None
    return {
        "provider": md.provider.name,
        "simulated": md.provider.is_simulated,
        "connected": h.connected,
        "connected_since": h.connected_since,
        "reconnects": h.reconnects,
        "last_error": h.last_error,
        "last_tick_at": h.last_tick_at,
        "last_heartbeat_at": h.last_heartbeat_at,
        "ticks_received": h.ticks_received,
        "ticks_per_second": md.ticks_per_second(),
        "subscribed": h.subscribed,
        "last_tick_age_seconds": ages,
        "session": md.calendar.session_state(now).value,
        "writes": md.writes,
    }


@router.get("/data")
async def data(md: MarketDataService = Depends(get_user_md)) -> dict:
    snap = md.quality.snapshot()
    snap["stale_symbols"] = [
        (md.registry.by_token(t).symbol if md.registry.by_token(t) else t) for t in snap["stale_tokens"]
    ]
    return snap
