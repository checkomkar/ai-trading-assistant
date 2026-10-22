"""Bring-your-own broker keys: the catalogue, and a customer's saved connections (secrets are write-only)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from trader.accounts import vault
from trader.api.deps import CurrentUser
from trader.brokers.catalog import CATALOG
from trader.brokers.fyers import auth as fyers_auth
from trader.brokers.fyers.check import fetch_profile
from trader.brokers.shoonya import auth as shoonya_auth
from trader.db.models import BrokerConnectionRow
from trader.logging import get_logger

log = get_logger("brokers")

router = APIRouter(prefix="/brokers", tags=["brokers"])


class ConnectIn(BaseModel):
    broker: str
    values: dict[str, str] = Field(default_factory=dict)


def _session(r: BrokerConnectionRow, key: str) -> dict | None:
    """For brokers with a daily sign-in: is today's token present? (the token itself is never returned)"""
    spec = CATALOG.get(r.broker)
    if spec is None or not spec.daily_login:
        return None
    try:
        blob = vault.unseal(r.secret_blob, key)
        tok = blob.get("access_token")
    except Exception:  # noqa: BLE001 - a key that no longer decrypts reads as "not signed in"
        blob, tok = {}, None
    if spec.login_kind == "password":  # Shoonya: the session token is opaque, its expiry was stored with it
        try:
            exp = datetime.fromisoformat(blob["expires_at"]) if tok else None
        except (KeyError, ValueError):
            exp = None
    else:
        exp = fyers_auth.jwt_expiry(tok) if tok else None
    live = bool(tok) and (exp is None or exp > datetime.now(timezone.utc))
    return {"state": "signed_in" if live else ("expired" if tok else "none"), "expires_at": exp.isoformat() if exp else None}


def _view(r: BrokerConnectionRow, key: str = "") -> dict:
    spec = CATALOG.get(r.broker)
    return {"session": _session(r, key) if key else None, "id": r.id, "broker": r.broker, "name": spec.name if spec else r.broker, "public": r.public,
            "status": spec.status if spec else "unknown", "created_at": r.created_at.isoformat(),
            "secrets_saved": [f.key for f in spec.fields if f.secret] if spec else []}


@router.get("/catalog")
async def catalog(request: Request, user: CurrentUser) -> dict:
    return {"vault_ready": bool(request.app.state.settings.credential_key), "fyers_redirect_uri": _redirect_uri(request),
            "brokers": [{"id": b.id, "name": b.name, "status": b.status, "data_note": b.data_note, "docs_url": b.docs_url,
                         "daily_login": b.daily_login, "login_kind": b.login_kind,
                         "fields": [{"key": f.key, "label": f.label, "secret": f.secret} for f in b.fields]}
                        for b in CATALOG.values()]}


@router.get("/connections")
async def connections(request: Request, user: CurrentUser) -> list[dict]:
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == user.id)
                                .order_by(BrokerConnectionRow.created_at))).scalars().all()
    return [_view(r, request.app.state.settings.credential_key) for r in rows]


@router.post("/connections")
async def connect(request: Request, user: CurrentUser, body: ConnectIn) -> dict:
    """Save (or replace) the keys for one broker. Nothing is sent to the broker here; `status` says whether an adapter
    exists yet that will use them."""
    spec = CATALOG.get(body.broker)
    if spec is None:
        raise HTTPException(404, "Unknown broker.")
    values = {f.key: body.values.get(f.key, "").strip() for f in spec.fields}
    missing = [f.label for f in spec.fields if not values[f.key]]
    if missing:
        raise HTTPException(422, "Fill in: " + ", ".join(missing))
    if any(len(v) > 512 for v in values.values()):
        raise HTTPException(422, "A value is too long.")
    try:
        blob = vault.seal(values, request.app.state.settings.credential_key)
    except vault.VaultUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    public = {f.key: values[f.key] for f in spec.fields if not f.secret}
    async with request.app.state.sessionmaker() as s, s.begin():
        row = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == user.id,
                                                                  BrokerConnectionRow.broker == spec.id))).scalars().first()
        if row is None:
            row = BrokerConnectionRow(id=uuid.uuid4().hex[:12], owner=user.id, broker=spec.id, public=public, secret_blob=blob,
                                      created_at=datetime.now(timezone.utc))
            s.add(row)
        else:
            row.public, row.secret_blob = public, blob
        await s.flush()
        out = _view(row, request.app.state.settings.credential_key)
    await request.app.state.feeds.drop(user.id)  # the old keys' feed must not outlive them
    return out


@router.delete("/connections/{conn_id}")
async def disconnect(request: Request, user: CurrentUser, conn_id: str) -> dict:
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await s.get(BrokerConnectionRow, conn_id)
        if row is None or row.owner != user.id:
            raise HTTPException(404, "Connection not found.")
        await s.delete(row)
    await request.app.state.feeds.drop(user.id)
    return {"deleted": conn_id}


