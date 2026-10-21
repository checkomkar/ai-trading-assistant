"""Verify a Google sign-in. The browser gets an ID token from Google; we ask Google to validate it and then check
that it was issued for OUR client id and that the email is verified. Without the audience check any app's token would do."""

from __future__ import annotations

import httpx

TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class GoogleError(ValueError):
    """The credential is not acceptable; the message is safe to show."""


async def verify_id_token(credential: str, client_id: str) -> dict:
    """-> {"sub", "email", "name"} or raises GoogleError."""
    if not client_id:
        raise GoogleError("Google sign-in is not set up on this server.")
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(TOKENINFO, params={"id_token": credential})
    except httpx.HTTPError as exc:
        raise GoogleError("Could not reach Google. Try again.") from exc
    if r.status_code != 200:
        raise GoogleError("Google did not accept that sign-in.")
    info = r.json()
    if info.get("aud") != client_id or info.get("iss") not in ISSUERS:
        raise GoogleError("That Google sign-in was not issued for this app.")
    if str(info.get("email_verified")).lower() != "true" or not info.get("email") or not info.get("sub"):
        raise GoogleError("Your Google email address is not verified.")
    return {"sub": info["sub"], "email": info["email"], "name": info.get("name", "")}
