from __future__ import annotations

from fastapi import HTTPException, Request

from trader.instruments.specs import UNDERLYINGS
from trader.market_data.service import MarketDataService


def get_md(request: Request) -> MarketDataService:
    return request.app.state.md


def normalize_underlying(underlying: str) -> str:
    u = underlying.upper()
    if u not in UNDERLYINGS:
        raise HTTPException(404, f"Unknown underlying '{underlying}'. Supported: {', '.join(UNDERLYINGS)}")
    return u