# ---- Shoonya: password + 2FA code typed here, used once for the sign-in and never stored ------------------------------
class ShoonyaLoginIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    totp: str = Field(min_length=4, max_length=12)


LOGIN_WINDOW_S, LOGIN_MAX = 600, 5  # Shoonya can lock an account after repeated bad attempts: stop at five


def _login_budget(request: Request, owner: str) -> None:
    attempts = getattr(request.app.state, "shoonya_attempts", None)
    if attempts is None:
        attempts = request.app.state.shoonya_attempts = {}
    now = time.time()
    recent = [t for t in attempts.get(owner, []) if now - t < LOGIN_WINDOW_S]
    if len(recent) >= LOGIN_MAX:
        raise HTTPException(429, "Too many sign-in attempts. Wait ten minutes before trying again, so Shoonya does not lock your account.")
    attempts[owner] = [*recent, now]


async def _shoonya_row(request: Request, user, s) -> BrokerConnectionRow:
    row = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == user.id,
                                                              BrokerConnectionRow.broker == "shoonya"))).scalars().first()
    if row is None:
        raise HTTPException(404, "Save your Shoonya keys first.")
    return row


@router.post("/shoonya/login")
async def shoonya_login(request: Request, user: CurrentUser, body: ShoonyaLoginIn) -> dict:
    """Sign in to the customer's Shoonya account for today. The password and code go to Shoonya in this one request and
    are discarded; only the session token is kept (encrypted) so the live feed can run."""
    _login_budget(request, user.id)
    async with request.app.state.sessionmaker() as s:
        row = await _shoonya_row(request, user, s)
    keys = _unseal(request, row)
    try:
        sess = await asyncio.to_thread(shoonya_auth.sign_in, keys["user_id"], body.password, body.totp, keys["vendor_code"],
                                       keys["api_key"], keys.get("imei") or "striktek")
    except shoonya_auth.ShoonyaAuthError as exc:
        raise HTTPException(400, str(exc)) from None
    except ConnectionError as exc:
        raise HTTPException(502, "Shoonya could not be reached. Try again in a minute.") from exc
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await s.get(BrokerConnectionRow, row.id)
        keys = {**_unseal(request, row), "access_token": sess["token"], "expires_at": sess["expires_at"]}
        row.secret_blob = vault.seal(keys, request.app.state.settings.credential_key)
    request.app.state.shoonya_attempts.pop(user.id, None)  # a good sign-in clears the counter
    _refresh_feed(request, user, sess["token"], datetime.fromisoformat(sess["expires_at"]))
    return {"ok": True, "name": sess["name"], "user_id": sess["user_id"], "expires_at": sess["expires_at"]}


@router.post("/shoonya/test")
async def shoonya_test(request: Request, user: CurrentUser) -> dict:
    """Read-only: one NIFTY quote through the saved session. Places no orders."""
    async with request.app.state.sessionmaker() as s:
        row = await _shoonya_row(request, user, s)
    keys = _unseal(request, row)
    token = keys.get("access_token")
    if not token:
        return {"ok": False, "needs_login": True, "message": "Not signed in to Shoonya today. Use \"Sign in to Shoonya\"."}
    try:
        q = await asyncio.to_thread(shoonya_auth.quote, keys["user_id"], token, "NSE", "26000")
    except shoonya_auth.ShoonyaAuthError as exc:
        return {"ok": False, "needs_login": True, "message": str(exc)}
    except ConnectionError as exc:
        return {"ok": False, "message": str(exc)}
    return {"ok": True, "nifty": shoonya_auth.num(q.get("lp")), "name": keys["user_id"], "fy_id": keys["user_id"]}


# ---- Fyers: the customer signs in to THEIR Fyers app; we keep the day's token (encrypted) and can test it -----------------
STATE_TTL = 600


def _redirect_uri(request: Request) -> str:
    """The address Fyers sends the browser back to. The customer registers exactly this in their own Fyers app."""
    st = request.app.state.settings
    base = st.public_url.rstrip("/") if st.public_url else str(request.base_url).rstrip("/")
    return f"{base}/api/brokers/fyers/callback"


def _sign(request: Request, user_id: str, conn_id: str, exp: int) -> str:
    key = request.app.state.settings.credential_key.encode()
    return hmac.new(key, f"{user_id}|{conn_id}|{exp}".encode(), hashlib.sha256).hexdigest()[:32]


async def _fyers_row(request: Request, user, s) -> BrokerConnectionRow:
    row = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == user.id,
                                                              BrokerConnectionRow.broker == "fyers"))).scalars().first()
    if row is None:
        raise HTTPException(404, "Save your Fyers keys first.")
    return row


