from __future__ import annotations

import asyncio

from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from trader.api.deps import CurrentUser, MeterDep
from trader.backtest import chart_series
from trader.backtest import data as bt_data
from trader.backtest.runner import WARMUP_DAYS, BacktestRequest, run_and_store
from trader.db.models import BacktestRunRow, StrategyRow
from trader.instruments.calendar import IST
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
    stop_slippage_range_pct: float = Field(10.0, ge=0, le=100)  # intrabar stop fills this share of the bar's range below
    min_entry_lots: float = Field(1.0, ge=0, le=1000)  # real candles: lots that must have traded in the entry bar


class BacktestIn(BaseModel):
    underlying: str = "NIFTY"  # ignored for stock strategies, which trade the stocks named in the spec
    start: date
    end: date
    timeframe: Literal["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m", "60m", "1d"] = "5m"
    source: Literal["auto", "real", "simulated"] = "auto"
    pricing: Literal["synthetic", "real", "hybrid"] = "synthetic"
    strategy: Literal["momentum", "spec"] = "momentum"
    strategy_id: str | None = None  # saved strategy version (PRD §19); implies strategy="spec"
    spec: dict | None = None  # unsaved spec, for quick tries
    strategy_params: StrategyParamsIn = StrategyParamsIn()
    engine_params: EngineParamsIn = EngineParamsIn()
    execution_params: ExecutionParamsIn = ExecutionParamsIn()
    slippage_pct: float = Field(0.05, ge=0, le=5)  # stock strategies: per side
    quantity: int | None = Field(None, ge=1, le=1_000_000)  # stock strategies: fixed shares per trade (None = size from capital)

    @model_validator(mode="after")
    def _range(self) -> "BacktestIn":
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        years = 15 if self.timeframe == "1d" else 3  # daily stock bars are cheap; intraday runs are not
        if (self.end - self.start).days > years * 366:
            raise ValueError(f"a single run can cover at most {years} years: choose a later start date")
        return self


@router.get("/coverage")
async def coverage(request: Request, underlying: str = Query("NIFTY"), timeframe: str = Query("5m")) -> dict:
    async with request.app.state.sessionmaker() as s:
        return await bt_data.coverage(s, underlying.upper(), timeframe)


@router.post("")
async def run(request: Request, body: BacktestIn, user: CurrentUser, meter: MeterDep) -> dict:
    spec, version_id, strategy = body.spec, None, body.strategy
    if body.strategy_id:
        async with request.app.state.sessionmaker() as s:
            row = await s.get(StrategyRow, body.strategy_id)
        if row is None or row.owner != user.id:
            raise HTTPException(404, "strategy not found")
        spec, version_id, strategy = row.spec, row.id, "spec"
    if spec and spec.get("market") == "equity":
        return await _run_equity(request, body, spec, version_id, user, meter)
    if body.underlying not in ("NIFTY", "BANKNIFTY"):
        raise HTTPException(422, "underlying must be NIFTY or BANKNIFTY")
    if body.timeframe == "1d":
        raise HTTPException(422, "daily candles are for stock strategies")
    req = BacktestRequest(
        underlying=body.underlying, start=body.start, end=body.end, timeframe=body.timeframe,
        source=body.source, pricing=body.pricing, strategy=strategy, spec=spec, strategy_version_id=version_id,
        strategy_params=body.strategy_params.model_dump(), engine_params=body.engine_params.model_dump(),
        execution_params=body.execution_params.model_dump(),
        risk_free_rate=request.app.state.md.settings.risk_free_rate,
    )
    try:
        async with meter.counted(user, "backtests"):  # no model involved: only a count, refunded if the run fails
            return await run_and_store(request.app.state.sessionmaker, req, owner=user.id)
    except LookupError as exc:
        raise HTTPException(404, str(exc) if user.is_admin else "Market history for this period is not available yet. Please try again later.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


async def _run_equity(request: Request, body: BacktestIn, spec: dict, version_id: str | None, user, meter) -> dict:
    from trader.backtest import equity_runner as er

    req = er.EquityBacktestRequest(
        spec=spec, start=body.start, end=body.end, capital=body.engine_params.capital,
        slippage_pct=body.slippage_pct, quantity=body.quantity, strategy_version_id=version_id,
        risk_free_rate=request.app.state.md.settings.risk_free_rate)
    try:
        async with meter.counted(user, "backtests"):
            return await er.run_and_store(request.app.state.sessionmaker, req, owner=user.id)
    except LookupError as exc:
        raise HTTPException(404, str(exc) + (er.OPERATOR_HINT if user.is_admin else "")) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.get("")
async def list_runs(request: Request, user: CurrentUser, limit: int = Query(20, ge=1, le=200)) -> list[dict]:
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(select(BacktestRunRow).where(BacktestRunRow.owner == user.id)
                                .order_by(BacktestRunRow.created_at.desc()).limit(limit))).scalars().all()
    keys = ("trades", "total_net_pnl", "win_rate", "profit_factor", "max_drawdown", "sharpe")
    return [{"id": r.id, "created_at": r.created_at.isoformat(), "underlying": r.underlying,
             "start": r.start.isoformat(), "end": r.end.isoformat(), "timeframe": r.timeframe,
             "strategy": r.strategy, "data_source": r.data_source, "pricing_mode": r.pricing_mode,
             **{k: (r.summary or {}).get(k) for k in keys}} for r in rows]


