"""Strategy interface and the standardised Signal (PRD §7, §12).

Strategies are pure: they read `MarketState` and return a `Signal` or a `NoTrade` reason.
They never size positions, place orders or know whether they run in a backtest or live.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from trader.features import Features
from trader.instruments.strikes import OptionType


class Direction(str, Enum):
    LONG = "LONG"  # buy CE
    SHORT = "SHORT"  # buy PE

    @property
    def option_type(self) -> OptionType:
        return OptionType.CE if self is Direction.LONG else OptionType.PE


@dataclass(frozen=True)
class Signal:
    direction: Direction
    confidence: float  # 0..1
    underlying: str
    option_selection: str  # "ATM", later "ITM1"/"OTM1"
    entry_ref: float  # underlying price at signal time
    stop_loss_pct: float  # on option premium
    target_pct: float  # on option premium
    reason: str
    timestamp: datetime
    strategy: str
    features: dict = field(default_factory=dict)  # snapshot for the audit trail (PRD §2.3)
    risk_pct: float | None = None  # size the trade so the stop loses this % of capital (None = the fixed lots)
    # (condition in English, True/False/None) for each entry condition and filter at the signal bar, for the chart
    checks: tuple = ()


@dataclass(frozen=True)
class NoTrade:
    reason: str


@dataclass
class MarketState:
    underlying: str
    features: Features
    has_position: bool = False
    trades_today: int = 0
    day_pnl: float = 0.0
    daily_loss_hit: bool = False


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def generate_signal(self, state: MarketState) -> Signal | NoTrade | None:
        """Called once per closed bar. None means "no setup", NoTrade means "explicitly blocked"."""

    def exit_signal(self, state: MarketState, direction: Direction) -> str | None:
        """Optional rule-based exit, checked on each closed bar while a position is open.
        A returned reason closes the position at the next bar's open."""
        return None