def _unseal(request: Request, row: BrokerConnectionRow) -> dict:
    try:
        return vault.unseal(row.secret_blob, request.app.state.settings.credential_key)
    except vault.VaultUnavailable as exc:
        raise HTTPException(503, str(exc)) from None


@router.post("/fyers/login-url")
async def fyers_login_url(request: Request, user: CurrentUser) -> dict:
    """The Fyers sign-in address for this customer's app. They sign in on Fyers' own site; we never see the password."""
    async with request.app.state.sessionmaker() as s:
        row = await _fyers_row(request, user, s)
    keys = _unseal(request, row)
    exp = int(time.time()) + STATE_TTL
    state = f"{row.id}.{exp}.{_sign(request, user.id, row.id, exp)}"
    ns = SimpleNamespace(fyers_client_id=keys["client_id"], fyers_secret_key=keys["secret_key"],
                         fyers_redirect_uri=_redirect_uri(request))
    url, _ = fyers_auth.login_url(ns, state=state)
    return {"url": url, "redirect_uri": ns.fyers_redirect_uri}


def _page(msg: str, ok: bool) -> HTMLResponse:
    colour = "#22c55e" if ok else "#f43f5e"
    return HTMLResponse(f"<html><body style='font-family:sans-serif;background:#0b0f14;color:#e5e7eb;padding:2rem'>"
                        f"<h3 style='color:{colour}'>{msg}</h3><p><a style='color:#f5b301' href='/#/brokers'>Back to Brokers</a></p></body></html>",
                        status_code=200 if ok else 400)


@router.get("/fyers/callback")
async def fyers_callback(request: Request, user: CurrentUser, auth_code: str = "", state: str = "", s: str = ""):
    """Fyers sends the browser here after sign-in. The state proves this request belongs to this logged-in customer."""
    if s and s != "ok":
        return _page("Fyers did not complete the sign-in. Try again.", False)
    try:
        conn_id, exp, sig = state.split(".")
        valid = int(exp) > time.time() and hmac.compare_digest(sig, _sign(request, user.id, conn_id, int(exp)))
    except ValueError:
        valid = False
    if not valid or not auth_code:
        return _page("This sign-in link is invalid or has expired. Start again from the Brokers page.", False)
    async with request.app.state.sessionmaker() as sess, sess.begin():
        row = await sess.get(BrokerConnectionRow, conn_id)
        if row is None or row.owner != user.id or row.broker != "fyers":
            return _page("That connection no longer exists.", False)
        keys = _unseal(request, row)
        ns = SimpleNamespace(fyers_client_id=keys["client_id"], fyers_secret_key=keys["secret_key"],
                             fyers_redirect_uri=_redirect_uri(request))
        try:
            token = await asyncio.to_thread(exchange_auth_code, ns, auth_code)
        except fyers_auth.FyersAuthError as exc:
            return _page(f"Fyers refused the sign-in: {exc}", False)
        keys["access_token"] = token
        row.secret_blob = vault.seal(keys, request.app.state.settings.credential_key)
    _refresh_feed(request, user, token)
    return RedirectResponse("/#/brokers", status_code=303)


def _refresh_feed(request: Request, user, token: str, expires_at: datetime | None = None) -> None:
    """A new sign-in: reconnect this customer's running feed with the new token (a feed that is not running just starts
    on their next visit)."""
    async def go() -> None:
        try:
            await request.app.state.feeds.refresh(user.id, token, expires_at)
        except Exception as exc:  # noqa: BLE001 - the sign-in itself succeeded; the feed status shows any problem
            log.warning("feed refresh failed", event="feed.refresh_failed", owner=user.id, error=str(exc))

    request.app.state.feed_tasks = getattr(request.app.state, "feed_tasks", set())
    t = asyncio.create_task(go())
    request.app.state.feed_tasks.add(t)
    t.add_done_callback(request.app.state.feed_tasks.discard)


exchange_auth_code = fyers_auth.exchange_auth_code  # module-level so tests can replace it


@router.post("/fyers/test")
async def fyers_test(request: Request, user: CurrentUser) -> dict:
    """Read-only: ask Fyers who is signed in and for one NIFTY quote. Places no orders."""
    async with request.app.state.sessionmaker() as s:
        row = await _fyers_row(request, user, s)
    keys = _unseal(request, row)
    token = keys.get("access_token")
    exp = fyers_auth.jwt_expiry(token) if token else None
    if not token or (exp is not None and exp <= datetime.now(timezone.utc)):
        return {"ok": False, "needs_login": True, "message": "Not signed in to Fyers today. Use \"Sign in to Fyers\"."}
    try:
        info = await fetch_profile(keys["client_id"], token)
    except fyers_auth.FyersAuthError as exc:
        return {"ok": False, "needs_login": True, "message": str(exc)}
    return {"ok": True, **info}
