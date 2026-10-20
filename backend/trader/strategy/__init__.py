"""Strategy engine (PRD Phase 7)."""

from trader.strategy.base import Direction, MarketState, NoTrade, Signal, Strategy
from trader.strategy.momentum import MomentumParams, MomentumStrategy

STRATEGIES = {"momentum": MomentumStrategy}

__all__ = ["Direction", "MarketState", "NoTrade", "Signal", "Strategy", "MomentumParams", "MomentumStrategy",
           "STRATEGIES"]
