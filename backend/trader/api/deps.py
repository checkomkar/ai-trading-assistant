"""Request dependencies: who is calling, and the meter. The account always comes from the session cookie, never from a
request field (PRD §23.3). With AUTH_ENABLED=false everyone is the unlimited local owner."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, WebSocket

from trader.accounts import repo as accounts
from trader.accounts.repo import Principal
from trader.billing.meter import Meter
from trader.instruments.specs import UNDERLYINGS
from trader.market_data.feeds import FeedUnavailable
from trader.market_data.service import MarketDataService

COOKIE = "striktek_session"


def get_md(request: Request) -> MarketDataService:
    return request.app.state.md


def normalize_underlying(underlying: str) -> str:
    u = underlying.upper()
    if u not in UNDERLYINGS:
        raise HTTPException(404, f"Unknown underlying '{underlying}'. Supported: {', '.join(UNDERLYINGS)}")
    return u


async def _principal(sessionmaker, token: str | None) -> Principal | None:
    if not token:
        return None
    async with sessionmaker() as s:
        return await accounts.principal_for_token(s, token)


async def current_principal(request: Request) -> Principal:
    if not request.app.state.settings.auth_enabled:
        return accounts.local_principal()
    p = await _principal(request.app.state.sessionmaker, request.cookies.get(COOKIE))
    if p is None:
        raise HTTPException(401, "Sign in to continue.")
    return p


async def require_admin(p: Annotated[Principal, Depends(current_principal)]) -> Principal:
    if not p.is_admin:
        raise HTTPException(403, "This is only available to the operator.")
    return p


def get_meter(request: Request) -> Meter:
    return request.app.state.meter


CurrentUser = Annotated[Principal, Depends(current_principal)]
AdminUser = Annotated[Principal, Depends(require_admin)]
MeterDep = Annotated[Meter, Depends(get_meter)]


async def ws_allowed(ws: WebSocket) -> bool:
    """Call before `accept()`-ing a socket. Closes it (policy violation) when a login is required and missing."""
    if not ws.app.state.settings.auth_enabled:
        return True
    if await _principal(ws.app.state.sessionmaker, ws.cookies.get(COOKIE)) is None:
        await ws.close(code=4401)
        return False
    return True


async def resolve_md(app, user: Principal) -> MarketDataService:
    """The market data this caller sees. Local mode shares the one feed. When accounts are on, every customer gets a feed
    built from their OWN broker sign-in; the operator may fall back to the site's shared feed if they have not connected one.
    Raises FeedUnavailable (the routes turn it into a 409 the UI can act on)."""
    if not app.state.settings.auth_enabled:
        return app.state.md
    try:
        return await app.state.feeds.md_for(user.id)
    except FeedUnavailable as exc:
        if user.is_admin and exc.code == "broker_required":
            return app.state.md
        raise


async def get_user_md(request: Request, user: CurrentUser) -> MarketDataService:
    try:
        return await resolve_md(request.app, user)
    except FeedUnavailable as exc:
        raise HTTPException(409, detail={"code": exc.code, "message": exc.message}) from None


UserMd = Annotated[MarketDataService, Depends(get_user_md)]


async def ws_principal(ws: WebSocket) -> Principal | None:
    if not ws.app.state.settings.auth_enabled:
        return accounts.local_principal()
    return await _principal(ws.app.state.sessionmaker, ws.cookies.get(COOKIE))
