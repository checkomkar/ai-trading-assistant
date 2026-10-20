"""Fyers access-token handling (PRD §17.2).

Tokens come from the interactive OAuth login (`python -m trader.brokers.fyers.login`) and are
cached in `backend/data/fyers_token.json` (git-ignored). A Fyers token is a JWT that expires at
06:30 IST the next day; we read its `exp` claim and refuse to use it once expired.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trader.config import Settings
from trader.market_data.base import ProviderAuthError

API_BASE = "https://api-t1.fyers.in/api/v3"
EXPIRY_MARGIN = timedelta(minutes=5)


class FyersAuthError(ProviderAuthError):
    """Missing / expired / rejected credentials. Needs a human to log in again."""


@dataclass
class TokenRecord:
    client_id: str
    access_token: str
    created_at: str
    expires_at: str | None


def token_path(settings: Settings) -> Path:
    return settings.data_dir / "fyers_token.json"


def jwt_expiry(token: str) -> datetime | None:
    """Read the `exp` claim without verifying the signature (we only need the expiry time)."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return datetime.fromtimestamp(int(exp), tz=timezone.utc) if exp else None
    except (IndexError, ValueError, json.JSONDecodeError):
        return None


def save_token(path: Path, client_id: str, access_token: str) -> TokenRecord:
    exp = jwt_expiry(access_token)
    rec = TokenRecord(client_id, access_token, datetime.now(timezone.utc).isoformat(),
                      exp.isoformat() if exp else None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec.__dict__, indent=2), encoding="utf-8")
    return rec


def load_token(path: Path, client_id: str, now: datetime | None = None) -> str | None:
    """Return a usable access token, or None if missing, for another app, or (nearly) expired."""
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if rec.get("client_id") != client_id or not rec.get("access_token"):
        return None
    exp = jwt_expiry(rec["access_token"])
    now = now or datetime.now(timezone.utc)
    if exp is not None and exp - EXPIRY_MARGIN <= now:
        return None
    return rec["access_token"]


def require_app_config(settings: Settings) -> None:
    missing = [name for name, val in (
        ("FYERS_CLIENT_ID", settings.fyers_client_id),
        ("FYERS_SECRET_KEY", settings.fyers_secret_key),
        ("FYERS_REDIRECT_URI", settings.fyers_redirect_uri),
    ) if not val]
    if missing:
        raise FyersAuthError(f"Missing in .env: {', '.join(missing)} (create an app at https://myapi.fyers.in)")


def app_id_hash(client_id: str, secret_key: str) -> str:
    return hashlib.sha256(f"{client_id}:{secret_key}".encode()).hexdigest()


def login_url(settings: Settings, state: str | None = None) -> tuple[str, str]:
    """Authorisation URL for the user's browser, and the `state` to verify on the callback."""
    state = state or secrets.token_urlsafe(16)
    params = {
        "client_id": settings.fyers_client_id,
        "redirect_uri": settings.fyers_redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{API_BASE}/generate-authcode?{urllib.parse.urlencode(params)}", state


def parse_callback(url_or_query: str) -> dict[str, str]:
    query = urllib.parse.urlparse(url_or_query).query or url_or_query.lstrip("?")
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}


def exchange_auth_code(settings: Settings, auth_code: str, post=None) -> str:  # noqa: ANN001
    """POST /validate-authcode -> access token. `post` is injectable for tests."""
    if post is None:
        import requests  # dependency of fyers-apiv3

        post = requests.post
    resp = post(f"{API_BASE}/validate-authcode", json={
        "grant_type": "authorization_code",
        "appIdHash": app_id_hash(settings.fyers_client_id, settings.fyers_secret_key),
        "code": auth_code,
    }, timeout=30)
    body = resp.json()
    if body.get("s") != "ok" or not body.get("access_token"):
        raise FyersAuthError(f"Token exchange failed: {body.get('message') or body}")
    return body["access_token"]
