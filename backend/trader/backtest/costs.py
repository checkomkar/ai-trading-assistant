"""Versioned transaction-cost model (PRD §8.2, §14.5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from trader.config import CONFIG_DIR

CRORE = 1e7


@dataclass(frozen=True)
class CostSchedule:
    effective_from: date
    brokerage_per_order: float
    stt_sell_pct: float
    exchange_txn_pct: float
    clearing_pct: float
    sebi_per_crore: float
    ipft_per_crore: float
    stamp_buy_pct: float
    gst_pct: float
    note: str = ""


@dataclass(frozen=True)
class Charges:
    brokerage: float
    stt: float
    exchange: float
    clearing: float
    sebi: float
    ipft: float
    stamp: float
    gst: float

    @property
    def total(self) -> float:
        return (self.brokerage + self.stt + self.exchange + self.clearing + self.sebi
                + self.ipft + self.stamp + self.gst)


class CostModel:
    def __init__(self, schedules: list[CostSchedule]) -> None:
        if not schedules:
            raise ValueError("at least one cost schedule is required")
        self.schedules = sorted(schedules, key=lambda s: s.effective_from)

    @classmethod
    def from_yaml(cls, path: Path) -> "CostModel":
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return cls([CostSchedule(**{**v, "effective_from": _d(v["effective_from"])}) for v in raw["versions"]])

    def schedule_for(self, d: date) -> CostSchedule:
        applicable = [s for s in self.schedules if s.effective_from <= d]
        if not applicable:
            # Never price an old trade with a later schedule (STT alone changed three times since 2019).
            raise ValueError(f"no cost schedule covers {d}: the earliest in costs.yaml starts "
                             f"{self.schedules[0].effective_from}; add an older version")
        return applicable[-1]

    def order_charges(self, trade_date: date, premium: float, quantity: int, side: str) -> Charges:
        """Charges for one executed option order. `side` is "BUY" or "SELL"; quantity in units (lots x lot size)."""
        s = self.schedule_for(trade_date)
        turnover = premium * quantity
        brokerage = s.brokerage_per_order if turnover > 0 else 0.0
        exchange = turnover * s.exchange_txn_pct / 100
        clearing = turnover * s.clearing_pct / 100
        sebi = turnover * s.sebi_per_crore / CRORE
        ipft = turnover * s.ipft_per_crore / CRORE
        stt = turnover * s.stt_sell_pct / 100 if side == "SELL" else 0.0
        stamp = turnover * s.stamp_buy_pct / 100 if side == "BUY" else 0.0
        gst = (brokerage + exchange + clearing + sebi + ipft) * s.gst_pct / 100
        return Charges(*(round(x, 4) for x in (brokerage, stt, exchange, clearing, sebi, ipft, stamp, gst)))


def _d(v: object) -> date:
    return v if isinstance(v, date) else date.fromisoformat(str(v))


@lru_cache
def default_cost_model() -> CostModel:
    return CostModel.from_yaml(CONFIG_DIR / "costs.yaml")
