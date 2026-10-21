"""Risk-based position sizing, shared by the backtester and the paper runner so both size a trade the same way."""

from __future__ import annotations

MAX_LOTS = 20  # a sanity ceiling, whatever the risk budget works out to


def lots_for_risk(capital: float, risk_pct: float, premium: float, stop_loss_pct: float, lot_size: int) -> int:
    """Whole lots such that hitting the stop loses at most `risk_pct`% of `capital`. 0 means even one lot risks more
    than the budget (the caller skips the trade rather than break the rule)."""
    loss_per_lot = premium * stop_loss_pct / 100 * lot_size
    if loss_per_lot <= 0:
        return 0
    return max(0, min(MAX_LOTS, int(capital * risk_pct / 100 // loss_per_lot)))
