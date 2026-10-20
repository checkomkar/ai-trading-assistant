"""Daily summary of paper trades (PRD §9.4): trades, wins, losses, gross/net P&L, drawdown, per strategy."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone


def utc_iso(v: datetime) -> str:
    """SQLite hands timestamps back naive; they are UTC (CLAUDE.md), so say so for the browser."""
    return (v if v.tzinfo else v.replace(tzinfo=timezone.utc)).isoformat()


def trade_to_dict(row) -> dict:  # noqa: ANN001 - PaperTradeRow
    def iso(v):  # noqa: ANN001, ANN202
        return utc_iso(v) if isinstance(v, datetime) else v

    cols = ("id", "correlation_id", "trading_day", "underlying", "symbol", "strategy", "strategy_version",
            "direction", "quantity", "entry_time", "entry_price", "exit_time", "exit_price", "exit_reason",
            "gross_pnl", "charges", "net_pnl", "mae_pct", "mfe_pct", "minutes_held", "signal_reason",
            "confidence", "data_source", "option_pricing", "features")
    return {c: iso(getattr(row, c)) for c in cols}


def _bucket(trades: list[dict]) -> dict:
    net = [t["net_pnl"] for t in trades]
    wins = [x for x in net if x > 0]
    return {"trades": len(trades), "wins": len(wins), "losses": len(trades) - len(wins),
            "net_pnl": round(sum(net), 2)}


def summarize(trades: list[dict]) -> dict:
    """`trades` need not be sorted. Drawdown is peak-to-trough of cumulative net P&L in exit order."""
    ordered = sorted(trades, key=lambda t: str(t["exit_time"]))
    net = [t["net_pnl"] for t in ordered]
    gross = sum(t["gross_pnl"] for t in ordered)
    charges = sum(t["charges"] for t in ordered)
    cum = peak = dd = 0.0
    for x in net:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    by: dict[str, list[dict]] = defaultdict(list)
    for t in ordered:
        by[t["strategy"]].append(t)
    by_reason: dict[str, int] = defaultdict(int)
    for t in ordered:
        by_reason[t["exit_reason"]] += 1
    gl = -sum(losses)
    return {
        "trades": len(ordered), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(ordered), 4) if ordered else None,
        "gross_pnl": round(gross, 2), "charges": round(charges, 2), "net_pnl": round(sum(net), 2),
        "max_drawdown": round(dd, 2),
        "profit_factor": round(sum(wins) / gl, 3) if gl > 0 else None,
        "best_trade": round(max(net), 2) if net else None, "worst_trade": round(min(net), 2) if net else None,
        "avg_minutes_held": round(sum(t["minutes_held"] for t in ordered) / len(ordered), 1) if ordered else None,
        "by_strategy": {k: _bucket(v) for k, v in by.items()},
        "by_exit_reason": dict(by_reason),
    }
