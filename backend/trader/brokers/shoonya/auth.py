"""Shoonya (Finvasia, NorenAPI) sign-in and REST plumbing.

The protocol follows Shoonya's own open-source client (ShoonyaApi-py): a sign-in posts the user id, the SHA-256 of the
password, the current 2FA code, the vendor code and the SHA-256 of "<user id>|<API key>"; the reply carries a session
token (`susertoken`) that authorises every later call and the websocket. The password and the 2FA code are used for that
one request and are never stored or logged: only the session token is kept (encrypted by the vault).
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta, timezone
from typing import Any, Callable

from trader.instruments.calendar import IST
from trader.market_data.base import ProviderAuthError

HOST = "https://api.shoonya.com/NorenWClientTP"
WS_URL = "wss://api.shoonya.com/NorenWSTP/"
MASTER_URL = "https://api.shoonya.com/{exch}_symbols.txt.zip"
TIMEOUT_S = 25


class ShoonyaAuthError(ProviderAuthError):
    """Wrong password / 2FA code / keys, API access not enabled, or an expired session."""


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def login_payload(user_id: str, password: str, totp: str, vendor_code: str, api_key: str, imei: str) -> dict:
    return {"source": "API", "apkversion": "1.0.0", "uid": user_id, "pwd": sha256(password), "factor2": totp,
            "vc": vendor_code, "appkey": sha256(f"{user_id}|{api_key}"), "imei": imei}


def encode(payload: dict, token: str | None = None) -> bytes:
    body = "jData=" + json.dumps(payload, separators=(",", ":"))
    if token:
        body += "&jKey=" + token
    return body.encode("utf-8")


def post(path: str, payload: dict, token: str | None = None) -> Any:
    """One REST call. Returns the decoded JSON (a dict, or a list for time series). Network problems raise
    ConnectionError so the caller's retry logic treats them as a dropped feed, not as bad credentials."""
    req = urllib.request.Request(HOST + path, data=encode(payload, token), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ConnectionError(f"Shoonya answered HTTP {exc.code} for {path}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ConnectionError(f"Shoonya request failed for {path}: {exc}") from None


def check(reply: Any, what: str) -> Any:
    """Shoonya reports failure inside a 200 reply: {"stat": "Not_Ok", "emsg": "..."}. An invalid or expired session is an
    auth problem (the customer must sign in again); anything else is a failed request."""
    if isinstance(reply, dict) and reply.get("stat") == "Not_Ok":
        msg = str(reply.get("emsg") or "rejected")
        low = msg.lower()
        if "session" in low or "not logged" in low or ("invalid" in low and "key" in low):
            raise ShoonyaAuthError(f"Your Shoonya sign-in has expired ({msg}). Sign in again on the Brokers page.")
        raise ConnectionError(f"Shoonya refused {what}: {msg}")
    return reply


def session_expiry(now: datetime | None = None) -> datetime:
    """Shoonya sessions do not outlive the night: treat a sign-in as valid until the next 06:00 IST."""
    now = (now or datetime.now(timezone.utc)).astimezone(IST)
    nxt = datetime.combine(now.date(), time(6, 0), tzinfo=IST)
    if nxt <= now:
        nxt += timedelta(days=1)
    return nxt.astimezone(timezone.utc)


def sign_in(user_id: str, password: str, totp: str, vendor_code: str, api_key: str, imei: str,
            http: Callable[..., Any] = post) -> dict:
    """Exchange password + current 2FA code for a session. Returns {"token", "user_id", "name", "expires_at"}."""
    reply = http("/QuickAuth", login_payload(user_id.strip(), password, totp.strip(), vendor_code.strip(), api_key.strip(), imei.strip()))
    if not isinstance(reply, dict) or reply.get("stat") != "Ok" or not reply.get("susertoken"):
        msg = (reply or {}).get("emsg") if isinstance(reply, dict) else None
        hint = " Check the password and the 6-digit code (they change every 30 seconds)." if msg and "invalid" in msg.lower() else ""
        raise ShoonyaAuthError(f"Shoonya refused the sign-in: {msg or 'no reply'}.{hint}")
    return {"token": reply["susertoken"], "user_id": reply.get("uid") or user_id, "name": reply.get("uname") or "",
            "expires_at": session_expiry().isoformat()}


def quote(user_id: str, token: str, exch: str, tk: str, http: Callable[..., Any] = post) -> dict:
    reply = check(http("/GetQuotes", {"uid": user_id, "exch": exch, "token": tk}, token), "the quote")
    return reply if isinstance(reply, dict) else {}


def num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None
