"""Run a backtest end to end: load data -> engine -> metrics -> persist (PRD M3)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.backtest import data as bt_data
from trader.backtest.costs import default_cost_model
from trader.backtest.engine import BacktestEngine, EngineConfig
from trader.backtest.metrics import compute_metrics
from trader.backtest.pricing import ExecutionModel, HybridPricer, RealPricer, SyntheticPricer
from trader.db.models import BacktestRunRow
from trader.instruments.calendar import IST, get_calendar
from trader.market_data.resample import minutes
from trader.strategy import MomentumParams, MomentumStrategy, Strategy
from trader.strategy.rule_strategy import RuleStrategy
from trader.strategy.spec import StrategySpec, render_english, resolved_entry, semantic_errors, walk_operands


WARMUP_DAYS = 10  # calendar days of earlier candles loaded so indicators are ready on the first day (not traded)


@dataclass
class BacktestRequest:
    underlying: str = "NIFTY"
    start: date = date(2025, 10, 1)
    end: date = date(2026, 10, 1)
    timeframe: str = "5m"
    source: str = "auto"  # auto | real | simulated
    pricing: str = "synthetic"  # synthetic | real | hybrid (real where stored, else modelled)
    strategy: str = "momentum"  # momentum | spec
    strategy_params: dict = field(default_factory=dict)
    spec: dict | None = None  # StrategySpec JSON when strategy == "spec"
    strategy_version_id: str | None = None
    engine_params: dict = field(default_factory=dict)
    execution_params: dict = field(default_factory=dict)
    risk_free_rate: float = 0.065


def _momentum_params(p: dict) -> MomentumParams:
    p = dict(p)
    for k in ("entry_start", "entry_end"):
        if isinstance(p.get(k), str):
            p[k] = time.fromisoformat(p[k])
    if "event_days" in p:
        p["event_days"] = tuple(date.fromisoformat(str(d)) for d in p["event_days"])
    return MomentumParams(**p)


def _engine_config(p: dict) -> EngineConfig:
    p = dict(p)
    if isinstance(p.get("eod_exit"), str):
        p["eod_exit"] = time.fromisoformat(p["eod_exit"])
    return EngineConfig(**p)


def build_strategy(req: BacktestRequest) -> tuple[Strategy, EngineConfig, bool, dict, bool]:
    """Strategy, engine config, roll-on-expiry flag, params for the report, uses-VWAP flag."""
    eng = dict(req.engine_params)
    if req.strategy == "momentum":
        sp = _momentum_params(req.strategy_params)
        return MomentumStrategy(sp), _engine_config(eng), True, sp.to_dict(), sp.use_vwap
    if req.strategy == "spec":
        if not req.spec:
            raise ValueError("strategy 'spec' needs a spec")
        spec = StrategySpec.model_validate(req.spec)
        errs = semantic_errors(spec)
        if errs:
            raise ValueError("; ".join(errs))
        if req.underlying not in spec.underlyings:
            raise ValueError(f"spec '{spec.name}' does not trade {req.underlying}")
        e = spec.exits
        # Exits and risk come from the spec; sizing/capital/daily loss still from the request.
        eng.update(trail_activate_pct=e.trail.activate_pct if e.trail else None,
                   trail_pct=e.trail.trail_pct if e.trail else None,
                   max_hold_bars=e.time_exit_bars, eod_exit=e.eod,
                   max_trades_per_day=spec.risk.max_trades_per_day)
        uses_vwap = any(o.ind in ("vwap", "fut_close") for c in _all_conditions(spec)
                        for o in walk_operands(c) if hasattr(o, "ind"))
        params = {"spec": spec.model_dump(mode="json", by_alias=True, exclude_none=True), "spec_hash": spec.hash,
                  "english": render_english(spec), "strategy_version_id": req.strategy_version_id}
        return RuleStrategy(spec), _engine_config(eng), spec.option_selection.roll_on_expiry_day, params, uses_vwap
    raise ValueError(f"Unknown strategy {req.strategy}")


def _all_conditions(spec: StrategySpec) -> list:
    out = [resolved_entry(spec, s) for s in ("long", "short")] + list(spec.filters.all)
    if spec.exits.conditions is not None:
        out.append(spec.exits.conditions)
    return [c for c in out if c is not None]


def execute(req: BacktestRequest, data: bt_data.BacktestData) -> dict:
    """Pure, synchronous part (CPU-bound): safe to run in a worker thread."""
    strategy, eng_cfg, roll, strat_report, uses_vwap = build_strategy(req)
    cal = get_calendar()
    if req.pricing == "real":
        pricer = RealPricer(data.options, data.option_lot_sizes, cal, roll_on_expiry_day=roll)
    elif req.pricing == "synthetic":
        pricer = SyntheticPricer(cal, req.risk_free_rate, roll_on_expiry_day=roll)
    elif req.pricing == "hybrid":
        pricer = HybridPricer(RealPricer(data.options, data.option_lot_sizes, cal, roll_on_expiry_day=roll),
                              SyntheticPricer(cal, req.risk_free_rate, roll_on_expiry_day=roll))
    else:
        raise ValueError(f"Unknown pricing mode {req.pricing}")
    tf_min = minutes(req.timeframe)
    engine = BacktestEngine(req.underlying, strategy, pricer, default_cost_model(),
                            tf_min, eng_cfg, ExecutionModel(**req.execution_params))
    result = engine.run(data.bars, data.futures, data.vix, trade_from=req.start)
    metrics = compute_metrics(result.trades, eng_cfg.capital, [d for d in data.trading_days if d >= req.start.isoformat()])
    warnings = list(result.warnings)
    if data.source == "simulated":
        warnings.insert(0, "Market data is SIMULATED (generated by the built-in simulator), not real NSE history.")
    if data.built_from and data.built_from != req.timeframe:
        warnings.append(f"{req.timeframe} candles were built from stored {data.built_from} candles "
                        "(exact OHLCV aggregation, anchored to the 09:15 open).")
    if not data.futures and uses_vwap:
        warnings.append("No futures candles in range: the VWAP condition blocked every setup. "
                        "Backfill futures or set use_vwap=false.")
    return {
        "data_source": data.source,
        "built_from": data.built_from,
        "pricing_mode": result.pricing_mode,
        "bars": result.bars,
        "days": result.days,
        "signals": result.signals,
        "no_trade_reasons": result.no_trade_reasons,
        "skipped_entries": result.skipped_entries,
        "warnings": warnings,
        "metrics": metrics,
        "trades": [t.__dict__ for t in result.trades],
        "params": {"strategy": strat_report,
                   "engine": {**eng_cfg.__dict__, "eod_exit": eng_cfg.eod_exit.isoformat()},
                   "execution": ExecutionModel(**req.execution_params).__dict__},
    }


async def run_and_store(sessionmaker: async_sessionmaker, req: BacktestRequest, owner: str = "local") -> dict:
    async with sessionmaker() as s:
        data = await bt_data.load(s, req.underlying, req.timeframe, req.start - timedelta(days=WARMUP_DAYS), req.end, req.source,
                                  with_options=req.pricing in ("real", "hybrid"))
    if not any(b.ts.astimezone(IST).date() >= req.start for b in data.bars):
        raise LookupError(f"No {req.timeframe} candles for {req.underlying} between {req.start} and {req.end} "
                          f"(source={req.source}). Backfill history first.")
    out = await asyncio.to_thread(execute, req, data)
    run_id = uuid.uuid4().hex[:12]
    row = BacktestRunRow(
        id=run_id, owner=owner, created_at=datetime.now(timezone.utc), underlying=req.underlying, start=req.start, end=req.end,
        timeframe=req.timeframe,
        strategy=(f"spec:{out['params']['strategy']['spec']['name']}"[:32] if req.strategy == "spec" else req.strategy),
        data_source=out["data_source"],
        pricing_mode=out["pricing_mode"], summary=out["metrics"] | {"equity_curve": None, "daily_pnl": None},
        result=out,
    )
    async with sessionmaker() as s, s.begin():
        s.add(row)
    return {"id": run_id, "underlying": req.underlying, "start": req.start.isoformat(), "end": req.end.isoformat(),
            "timeframe": req.timeframe, "strategy": req.strategy, **out}
