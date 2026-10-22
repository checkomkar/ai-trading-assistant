"""Paper-trading API (PRD M4, Phase 11). There is deliberately no endpoint that takes an order:
orders come only from the strategy runner, through the risk engine."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from trader.db.models import EventRow, StrategyRow
from trader.execution.runner import LaneConfig
from trader.execution.service import PaperService
from trader.execution.store import load_trades
from trader.execution.summary import summarize, trade_to_dict, utc_iso
from trader.instruments.calendar import IST

router = APIRouter(prefix="/paper", tags=["paper"])


def svc(request: Request) -> PaperService:
    return request.app.state.paper


class StartIn(BaseModel):
    underlyings: list[Literal["NIFTY", "BANKNIFTY"]] | None = None
    timeframe: str | None = None  # default: the saved strategy's own timeframe, else the configured one
    strategy: Literal["momentum", "spec"] | None = None
    strategy_id: str | None = None  # saved strategy version; implies strategy="spec"
    strategy_params: dict = Field(default_factory=dict)
    engine_params: dict = Field(default_factory=dict)  # exits (trail, eod, hold bars) and lots


class KillIn(BaseModel):
    reason: str = "manual STOP"
    flatten: bool = True


@router.get("/status")
async def status(request: Request) -> dict:
    p = svc(request)
    if p.runner is not None:
        return p.runner.status()
    return {"running": False, "broker": p.broker.name, "data_source": p.md.provider.name,
            "option_pricing": p.md.provider.option_pricing, "cash": p.broker.cash,
            "starting_cash": p.broker.starting_cash, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "errors": 0,
            "last_error": None, "lanes": [], "risk": p.risk.status(), "restore": p.restore_report,
            "now": p.md.now().isoformat()}


@router.post("/start")
async def start(request: Request, body: StartIn | None = None) -> dict:
    p, body = svc(request), body or StartIn()
    base = p.default_lanes()
    strategy = body.strategy or ("spec" if body.strategy_id else base[0].strategy if base else "momentum")
    spec = version = None
    if body.strategy_id:
        async with request.app.state.sessionmaker() as s:
            row = await s.get(StrategyRow, body.strategy_id)
        if row is None:
            raise HTTPException(404, "strategy not found")
        spec, version, strategy = row.spec, row.id, "spec"
        if spec.get("market") == "equity":
            raise HTTPException(400, "Stock strategies paper-trade on the Stocks page (daily candles, end-of-day orders), "
                                     "not in this intraday runner.")
    # A saved strategy trades on its own candle size; the live feed only builds some of them.
    timeframe = body.timeframe or (spec["timeframe"] if spec else p.settings.paper_timeframe)
    if timeframe not in p.md.candles.timeframes:
        raise HTTPException(400, f"{timeframe} candles are not built live (only {', '.join(p.md.candles.timeframes)}), "
                                 "so this strategy cannot run in paper trading yet")
    try:
        lanes = [LaneConfig(underlying=u, timeframe=timeframe, strategy=strategy,
                            strategy_params=body.strategy_params, spec=spec, strategy_version_id=version,
                            engine_params=body.engine_params)
                 for u in (body.underlyings or [ln.underlying for ln in base])]
        runner = await p.start(lanes)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return runner.status()


@router.post("/stop")
async def stop(request: Request) -> dict:
    await svc(request).stop()
    return {"running": False}


@router.post("/kill")
async def kill(request: Request, body: KillIn | None = None) -> dict:
    """Emergency STOP: blocks new entries and, by default, closes open positions at market."""
    p, body = svc(request), body or KillIn()
    if p.runner is not None:
        await p.runner.kill(body.reason, flatten=body.flatten)
    else:
        p.risk.trip_kill_switch(body.reason, p.md.now())
        p.store.emit("kill_switch.tripped", None, {"reason": body.reason, "flatten": False})
        await p.persist()
    return p.risk.status()["kill_switch"]


@router.post("/kill/reset")
async def kill_reset(request: Request) -> dict:
    p = svc(request)
    p.risk.reset_kill_switch()
    p.store.emit("kill_switch.reset", None, {})
    await p.persist()
    return p.risk.status()["kill_switch"]


@router.post("/strategies/{name}/enabled")
async def set_enabled(request: Request, name: str, enabled: bool = Query(...)) -> dict:
    p = svc(request)
    p.risk.set_strategy_enabled(name, enabled)
    p.store.emit("strategy.enabled" if enabled else "strategy.disabled", None, {"strategy": name})
    await p.persist()
    return {"strategy": name, "enabled": enabled}


@router.get("/risk")
async def risk(request: Request) -> dict:
    return svc(request).risk.status()


@router.get("/positions")
async def positions(request: Request) -> dict:
    p = svc(request)
    return {"positions": p.runner.positions() if p.runner else []}


@router.get("/orders")
async def orders(request: Request) -> dict:
    return {"orders": [o.to_dict() for o in reversed(svc(request).broker.orders())]}


@router.get("/signals")
async def signals(request: Request) -> dict:
    p = svc(request)
    out = []
    for ln in (p.runner.lanes if p.runner else []):
        out.append({"underlying": ln.cfg.underlying, "strategy": ln.label, "last_bar":
                    ln.last_bar.ts.isoformat() if ln.last_bar else None, "last_decision": ln.last_decision,
                    "recent": list(ln.signals)[::-1]})
    return {"lanes": out}


@router.get("/journal")
async def journal(request: Request, day: str | None = None, symbol: str | None = None,
                  underlying: str | None = None, strategy: str | None = None,
                  direction: Literal["LONG", "SHORT"] | None = None, result: Literal["win", "loss"] | None = None,
                  data_source: str | None = None, limit: int = Query(500, ge=1, le=5000)) -> dict:
    async with request.app.state.sessionmaker() as s:
        rows = await load_trades(s, day=day, symbol=symbol, underlying=underlying, strategy=strategy,
                                 direction=direction, result=result, data_source=data_source, limit=limit)
    trades = [trade_to_dict(r) for r in rows]
    return {"trades": trades, "summary": summarize(trades)}


@router.get("/summary")
async def summary(request: Request, day: str | None = None, data_source: str | None = None) -> dict:
    """Daily summary (PRD §9.4). Defaults to the runner's current trading day."""
    p = svc(request)
    day = day or p.md.now().astimezone(IST).date().isoformat()
    async with request.app.state.sessionmaker() as s:
        rows = await load_trades(s, day=day, data_source=data_source, limit=5000)
    return {"day": day, "data_source": data_source or "all", **summarize([trade_to_dict(r) for r in rows])}


@router.get("/trace/{correlation_id}")
async def trace(request: Request, correlation_id: str) -> dict:
    """Everything recorded for one trade: signal -> risk -> order -> fill -> exit (PRD §14.4)."""
    p = svc(request)
    await p.store.flush()
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(select(EventRow).where(EventRow.correlation_id == correlation_id)
                                .order_by(EventRow.id))).scalars().all()
    if not rows:
        raise HTTPException(404, "no events for that correlation_id")
    return {"correlation_id": correlation_id, "events": [
        {"ts": utc_iso(r.ts), "event_type": r.event_type, "payload": r.payload} for r in rows]}


@router.get("/events")
async def events(request: Request, limit: int = Query(100, ge=1, le=300)) -> dict:
    return {"events": list(svc(request).store.recent)[-limit:][::-1]}
