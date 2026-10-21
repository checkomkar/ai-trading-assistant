"""Shadow mode (PRD M5.7 step 4): on real market data, record the exact order a live broker WOULD send next to the
paper fill. Nothing here talks to a broker: it is arithmetic on a quote, and the result is only logged and shown."""

from __future__ import annotations

import math


def intended_live_entry(ask: float, tick_size: float, slippage_pct: float) -> float:
    """Marketable limit for an entry: the live ask plus a capped slippage allowance, rounded UP to the tick so the
    order is still marketable. Never an open-ended market order (PRD M5.4)."""
    if ask <= 0 or tick_size <= 0:
        raise ValueError("ask and tick_size must be positive")
    raw = ask * (1 + slippage_pct / 100)
    return round(math.ceil(raw / tick_size - 1e-9) * tick_size, 2)


def shadow_order(*, symbol: str, quantity: int, ask: float, tick_size: float, slippage_pct: float,
                 paper_price: float, correlation_id: str) -> dict:
    limit = intended_live_entry(ask, tick_size, slippage_pct)
    return {"sent": False, "side": "BUY", "order_type": "LIMIT", "symbol": symbol, "quantity": quantity,
            "limit_price": limit, "quote_ask": ask, "slippage_cap_pct": slippage_pct, "paper_price": paper_price,
            "worst_case_extra_cost": round((limit - paper_price) * quantity, 2), "correlation_id": correlation_id,
            "note": "Shadow only: this order was NOT sent to any broker."}
