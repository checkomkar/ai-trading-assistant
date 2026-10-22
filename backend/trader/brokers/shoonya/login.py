"""Daily Shoonya sign-in for local use (the operator's own account).

    python -m trader.brokers.shoonya.login

Reads SHOONYA_USER_ID, SHOONYA_PASSWORD, SHOONYA_VENDOR_CODE, SHOONYA_API_KEY and SHOONYA_IMEI from .env. The 2FA code is
worked out from SHOONYA_TOTP_SECRET when that is set, otherwise you are asked to type the current 6-digit code. Only the
session token is written (backend/data/shoonya_token.json); the password is used for this one request and never saved.
Then set MARKET_DATA_PROVIDER=shoonya to run the local app on Shoonya's live data.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import struct
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from trader.brokers.shoonya import auth
from trader.config import Settings, get_settings


def totp_now(secret: str, at: float | None = None, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 time-based code from a base32 secret (what an authenticator app computes)."""
    key = base64.b32decode(secret.replace(" ", "").upper() + "=" * (-len(secret.replace(" ", "")) % 8))
    counter = int((time.time() if at is None else at) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % 10 ** digits
    return str(code).zfill(digits)


def token_path(settings: Settings) -> Path:
    return settings.data_dir / "shoonya_token.json"


def save_token(path: Path, user_id: str, token: str, expires_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"user_id": user_id, "access_token": token, "expires_at": expires_at}), encoding="utf-8")


def load_token(path: Path, user_id: str, now: datetime | None = None) -> str | None:
    """A usable session token for this user, or None if missing, for another user, or past its expiry."""
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
        exp = datetime.fromisoformat(rec["expires_at"])
    except (OSError, ValueError, KeyError):
        return None
    if rec.get("user_id") != user_id or not rec.get("access_token") or exp <= (now or datetime.now(timezone.utc)):
        return None
    return rec["access_token"]


def main() -> None:
    s = get_settings()
    missing = [k for k, v in {"SHOONYA_USER_ID": s.shoonya_user_id, "SHOONYA_PASSWORD": s.shoonya_password,
                              "SHOONYA_VENDOR_CODE": s.shoonya_vendor_code, "SHOONYA_API_KEY": s.shoonya_api_key}.items() if not v]
    if missing:
        raise SystemExit("Missing in .env: " + ", ".join(missing))
    code = totp_now(s.shoonya_totp_secret) if s.shoonya_totp_secret else input("Current 6-digit code: ").strip()
    try:
        sess = auth.sign_in(s.shoonya_user_id, s.shoonya_password, code, s.shoonya_vendor_code, s.shoonya_api_key,
                            s.shoonya_imei or "striktek")
    except auth.ShoonyaAuthError as exc:
        raise SystemExit(f"Sign-in failed: {exc}") from None
    except ConnectionError as exc:
        raise SystemExit(f"Could not reach Shoonya: {exc}") from None
    save_token(token_path(s), s.shoonya_user_id, sess["token"], sess["expires_at"])
    print(f"Signed in as {sess['name'] or s.shoonya_user_id}; session valid until {sess['expires_at']}.")
    try:
        q = auth.quote(s.shoonya_user_id, sess["token"], "NSE", "26000")
        print(f"NIFTY 50 last price: {q.get('lp')}")
    except Exception as exc:  # noqa: BLE001 - the sign-in worked; the quote is only a sanity check
        print(f"(Quote check failed: {exc})", file=sys.stderr)


if __name__ == "__main__":
    main()
