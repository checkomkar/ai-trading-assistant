from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from trader.api.deps import get_md, normalize_underlying
from trader.db import repository as repo
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL
from trader.market_data.candles import TIMEFRAMES
from trader.market_data.service import MarketDataService

router = APIRouter(prefix="/market", tags=["market"])


def _quote_or_404(md: MarketDataService, symbol: str) -> dict:
    q = md.quote(symbol)
    if q is None:
        raise HTTPException(503, f"No data yet for {symbol}")
    return asdict(q)


@router.get("/overview")
async def overview(md: MarketDataService = Depends(get_md)) -> dict:
    now = md.now()
    return {
        "session": md.calendar.session_state(now).value,
        "server_time": now.isoformat(),
        "simulated": md.provider.is_simulated,
        "quotes": {s: (asdict(q) if (q := md.quote(s)) else None) for s in (*UNDERLYINGS, VIX_SYMBOL)},
    }


@router.get("/quotes")
async def quotes(symbols: str = Query(..., description="Comma-separated, e.g. NIFTY,BANKNIFTY,INDIAVIX"),
                 md: MarketDataService = Depends(get_md)) -> dict:
    out = {}
    for s in (x.strip().upper() for x in symbols.split(",") if x.strip()):
        q = md.quote(s)
        out[s] = asdict(q) if q else None
    return out


@router.get("/candles")
async def candles(
    request: Request,
    symbol: str = Query("NIFTY", description="NIFTY | BANKNIFTY | INDIAVIX | NIFTY-FUT | BANKNIFTY-FUT"),
    timeframe: str = Query("5m"),
    limit: int = Query(300, ge=1, le=5000),
    md: MarketDataService = Depends(get_md),
) -> dict:
    if timeframe not in md.candles.timeframes:
        raise HTTPException(400, f"timeframe must be one of {list(md.candles.timeframes)}")
    inst = md.candle_instrument(symbol.upper())
    if inst is None:
        raise HTTPException(404, f"Unknown symbol {symbol}")
    rows = []
    inst_id = md.token_to_id.get(inst.token)
    if inst_id is not None:
        async with request.app.state.sessionmaker() as s:
            for r in await repo.get_candles(s, inst_id, timeframe, limit):
                ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)  # SQLite returns naive
                rows.append(dict(ts=ts.isoformat(), open=r.open, high=r.high, low=r.low,
                                 close=r.close, volume=r.volume, closed=True))
    live = md.candles.current(inst.token, timeframe)
    if live is not None:
        live_d = live.to_dict()
        rows = [r for r in rows if r["ts"] != live_d["ts"]]
        rows.append({k: live_d[k] for k in ("ts", "open", "high", "low", "close", "volume", "closed")})
    return {"symbol": inst.symbol, "token": inst.token, "timeframe": timeframe,
            "interval_seconds": int(TIMEFRAMES[timeframe].total_seconds()), "candles": rows[-limit:]}


@router.get("/{underlying}")
async def underlying_quote(underlying: str, md: MarketDataService = Depends(get_md)) -> dict:
    u = underlying.upper()
    if u != VIX_SYMBOL:
        u = normalize_underlying(u)
    out = _quote_or_404(md, u)
    out["session"] = md.calendar.session_state(md.now()).value
    return out
