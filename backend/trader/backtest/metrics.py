"""Backtest performance metrics (PRD §8.4)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from datetime import datetime

from trader.backtest.engine import Trade
from trader.instruments.calendar import IST


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, n)


def compute_metrics(trades: list[Trade], capital: float, trading_days: list[str]) -> dict:
    """Trade, equity and risk statistics. Daily ratios include zero-P&L days (no trade)."""
    wins = [t for t in trades if t.net_pnl > 0]
    losses = [t for t in trades if t.net_pnl <= 0]
    gross_win = sum(t.net_pnl for t in wins)
    gross_loss = -sum(t.net_pnl for t in losses)
    n = len(trades)

    equity, peak, max_dd, max_dd_pct = capital, capital, 0.0, 0.0
    curve = [{"ts": None, "equity": capital}]
    for t in sorted(trades, key=lambda x: x.exit_time):
        equity += t.net_pnl
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd, max_dd_pct = dd, dd / peak * 100
        curve.append({"ts": t.exit_time, "equity": round(equity, 2), "drawdown": round(-dd, 2)})
    curve[0]["ts"] = (curve[1]["ts"] if len(curve) > 1 else None)

    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[datetime.fromisoformat(t.exit_time).astimezone(IST).date().isoformat()] += t.net_pnl
    rets = [daily.get(d, 0.0) / capital for d in trading_days] or [0.0]
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    downside = [min(r, 0.0) for r in rets]
    dsd = math.sqrt(sum(r * r for r in downside) / len(downside)) if downside else 0.0

    monthly: dict[str, float] = defaultdict(float)
    for d, v in daily.items():
        monthly[d[:7]] += v

    def side(direction: str) -> dict:
        ts = [t for t in trades if t.direction == direction]
        w = [t for t in ts if t.net_pnl > 0]
        return {"trades": len(ts), "win_rate": _r(len(w) / len(ts) * 100) if ts else None,
                "net_pnl": _r(sum(t.net_pnl for t in ts))}

    return {
        "trades": n,
        "total_net_pnl": _r(sum(t.net_pnl for t in trades)),
        "total_gross_pnl": _r(sum(t.gross_pnl for t in trades)),
        "total_charges": _r(sum(t.charges for t in trades)),
        "win_rate": _r(len(wins) / n * 100) if n else None,
        "loss_rate": _r(len(losses) / n * 100) if n else None,
        "profit_factor": _r(gross_win / gross_loss) if gross_loss > 0 else None,
        "average_win": _r(gross_win / len(wins)) if wins else None,
        "average_loss": _r(-gross_loss / len(losses)) if losses else None,
        "expectancy": _r(sum(t.net_pnl for t in trades) / n) if n else None,
        "max_drawdown": _r(max_dd),
        "max_drawdown_pct": _r(max_dd_pct),
        "sharpe": _r(mean / sd * math.sqrt(252)) if sd > 0 else None,
        "sortino": _r(mean / dsd * math.sqrt(252)) if dsd > 0 else None,
        "return_pct": _r(sum(t.net_pnl for t in trades) / capital * 100),
        "average_holding_minutes": _r(sum(t.minutes_held for t in trades) / n, 1) if n else None,
        "best_trade": _r(max((t.net_pnl for t in trades), default=None)),
        "worst_trade": _r(min((t.net_pnl for t in trades), default=None)),
        "average_mae_pct": _r(sum(t.mae_pct for t in trades) / n) if n else None,
        "average_mfe_pct": _r(sum(t.mfe_pct for t in trades) / n) if n else None,
        "exit_reasons": dict(Counter(t.exit_reason for t in trades)),
        "by_direction": {"LONG": side("LONG"), "SHORT": side("SHORT")},
        "trading_days": len(trading_days),
        "days_traded": len(daily),
        "daily_pnl": [{"date": d, "pnl": _r(daily.get(d, 0.0))} for d in trading_days],
        "monthly_pnl": [{"month": m, "pnl": _r(v)} for m, v in sorted(monthly.items())],
        "equity_curve": curve,
    }
