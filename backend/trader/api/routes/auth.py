"""Sign-up, login, logout and "who am I". The session is an HttpOnly cookie holding a random token (only its hash is stored)."""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from trader.accounts import repo as accounts
from trader.api.deps import COOKIE, CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])

# Failed logins per (ip, email) in a sliding window. In-process on purpose: a multi-process deployment moves this to Redis.
_FAILS: dict[str, list[float]] = {}
WINDOW, MAX_FAILS = 900.0, 8


def _throttled(key: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _FAILS.get(key, []) if now - t < WINDOW]
    _FAILS[key] = hits
    return len(hits) >= MAX_FAILS


class SignupIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)
    name: str = Field("", max_length=80)
    invite_code: str = Field("", max_length=64)


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=256)


def _me(p: accounts.Principal, auth_enabled: bool) -> dict:
    return {"id": p.id, "email": p.email, "name": p.name, "is_admin": p.is_admin, "plan": p.plan, "auth_enabled": auth_enabled}


def _set_cookie(request: Request, response: Response, token: str) -> None:
    st = request.app.state.settings
    response.set_cookie(COOKIE, token, max_age=st.auth_session_days * 86400, httponly=True, samesite="lax",
                        secure=st.auth_cookie_secure, path="/")


@router.get("/me")
async def me(request: Request) -> dict:
    """Never 401s: the page uses it to decide between the app and the login screen."""
    st = request.app.state.settings
    if not st.auth_enabled:
        return {**_me(accounts.local_principal(), False), "signed_in": True, "signup_open": False}
    from trader.api.deps import _principal

    p = await _principal(request.app.state.sessionmaker, request.cookies.get(COOKIE))
    return {**(_me(p, True) if p else {"auth_enabled": True}), "signed_in": p is not None,
            "signup_open": st.signup_open, "invite_required": bool(st.signup_invite_code)}


@router.post("/signup")
async def signup(request: Request, response: Response, body: SignupIn) -> dict:
    st = request.app.state.settings
    if not st.auth_enabled:
        raise HTTPException(404, "Accounts are not enabled on this server.")
    if not st.signup_open:
        raise HTTPException(403, "Sign-up is closed.")
    if st.signup_invite_code and not hmac.compare_digest(body.invite_code.encode(), st.signup_invite_code.encode()):
        raise HTTPException(403, "That invite code is not valid.")
    try:
        async with request.app.state.sessionmaker() as s, s.begin():
            row = await accounts.create_account(s, body.email, body.password, body.name, st.admin_email_set)
            token = await accounts.create_session(s, row.id, st.auth_session_days)
            p = accounts.principal_from_row(row)
    except accounts.AccountError as exc:
        raise HTTPException(422, str(exc)) from None
    _set_cookie(request, response, token)
    return _me(p, True)


@router.post("/login")
async def login(request: Request, response: Response, body: LoginIn) -> dict:
    st = request.app.state.settings
    if not st.auth_enabled:
        raise HTTPException(404, "Accounts are not enabled on this server.")
    key = f"{request.client.host if request.client else '?'}|{body.email.strip().lower()}"
    if _throttled(key):
        raise HTTPException(429, "Too many failed attempts. Try again in a few minutes.")
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await accounts.authenticate(s, body.email, body.password)
        if row is None:
            _FAILS.setdefault(key, []).append(time.monotonic())
            raise HTTPException(401, "Wrong email or password.")
        token = await accounts.create_session(s, row.id, st.auth_session_days)
        p = accounts.principal_from_row(row)
    _FAILS.pop(key, None)
    _set_cookie(request, response, token)
    return _me(p, True)


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        async with request.app.state.sessionmaker() as s, s.begin():
            await accounts.end_session(s, token)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.get("/session")
async def session_info(user: CurrentUser, request: Request) -> dict:
    return _me(user, request.app.state.settings.auth_enabled)
