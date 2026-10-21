"""Read-only check of a Fyers session: who is signed in, and can we read a quote. Places no orders, uses no order endpoint."""

from __future__ import annotations

import httpx

from trader.brokers.fyers.auth import API_BASE, FyersAuthError

DATA_BASE = "https://api-t1.fyers.in/data"


async def fetch_profile(client_id: str, access_token: str, quote_symbol: str = "NSE:NIFTY50-INDEX") -> dict:
    """-> {"name", "fy_id", "email", "nifty": float | None}. Raises FyersAuthError if Fyers rejects the token."""
    headers = {"Authorization": f"{client_id}:{access_token}"}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{API_BASE}/profile", headers=headers)
            body = r.json()
            if r.status_code in (401, 403) or body.get("s") != "ok":
                raise FyersAuthError(str(body.get("message") or "Fyers rejected the session; sign in again."))
            d = body.get("data", {})
            nifty = None
            try:
                q = (await c.get(f"{DATA_BASE}/quotes", headers=headers, params={"symbols": quote_symbol})).json()
                nifty = float(q["d"][0]["v"]["lp"])
            except (KeyError, IndexError, TypeError, ValueError, httpx.HTTPError):
                pass  # the quote is a bonus; the profile already proved the session works
    except httpx.HTTPError as exc:
        raise FyersAuthError("Could not reach Fyers. Try again.") from exc
    return {"name": d.get("name", ""), "fy_id": d.get("fy_id", ""), "email": d.get("email_id", ""), "nifty": nifty}
