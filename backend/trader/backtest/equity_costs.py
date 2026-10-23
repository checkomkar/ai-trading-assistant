"""Transaction costs for delivery (CNC) trades in NSE cash equities.

Statutory rates as published for FY 2025-26 (they change by circular, so check them against a contract note before
relying on the exact figures; the model is deliberately a little pessimistic: it charges both legs).
Brokerage defaults to 0, which is what most discount brokers charge on delivery; set it if yours charges.
"""

from __future__ import annotations

from dataclasses import dataclass

CRORE = 1e7


@dataclass(frozen=True)
class EquityCosts:
    brokerage_per_order: float = 0.0
    stt_pct: float = 0.1  # on both buy and sell for delivery
    exchange_txn_pct: float = 0.00297  # NSE
    sebi_per_crore: float = 10.0
    ipft_pct: float = 0.0001
    stamp_buy_pct: float = 0.015
    gst_pct: float = 18.0  # on brokerage + exchange + SEBI fees
    dp_charge_per_sell: float = 15.34  # depository charge per stock per sell day, incl. GST

    def order_charges(self, price: float, quantity: int, side: str) -> float:
        """Total rupees of charges for one executed delivery order. `side` is "BUY" or "SELL"."""
        turnover = price * quantity
        if turnover <= 0:
            return 0.0
        exchange = turnover * self.exchange_txn_pct / 100
        sebi = turnover * self.sebi_per_crore / CRORE
        ipft = turnover * self.ipft_pct / 100
        gst = (self.brokerage_per_order + exchange + sebi) * self.gst_pct / 100
        stt = turnover * self.stt_pct / 100
        stamp = turnover * self.stamp_buy_pct / 100 if side == "BUY" else 0.0
        dp = self.dp_charge_per_sell if side == "SELL" else 0.0
        return round(self.brokerage_per_order + exchange + sebi + ipft + gst + stt + stamp + dp, 4)
