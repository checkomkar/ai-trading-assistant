"""Interactive daily Fyers login (PRD §17.2).

    python -m trader.brokers.fyers.login

Opens the Fyers login page in your browser. You sign in yourself (password / TOTP / PIN are
entered on Fyers' site, never here). When Fyers redirects back to FYERS_REDIRECT_URI, a one-shot
local listener captures the auth code, exchanges it for an access token, and caches it in
backend/data/fyers_token.json. If the redirect URI is not local, paste the redirected URL instead.
"""

from __future__ import annotations

import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from trader.brokers.fyers.auth import (
    FyersAuthError,
    exchange_auth_code,
    jwt_expiry,
    load_token,
    login_url,
    parse_callback,
    require_app_config,
    save_token,
    token_path,
)
from trader.config import get_settings
from trader.instruments.calendar import IST

LOCAL_HOSTS = {"127.0.0.1", "localhost"}
WAIT_SECONDS = 300


def _capture_redirect(host: str, port: int) -> str | None:
    """Serve exactly one request on host:port and return its full path (with query)."""
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            captured["path"] = self.path
            ok = "auth_code=" in self.path
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "Fyers login captured. You can close this tab." if ok else "No auth_code in redirect."
            self.wfile.write(f"<html><body style='font-family:sans-serif'><h3>{msg}</h3></body></html>".encode())

        def log_message(self, *args) -> None:  # silence default stderr logging
            pass

    server = HTTPServer((host, port), Handler)
    server.timeout = WAIT_SECONDS
    t = threading.Thread(target=server.handle_request, daemon=True)
    t.start()
    t.join(WAIT_SECONDS + 1)
    server.server_close()
    return captured.get("path")


def main() -> int:
    settings = get_settings()
    try:
        require_app_config(settings)
    except FyersAuthError as exc:
        print(exc)
        return 2

    path = token_path(settings)
    if "--force" not in sys.argv and load_token(path, settings.fyers_client_id):
        print(f"A valid token is already cached at {path}. Use --force to log in again.")
        return 0

    url, state = login_url(settings)
    redirect = urlparse(settings.fyers_redirect_uri)
    local = redirect.hostname in LOCAL_HOSTS and redirect.port is not None

    print("Opening the Fyers login page. Sign in there (credentials are never entered here):\n")
    print(f"  {url}\n")
    webbrowser.open(url)

    if local:
        print(f"Waiting up to {WAIT_SECONDS // 60} min for the redirect on {redirect.hostname}:{redirect.port} ...")
        raw = _capture_redirect(redirect.hostname, redirect.port)
    else:
        raw = input("After signing in, paste the full URL you were redirected to:\n> ").strip()
    if not raw:
        print("No redirect received.")
        return 1

    params = parse_callback(raw)
    if params.get("state") != state:
        print("State mismatch — refusing to use this auth code (possible stale or forged redirect).")
        return 1
    if "auth_code" not in params:
        print(f"Login failed: {params}")
        return 1

    try:
        token = exchange_auth_code(settings, params["auth_code"])
    except FyersAuthError as exc:
        print(exc)
        return 1
    save_token(path, settings.fyers_client_id, token)
    exp = jwt_expiry(token)
    print(f"Token saved to {path}")
    if exp:
        print(f"Valid until {exp.astimezone(IST):%Y-%m-%d %H:%M} IST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