async def _equity_chart(s, row: BacktestRunRow, symbol: str | None, from_date: date | None, last_days: int | None,
                        extras: list | None = None) -> dict:
    from trader.backtest.equity import BAR_MINUTES
    from trader.backtest.equity_runner import parse_spec
    from trader.instruments import equities as eq
    from trader.market_data import equity_daily

    sp = (row.result.get("params") or {})["strategy"]
    traded = sorted({t["contract"] for t in row.result.get("trades", [])})
    universe = eq.expand(parse_spec(sp["spec"]).underlyings)
    sym = symbol or (traded[0] if traded else universe[0])
    if sym not in universe:
        raise HTTPException(404, f"{sym} is not part of this run")
    lo = max(from_date or row.start, row.start)  # a stock chart shows the whole run: daily bars are few
    # Fed from the run's own warm-up start, like the engine, so indicator values are the ones it used.
    cs = (await equity_daily.load_daily(s, [sym], eq.warmup_start(row.start), row.end)).get(sym, [])
    try:
        specs = chart_series.strategy_series(sp)
    except ValueError:
        specs = []
    seen = {x.key for x in specs}
    specs += [x for x in (extras or []) if x.key not in seen]
    keep = next((b.ts for b in cs if eq.day_of(b.ts) >= lo), None)
    # the stock's own volume plays the role futures volume has for indices, as in backtest/equity.py
    values = chart_series.compute(cs, {b.ts: b for b in cs}, {}, specs, BAR_MINUTES, keep)
    bars = [[int(b.ts.timestamp()), b.open, b.high, b.low, b.close] for b in cs if keep is not None and b.ts >= keep]
    series = chart_series.describe(specs, values)
    by_id = {x["id"]: x["points"] for x in series}
    return {"underlying": sym, "symbol": sym, "symbols": sorted(set(traded) | {sym}), "timeframe": "1d", "run_timeframe": "1d",
            "exact": True, "data_source": "real", "bars": bars, "series": series,
            "ema_fast": {"period": 9, "points": by_id.get("ema|9||", [])}, "ema_slow": {"period": 21, "points": by_id.get("ema|21||", [])}}


@router.get("/{run_id}/chart")
async def chart(request: Request, run_id: str, user: CurrentUser, timeframe: str | None = None, from_date: date | None = None,
                last_days: int | None = Query(None, ge=1, le=400), symbol: str | None = None,
                extra: str | None = Query(None, max_length=600)) -> dict:
    """Underlying candles for a run at any timeframe (built exactly from 1m) + indicator series (PRD §24.3 B2).

    `series` holds every indicator the run's strategy referenced (source "strategy", with the constants it compared them
    with as `thresholds`) and the indicators the viewer added (`extra` = "name:period:mult,..."; source "user"). On the
    run's own timeframe they are computed from the run's first bar by the engine's own code, so they are exactly what
    the strategy saw (`exact: true`). `from_date` / `last_days` trim the candles returned, so 1m charts stay small."""
    try:
        extras = chart_series.parse_extra(extra)
    except ValueError as exc:
        raise HTTPException(422, f"extra: {exc}") from None
    async with request.app.state.sessionmaker() as s:
        row = await s.get(BacktestRunRow, run_id)
        if row is None or row.owner != user.id:
            raise HTTPException(404, "backtest not found")
        if row.timeframe == "1d":  # a stock run: daily candles of one of its stocks
            return await _equity_chart(s, row, symbol, from_date, last_days, extras)
        tf = timeframe or row.timeframe
        try:
            minutes(tf)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        lo = max(from_date or row.start, row.start)
        if last_days:
            lo = max(lo, row.end - timedelta(days=int(last_days * 1.5) + 2))  # calendar days ≈ 1.5x trading days
        exact = tf == row.timeframe
        # Indicators need the bars before the window: from the run's own warm-up start on its timeframe (identical values),
        # else a warm-up of the same length before the window.
        feed_from = (row.start if exact else lo) - timedelta(days=WARMUP_DAYS)
        data = await bt_data.load(s, row.underlying, tf, feed_from, row.end, row.data_source)
    sp = (row.result.get("params") or {}).get("strategy", {})
    try:
        specs = chart_series.strategy_series(sp)
    except ValueError:
        specs = []  # a run stored by an older version whose spec no longer validates: candles only
    seen = {x.key for x in specs}
    specs += [x for x in extras if x.key not in seen]
    window_from = datetime.combine(lo, datetime.min.time(), tzinfo=IST).astimezone(timezone.utc)
    if last_days:  # keep exactly the last N trading days
        days = [d for d in data.trading_days if d >= lo.isoformat()][-last_days:]
        if days:
            window_from = max(window_from, datetime.combine(date.fromisoformat(days[0]), datetime.min.time(),
                                                            tzinfo=IST).astimezone(timezone.utc))
    values = await asyncio.to_thread(chart_series.compute, data.bars, data.futures, data.vix, specs, minutes(tf), window_from)
    bars = [[int(b.ts.timestamp()), b.open, b.high, b.low, b.close] for b in data.bars if b.ts >= window_from]
    series = chart_series.describe(specs, values)
    by_name = {x["id"]: x["points"] for x in series}
    ema9, ema21 = by_name.get("ema|9||"), by_name.get("ema|21||")
    return {"underlying": row.underlying, "timeframe": tf, "run_timeframe": row.timeframe, "exact": exact,
            "data_source": data.source, "bars": bars, "series": series,
            # kept for older clients
            "ema_fast": {"period": 9, "points": ema9 or []}, "ema_slow": {"period": 21, "points": ema21 or []}}


@router.get("/{run_id}")
async def get_run(request: Request, run_id: str, user: CurrentUser) -> dict:
    async with request.app.state.sessionmaker() as s:
        row = await s.get(BacktestRunRow, run_id)
    if row is None or row.owner != user.id:
        raise HTTPException(404, "backtest not found")
    return {"id": row.id, "underlying": row.underlying, "start": row.start.isoformat(), "end": row.end.isoformat(),
            "timeframe": row.timeframe, "strategy": row.strategy, **row.result}
