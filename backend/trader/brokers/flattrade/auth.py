"""Flattrade sign-in: the customer signs in on Flattrade's own page; we only exchange the returned code for a session.

Flow (from Flattrade's own token generator): the customer opens https://auth.flattrade.in/?app_key=<API key>, signs in there
(password and 2FA are typed on Flattrade's site, never here), and Flattrade redirects the browser to the redirect URL they
registered for their API key, adding `?code=<request code>`. The code is exchanged for a session token with
sha256(api_key + code + api_secret). The token then authorises the same NorenAPI calls Shoonya uses, with the client id
Flattrade returns as the user id.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from trader.brokers.shoonya.auth import ShoonyaAuthError, session_expiry

LOGIN_URL = "https://auth.flattrade.in/"
TOKEN_URL = "https://authapi.flattrade.in/trade/apitoken"
HOST = "https://piconnect.flattrade.in/PiConnectAPI"
WS_URLS = ("wss://piconnect.flattrade.in/PiConnectWSAPI/",)
MASTER_URL = "https://flattrade.s3.ap-south-1.amazonaws.com/scripmaster/Nfo_Index_Derivatives.csv"
TIMEOUT_S = 25


class FlattradeAuthError(ShoonyaAuthError):
    """The code or the keys were refused, or the session has expired."""


def login_url(api_key: str) -> str:
    return f"{LOGIN_URL}?{urllib.parse.urlencode({'app_key': api_key})}"


def request_hash(api_key: str, code: str, api_secret: str) -> str:
    return hashlib.sha256(f"{api_key}{code}{api_secret}".encode("utf-8")).hexdigest()


def _post_json(url: str, body: dict) -> Any:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - fixed https host
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            raise ConnectionError(f"Flattrade answered HTTP {exc.code}") from None
    except (OSError, ValueError) as exc:
        raise ConnectionError(f"Flattrade request failed: {exc}") from None


def exchange_code(api_key: str, api_secret: str, code: str, http: Callable[[str, dict], Any] = _post_json) -> dict:
    """Request code -> {"token", "client_id", "expires_at"}. Raises FlattradeAuthError with Flattrade's own message."""
    reply = http(TOKEN_URL, {"api_key": api_key, "request_code": code, "api_secret": request_hash(api_key, code, api_secret)})
    if not isinstance(reply, dict) or not reply.get("token"):
        msg = (reply or {}).get("emsg") if isinstance(reply, dict) else None
        raise FlattradeAuthError(f"Flattrade refused the sign-in{': ' + msg if msg else ''}. The code can only be used once and "
                                 "for a short time: start again with \"Sign in to Flattrade\". If it keeps failing, check the "
                                 "API key and secret and the redirect URL registered for your key.")
    return {"token": reply["token"], "client_id": reply.get("client") or "", "expires_at": session_expiry().isoformat()}
