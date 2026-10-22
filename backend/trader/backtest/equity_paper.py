"""Stock paper portfolios: forward-test a saved strategy on real daily candles, starting from a day the trader picks
(the latest stored day when created). There is no order endpoint and no broker: the portfolio is the equity engine run
from its start day to the latest stored day, so positions, tomorrow's orders and P&L are always consistent with what a
backtest of the same days would have done, and survive any restart because nothing but the settings is stored."""

from __future__ import annotations

import asyncio
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.backtest.equity import EquityConfig, EquityEngine
from trader.backtest.equity_costs import EquityCosts
from trader.backtest.equity_runner import build_metrics, parse_spec
from trader.db.models import CandleRow, EquityPaperRow
from trader.instruments import equities as eq
from trader.market_data import equity_daily


async def latest_day(sessionmaker: async_sessionmaker) -> date | None:
    async with sessionmaker() as s:
        ts = (await s.execute(select(func.max(CandleRow.ts)).where(CandleRow.timeframe == eq.DAILY))).scalar_one_or_none()
    return eq.day_of(ts) if ts else None


def _compute(row: EquityPaperRow, bars: dict, last_day: date | None) -> dict:
    spec = parse_spec(row.spec)
    engine = EquityEngine(spec, EquityConfig(row.capital, 0.05, EquityCosts()))
    res = engine.run(bars, row.start_day)
    metrics = build_metrics(res, res.trades, row.capital, spec.risk.max_positions)
    equity = res.equity_curve[-1]["equity"] if res.equity_curve else row.capital
    cash = res.equity_curve[-1]["cash"] if res.equity_curve else row.capital
    positions = []
    for p in res.open_positions:
        last = res.last_close.get(p.symbol, p.entry_price)
        positions.append({
            "symbol": p.symbol, "quantity": p.quantity, "entry_day": p.entry_day.isoformat(),
            "entry_price": round(p.entry_price, 2), "last": round(last, 2), "value": round(p.quantity * last, 2),
            "pnl": round((last - p.entry_price) * p.quantity, 2), "pnl_pct": round((last / p.entry_price - 1) * 100, 2),
            "stop": round(p.stop, 2), "target": round(p.target, 2), "days_held": p.bars,
            "exit_planned": p.pending_exit, "reason": p.reason})
    slot = row.capital / spec.risk.max_positions
    orders = [{"symbol": o["symbol"], "side": "BUY", "note": f"at the next open, about ₹{slot:,.0f} "
              f"({int(slot // res.last_close[o['symbol']])} shares at the last close)"
              if res.last_close.get(o["symbol"]) else "at the next open"} for o in res.pending_entries]
    orders += [{"symbol": o["symbol"], "side": "SELL", "note": f"at the next open ({o['reason']})"} for o in res.pending_exits]
    return {
        "id": row.id, "name": row.name, "strategy_id": row.strategy_id, "capital": row.capital,
        "start_day": row.start_day.isoformat(), "last_data_day": last_day.isoformat() if last_day else None,
        "english": render(spec), "equity": equity, "cash": cash, "return_pct": round((equity / row.capital - 1) * 100, 2),
        "days_running": len(res.days), "positions": positions, "orders": orders, "metrics": metrics,
        "trades": [t.__dict__ for t in res.trades], "warnings": res.warnings, "skipped": res.skipped,
        "max_positions": spec.risk.max_positions,
    }


def render(spec) -> str:  # noqa: ANN001
    from trader.strategy.spec import render_english

    return render_english(spec)


async def evaluate(sessionmaker: async_sessionmaker, row: EquityPaperRow, cache: dict) -> dict:
    """The portfolio's current state. Cached per (portfolio, latest stored day): the inputs only change when a new day's
    candles arrive."""
    last_day = await latest_day(sessionmaker)
    key = (row.id, row.capital, last_day)
    if key in cache:
        return cache[key]
    spec = parse_spec(row.spec)
    symbols = eq.expand(spec.underlyings)
    async with sessionmaker() as s:
        bars = await equity_daily.load_daily(s, symbols, eq.warmup_start(row.start_day), last_day or row.start_day)
    out = await asyncio.to_thread(_compute, row, {x: bars.get(x, []) for x in symbols}, last_day)
    for k in [k for k in cache if k[0] == row.id]:
        cache.pop(k, None)
    cache[key] = out
    return out
