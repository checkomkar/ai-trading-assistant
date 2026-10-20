from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from trader.api.deps import get_md, normalize_underlying
from trader.instruments.expiry import resolve_expiries
from trader.instruments.specs import get_spec
from trader.instruments.strikes import OptionType
from trader.market_data.service import MarketDataService

router = APIRouter(tags=["options"])


@router.get("/options/{underlying}/expiries")
async def expiries(underlying: str, md: MarketDataService = Depends(get_md)) -> dict:
    u = normalize_underlying(underlying)
    now = md.now()
    calc = resolve_expiries(u, now, md.calendar)
    spec = get_spec(u)
    return {
        "underlying": u,
        "listed": [d.isoformat() for d in md.registry.expiries(u, now)],
        "nearest": calc.nearest, "next": calc.next, "monthly": calc.monthly,
        "cycle": spec.expiry_cycle.value, "lot_size": spec.lot_size, "strike_interval": spec.strike_interval,
    }


@router.get("/options/{underlying}/chain")
async def chain(
    underlying: str,
    expiry: date | None = Query(None, description="YYYY-MM-DD; defaults to nearest listed expiry"),
    strikes: int | None = Query(None, ge=1, le=50, description="Limit to ATM +/- N strikes"),
    md: MarketDataService = Depends(get_md),
) -> dict:
    u = normalize_underlying(underlying)
    exp = expiry or md.registry.resolve_expiry(u, "nearest", md.now())
    if exp is None or exp not in md.registry.expiries(u):
        raise HTTPException(404, f"No listed expiry {expiry or ''} for {u}")
    return md.chain(u, exp, strikes).to_dict()


@router.get("/instruments/resolve")
async def resolve_option(
    underlying: str,
    option_type: OptionType,
    expiry: str = Query("nearest", description="nearest | next | monthly | YYYY-MM-DD"),
    strike: float | None = None,
    offset: int = Query(0, description="Strike steps from ATM when strike is omitted, e.g. 1 = ATM+1"),
    md: MarketDataService = Depends(get_md),
) -> dict:
    """Option resolver (PRD §3.4)."""
    u = normalize_underlying(underlying)
    selector: Literal["nearest", "next", "monthly"] | date
    selector = date.fromisoformat(expiry) if expiry[:1].isdigit() else expiry  # type: ignore[assignment]
    spot = md.spot(u)
    if strike is None and spot is None:
        raise HTTPException(503, "No spot price yet")
    inst = md.registry.resolve_option(u, option_type, md.now(), spot=spot, strike=strike, offset=offset,
                                      expiry=selector)
    if inst is None:
        raise HTTPException(404, "No matching contract")
    tick = md.latest.get(inst.token)
    return {
        "token": inst.token, "symbol": inst.symbol, "underlying": inst.underlying, "expiry": inst.expiry,
        "strike": inst.strike, "option_type": inst.option_type, "lot_size": inst.lot_size, "spot": spot,
        "ltp": tick.ltp if tick else None,
    }
