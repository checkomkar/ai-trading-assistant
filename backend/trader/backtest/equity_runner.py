"""Run a stock-strategy backtest end to end: load daily candles -> equity engine -> metrics -> persist."""

from __future__ import annotations

import asyncio
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.backtest.equity import CLOSE_T, EquityConfig, EquityEngine, EquityResult
from trader.backtest.equity_costs import EquityCosts
from trader.backtest.metrics import compute_metrics
from trader.db.models import BacktestRunRow
from trader.instruments import equities as eq
from trader.instruments.calendar import IST
from trader.market_data import equity_daily
from trader.market_data.candles import Candle
from trader.strategy.spec import StrategySpec, render_english, semantic_errors

DATA_WARNING = ("Daily prices are the broker's: if a stock split or bonus happened in the range and the broker did not "
                "adjust older prices, trades around it can look wrong. Costs are delivery charges (STT, stamp duty, "
                "exchange, DP); brokerage is assumed 0 unless set.")
NO_DATA = "Daily price history for these stocks is not available for this period yet. Please try again later."
OPERATOR_HINT = " (Operator: load it with python -m trader.scripts.backfill_equities --years 5, or the Stocks page button.)"


@dataclass
class EquityBacktestRequest:
    spec: dict
    start: date
    end: date
    capital: float = 100_000.0
    slippage_pct: float = 0.05
    brokerage_per_order: float = 0.0
    risk_free_rate: float = 0.065
    strategy_version_id: str | None = None


def parse_spec(raw: dict) -> StrategySpec:
    spec = StrategySpec.model_validate(raw)
    errs = semantic_errors(spec)
    if errs:
        raise ValueError("; ".join(errs))
    if spec.market != "equity":
        raise ValueError("not a stock strategy")
    return spec


def _ratios(curve: list[dict], capital: float) -> dict:
    rets = [(b["equity"] / a["equity"] - 1) for a, b in zip(curve, curve[1:]) if a["equity"]]
    if len(rets) < 2:
        return {"sharpe": None, "sortino": None}
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
    dsd = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / len(rets))
    return {"sharpe": round(mean / sd * math.sqrt(252), 2) if sd > 0 else None,
            "sortino": round(mean / dsd * math.sqrt(252), 2) if dsd > 0 else None}


def build_metrics(res: EquityResult, trades: list, capital: float, max_positions: int) -> dict:
    """The option engine's metrics, with the equity curve, drawdown and ratios taken from the daily mark-to-market
    value of the whole portfolio (positions are held for days, so realised-only curves would hide the dips)."""
    m = compute_metrics(trades, capital, res.days)
    pts = [{"date": None, "equity": capital}] + res.equity_curve
    peak, dd_max, dd_pct = capital, 0.0, 0.0
    curve = []
    for p in pts:
        peak = max(peak, p["equity"])
        dd = peak - p["equity"]
        if dd > dd_max:
            dd_max, dd_pct = dd, dd / peak * 100
        d = p.get("date") or (res.days[0] if res.days else None)
        ts = datetime.combine(date.fromisoformat(d), CLOSE_T, tzinfo=IST).astimezone(timezone.utc).isoformat() if d else None
        curve.append({"ts": ts, "equity": p["equity"], "drawdown": round(-dd, 2)})
    m["equity_curve"] = curve
    m["max_drawdown"], m["max_drawdown_pct"] = round(dd_max, 2), round(dd_pct, 2)
    m.update(_ratios(pts, capital))
    m["daily_pnl"] = [{"date": b["date"], "pnl": round(b["equity"] - a["equity"], 2)}
                      for a, b in zip(pts, pts[1:])]
    m["average_holding_days"] = round(sum(t.bars_held for t in trades) / len(trades), 1) if trades else None
    m["exposure_pct"] = (round(sum(p["positions"] for p in res.equity_curve) / (len(res.equity_curve) * max_positions) * 100, 1)
                         if res.equity_curve else None)
    per: dict[str, list] = defaultdict(list)
    for t in trades:
        per[t.contract].append(t)
    m["by_symbol"] = sorted(
        ({"symbol": s, "trades": len(ts), "net_pnl": round(sum(t.net_pnl for t in ts), 2),
          "win_rate": round(sum(1 for t in ts if t.net_pnl > 0) / len(ts) * 100, 1)} for s, ts in per.items()),
        key=lambda x: -x["net_pnl"])
    return m


def execute(req: EquityBacktestRequest, bars: dict[str, list[Candle]]) -> dict:
    """Pure and CPU-bound: safe to run in a worker thread."""
    spec = parse_spec(req.spec)
    cfg = EquityConfig(req.capital, req.slippage_pct, EquityCosts(brokerage_per_order=req.brokerage_per_order))
    engine = EquityEngine(spec, cfg)
    res = engine.run(bars, req.start, req.end)
    trades = res.trades + engine.finish_open(res, bars)
    metrics = build_metrics(res, trades, req.capital, spec.risk.max_positions)
    warnings = [DATA_WARNING, *res.warnings]
    if res.skipped:
        warnings.append("Signals not taken: " + ", ".join(f"{k} ({v})" for k, v in sorted(res.skipped.items())) + ".")
    if res.open_positions:
        warnings.append(f"{len(res.open_positions)} position(s) were still open on the last day and are counted as sold "
                        "at that day's close.")
    return {
        "data_source": "real", "built_from": None, "pricing_mode": "equity delivery",
        "bars": sum(len(v) for v in bars.values()), "days": len(res.days), "signals": res.signals,
        "no_trade_reasons": res.skipped, "skipped_entries": res.skipped, "warnings": warnings, "metrics": metrics,
        "trades": [t.__dict__ for t in trades],
        "params": {"strategy": {"spec": spec.model_dump(mode="json", by_alias=True, exclude_none=True),
                                "spec_hash": spec.hash, "english": render_english(spec),
                                "strategy_version_id": req.strategy_version_id, "market": "equity"},
                   "engine": {"capital": req.capital, "slippage_pct": req.slippage_pct,
                              "brokerage_per_order": req.brokerage_per_order,
                              "max_positions": spec.risk.max_positions},
                   "execution": {}},
    }


async def run_and_store(sessionmaker: async_sessionmaker, req: EquityBacktestRequest, owner: str = "local") -> dict:
    spec = parse_spec(req.spec)
    symbols = eq.expand(spec.underlyings)
    async with sessionmaker() as s:
        bars = await equity_daily.load_daily(s, symbols, eq.warmup_start(req.start), req.end)
    if not any(eq.day_of(c.ts) >= req.start for cs in bars.values() for c in cs):
        raise LookupError(NO_DATA)
    bars = {s: bars.get(s, []) for s in symbols}
    out = await asyncio.to_thread(execute, req, bars)
    run_id = uuid.uuid4().hex[:12]
    row = BacktestRunRow(
        id=run_id, owner=owner, created_at=datetime.now(timezone.utc),
        underlying=(spec.underlyings[0] if len(symbols) > 1 or len(spec.underlyings) > 1 else symbols[0])[:32],
        start=req.start, end=req.end, timeframe="1d", strategy=f"spec:{spec.name}"[:32], data_source="real",
        pricing_mode=out["pricing_mode"], summary=out["metrics"] | {"equity_curve": None, "daily_pnl": None},
        result=out)
    async with sessionmaker() as s, s.begin():
        s.add(row)
    return {"id": run_id, "underlying": row.underlying, "start": req.start.isoformat(), "end": req.end.isoformat(),
            "timeframe": "1d", "strategy": "spec", **out}
