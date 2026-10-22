"""Stocks: the NIFTY 50 daily-candle store and stock paper portfolios (forward tests on real daily candles).

Like options paper trading this is research only: no endpoint takes an order, and nothing here reaches a broker.
Portfolios are per account; the daily candles are shared market data, refreshed by the operator."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from trader.api.deps import AdminUser, CurrentUser
from trader.backtest import equity_paper
from trader.backtest.equity_runner import parse_spec
from trader.db.models import EquityPaperRow, StrategyRow
from trader.instruments import equities as eq
from trader.market_data import equity_daily

router = APIRouter(prefix="/equity", tags=["equity"])
MAX_PORTFOLIOS = 5


class PaperIn(BaseModel):
    strategy_id: str
    capital: float = Field(100_000, ge=10_000, le=100_000_000)
    name: str | None = Field(None, max_length=80)


@router.get("/universe")
async def universe(request: Request, user: CurrentUser) -> dict:
    async with request.app.state.sessionmaker() as s:
        cov = await equity_daily.coverage(s)
    cov.pop("per_symbol", None)
    latest = await equity_paper.latest_day(request.app.state.sessionmaker)
    return {"universes": {k: list(v) for k, v in eq.UNIVERSES.items()}, "coverage": cov,
            "latest_day": latest.isoformat() if latest else None}


@router.get("/coverage")
async def coverage(request: Request, user: CurrentUser) -> dict:
    async with request.app.state.sessionmaker() as s:
        return await equity_daily.coverage(s)


@router.post("/refresh")
async def refresh(request: Request, admin: AdminUser) -> dict:
    """Operator only: fetch the days since the last stored one for the whole NIFTY 50 (a few minutes the first time)."""
    st = request.app.state
    state = getattr(st, "equity_refresh", None)
    if state and state.get("running"):
        return state
    provider = st.md.provider
    if provider.is_simulated:
        raise HTTPException(409, "The market data provider is simulated; stock history needs the broker (Fyers).")
    state = st.equity_refresh = {"running": True, "started": datetime.now(timezone.utc).isoformat()}

    async def job() -> None:
        try:
            out = await equity_daily.refresh(provider, st.sessionmaker, list(eq.NIFTY50))
            state.update(out | {"candles": sum(out["stored"].values())})
        except Exception as exc:  # noqa: BLE001 - report to the operator instead of losing it in a background task
            state["error"] = str(exc)[:300]
        finally:
            state["running"] = False

    asyncio.create_task(job())
    return state


@router.get("/refresh")
async def refresh_status(request: Request, admin: AdminUser) -> dict:
    return getattr(request.app.state, "equity_refresh", None) or {"running": False}


def _cache(request: Request) -> dict:
    st = request.app.state
    if not hasattr(st, "equity_paper_cache"):
        st.equity_paper_cache = {}
    return st.equity_paper_cache


async def _mine(request: Request, user, pid: str) -> EquityPaperRow:
    async with request.app.state.sessionmaker() as s:
        row = await s.get(EquityPaperRow, pid)
    if row is None or row.owner != user.id or not row.active:
        raise HTTPException(404, "portfolio not found")
    return row


@router.get("/paper")
async def list_paper(request: Request, user: CurrentUser) -> list[dict]:
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(select(EquityPaperRow).where(EquityPaperRow.owner == user.id, EquityPaperRow.active.is_(True))
                                .order_by(EquityPaperRow.created_at.desc()))).scalars().all()
    out = []
    for r in rows:
        try:
            d = await equity_paper.evaluate(request.app.state.sessionmaker, r, _cache(request))
            out.append({k: d[k] for k in ("id", "name", "capital", "start_day", "equity", "return_pct", "days_running")}
                       | {"positions": len(d["positions"]), "trades": d["metrics"]["trades"]})
        except Exception as exc:  # noqa: BLE001 - one broken portfolio must not hide the others
            out.append({"id": r.id, "name": r.name, "capital": r.capital, "start_day": r.start_day.isoformat(),
                        "error": str(exc)[:200]})
    return out


@router.post("/paper")
async def create_paper(request: Request, body: PaperIn, user: CurrentUser) -> dict:
    sm = request.app.state.sessionmaker
    async with sm() as s:
        strat = await s.get(StrategyRow, body.strategy_id)
        n = len((await s.execute(select(EquityPaperRow.id).where(EquityPaperRow.owner == user.id,
                                                                  EquityPaperRow.active.is_(True)))).scalars().all())
    if strat is None or strat.owner != user.id:
        raise HTTPException(404, "strategy not found")
    try:
        parse_spec(strat.spec)
    except ValueError as exc:
        raise HTTPException(422, f"this is not a valid stock strategy: {exc}") from None
    if n >= MAX_PORTFOLIOS:
        raise HTTPException(409, f"You can run up to {MAX_PORTFOLIOS} stock paper portfolios. Remove one first.")
    last = await equity_paper.latest_day(sm)
    if last is None:
        raise HTTPException(409, "No daily stock candles are stored yet. The operator loads them first.")
    row = EquityPaperRow(id=uuid.uuid4().hex[:12], owner=user.id, name=(body.name or f"{strat.name} v{strat.version}")[:80],
                         strategy_id=strat.id, spec=strat.spec, capital=body.capital, start_day=last, active=True,
                         created_at=datetime.now(timezone.utc))
    async with sm() as s, s.begin():
        s.add(row)
    return await equity_paper.evaluate(sm, row, _cache(request))


@router.get("/paper/{pid}")
async def get_paper(request: Request, pid: str, user: CurrentUser) -> dict:
    row = await _mine(request, user, pid)
    return await equity_paper.evaluate(request.app.state.sessionmaker, row, _cache(request))


@router.delete("/paper/{pid}")
async def remove_paper(request: Request, pid: str, user: CurrentUser) -> dict:
    """Stops and hides a portfolio (the row is kept, flagged inactive)."""
    await _mine(request, user, pid)
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await s.get(EquityPaperRow, pid)
        row.active = False
    return {"removed": pid}
