"""Indicator catalogue and per-account chart preferences (PRD §24.3 B2.4-B2.5)."""

from __future__ import annotations

import re

from fastapi import APIRouter, Body, HTTPException, Query, Request

from trader.api.deps import CurrentUser
from trader.db.models import UserPrefRow
from trader.strategy import catalogue

router = APIRouter(tags=["indicators"])

PREF_KEYS = re.compile(r"^[a-z0-9_.-]{1,64}$")
MAX_PREF_BYTES = 8_000


@router.get("/indicators")
async def indicators(q: str = Query("", max_length=80), limit: int = Query(200, ge=1, le=200)) -> list[dict]:
    """Every indicator a strategy or chart can use, or those matching `q` (name, label, aliases, category)."""
    return catalogue.search(q, limit)


@router.get("/prefs/{key}")
async def get_pref(request: Request, key: str, user: CurrentUser) -> dict:
    if not PREF_KEYS.match(key):
        raise HTTPException(400, "bad preference key")
    async with request.app.state.sessionmaker() as s:
        row = await s.get(UserPrefRow, (user.id, key))
    return {"key": key, "value": row.value if row else None}


@router.put("/prefs/{key}")
async def put_pref(request: Request, key: str, user: CurrentUser, value: dict | list = Body(..., embed=True)) -> dict:
    if not PREF_KEYS.match(key):
        raise HTTPException(400, "bad preference key")
    import json

    if len(json.dumps(value)) > MAX_PREF_BYTES:
        raise HTTPException(413, "preference too large")
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await s.get(UserPrefRow, (user.id, key))
        if row is None:
            s.add(UserPrefRow(owner=user.id, key=key, value=value))
        else:
            row.value = value
    return {"key": key, "value": value}
