from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from trader.backtest import data as bt_data
from trader.backtest.runner import BacktestRequest, run_and_store
from trader.db.models import BacktestRunRow, StrategyRow
from trader.features import FeatureConfig, FeatureEngine
from trader.market_data.resample import minutes

router = APIRouter(prefix="/backtests", tags=["backtests"])


class StrategyParamsIn(BaseModel):
    rsi_long: float = Field(55, ge=50, le=90)
    rsi_short: float = Field(45, ge=10, le=50)
    use_vwap: bool = True
    adx_min: float = Field(18, ge=0, le=60)
    atr_pct_min: float = Field(0.04, ge=0, le=2)
    entry_start: str = "09:30"
    entry_end: str = "14:45"
    max_trades_per_day: int = Field(3, ge=1, le=20)
    stop_loss_pct: float = Field(25, gt=0, lt=100)
    target_pct: float = Field(50, gt=0, le=500)


class EngineParamsIn(BaseModel):
    lots: int = Field(1, ge=1, le=50)
    max_daily_loss: float = Field(5000, gt=0)
    trail_activate_pct: float | None = Field(30, gt=0)
    trail_pct: float | None = Field(15, gt=0, lt=100)
    max_hold_bars: int | None = Field(None, ge=1)
    eod_exit: str = "15:15"
    capital: float = Field(100_000, gt=0)


class ExecutionParamsIn(BaseModel):
    spread_pct: float = Field(0.30, ge=0, le=10)
    slippage_ticks: int = Field(1, ge=0, le=20)


class BacktestIn(BaseModel):
    underlying: Literal["NIFTY", "BANKNIFTY"] = "NIFTY"
    start: date
    end: date
    timeframe: Literal["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m", "60m"] = "5m"
    source: Literal["auto", "real", "simulated"] = "auto"
    pricing: Literal["synthetic", "real"] = "synthetic"
    strategy: Literal["momentum", "spec"] = "momentum"
    strategy_id: str | None = None  # saved strategy version (PRD §19); implies strategy="spec"
    spec: dict | None = None  # unsaved spec, for quick tries
    strategy_params: StrategyParamsIn = StrategyParamsIn()
    engine_params: EngineParamsIn = EngineParamsIn()
    execution_params: ExecutionParamsIn = ExecutionParamsIn()

    @model_validator(mode="after")
    def _range(self) -> "BacktestIn":
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        if (self.end - self.start).days > 3 * 366:
            raise ValueError("range is limited to 3 years per run")
        return self


@router.get("/coverage")
async def coverage(request: Request, underlying: str = Query("NIFTY"), timeframe: str = Query("5m")) -> dict:
    async with request.app.state.sessionmaker() as s:
        return await bt_data.coverage(s, underlying.upper(), timeframe)


@router.post("")
async def run(request: Request, body: BacktestIn) -> dict:
    spec, version_id, strategy = body.spec, None, body.strategy
    if body.strategy_id:
        async with request.app.state.sessionmaker() as s:
            row = await s.get(StrategyRow, body.strategy_id)
        if row is None:
            raise HTTPException(404, "strategy not found")
        spec, version_id, strategy = row.spec, row.id, "spec"
    req = BacktestRequest(
        underlying=body.underlying, start=body.start, end=body.end, timeframe=body.timeframe,
        source=body.source, pricing=body.pricing, strategy=strategy, spec=spec, strategy_version_id=version_id,
        strategy_params=body.strategy_params.model_dump(), engine_params=body.engine_params.model_dump(),
        execution_params=body.execution_params.model_dump(),
        risk_free_rate=request.app.state.md.settings.risk_free_rate,
    )
    try:
        return await run_and_store(request.app.state.sessionmaker, req)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("")
async def list_runs(request: Request, limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(
            select(BacktestRunRow).order_by(BacktestRunRow.created_at.desc()).limit(limit))).scalars().all()
    keys = ("trades", "total_net_pnl", "win_rate", "profit_factor", "max_drawdown", "sharpe")
    return [{"id": r.id, "created_at": r.created_at.isoformat(), "underlying": r.underlying,
             "start": r.start.isoformat(), "end": r.end.isoformat(), "timeframe": r.timeframe,
             "strategy": r.strategy, "data_source": r.data_source, "pricing_mode": r.pricing_mode,
             **{k: (r.summary or {}).get(k) for k in keys}} for r in rows]


@router.get("/{run_id}/chart")
async def chart(request: Request, run_id: str) -> dict:
    """Underlying candles for a run + the EMA overlays the strategy saw, from the same FeatureEngine."""
    async with request.app.state.sessionmaker() as s:
        row = await s.get(BacktestRunRow, run_id)
        if row is None:
            raise HTTPException(404, "backtest not found")
        data = await bt_data.load(s, row.underlying, row.timeframe, row.start, row.end, row.data_source)
    sp = (row.result.get("params") or {}).get("strategy", {})
    cfg = FeatureConfig(ema_fast=int(sp.get("ema_fast", 9)), ema_slow=int(sp.get("ema_slow", 21)))
    fe = FeatureEngine(minutes(row.timeframe), cfg)
    bars, fast, slow = [], [], []
    for b in data.bars:
        f = fe.update(b)
        t = int(b.ts.timestamp())
        bars.append([t, b.open, b.high, b.low, b.close])
        if f["ema_fast"] is not None:
            fast.append([t, round(f["ema_fast"], 2)])
        if f["ema_slow"] is not None:
            slow.append([t, round(f["ema_slow"], 2)])
    return {"underlying": row.underlying, "timeframe": row.timeframe, "data_source": data.source,
            "bars": bars, "ema_fast": {"period": cfg.ema_fast, "points": fast},
            "ema_slow": {"period": cfg.ema_slow, "points": slow}}


@router.get("/{run_id}")
async def get_run(request: Request, run_id: str) -> dict:
    async with request.app.state.sessionmaker() as s:
        row = await s.get(BacktestRunRow, run_id)
    if row is None:
        raise HTTPException(404, "backtest not found")
    return {"id": row.id, "underlying": row.underlying, "start": row.start.isoformat(), "end": row.end.isoformat(),
            "timeframe": row.timeframe, "strategy": row.strategy, **row.result}
