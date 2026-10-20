"""Deterministic risk engine (PRD Phase 10, M4).

Every entry order passes through `RiskEngine.check` before it reaches a broker. The check is a
pure function of the intent, the engine's own counters and the supplied context, with no
randomness and no model calls. Strategies cannot bypass it: they only emit signals, and the
runner owns the only path to the broker.

* Entries are blocked by: kill switch, disabled strategy, closed market, invalid instrument or
  quantity, stale data, insufficient funds, daily loss, consecutive losses, trades per day, open
  positions, per-trade risk, lots per trade, underlying exposure and total premium exposure.
* Exits (`reduce_only`) are never blocked: the kill switch stops new risk, it does not trap you in it.
* All checks are evaluated and returned (not just the first failure), so the audit trail shows
  exactly which limits were near.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class RiskLimits:
    max_daily_loss: float = 5000.0  # rupees, realised + unrealised, after costs
    max_consecutive_losses: int = 3
    max_trades_per_day: int = 5
    max_open_positions: int = 1
    max_lots_per_trade: int = 1
    max_risk_per_trade: float = 4000.0  # premium x quantity x stop-loss %
    max_premium_exposure: float = 60_000.0  # total premium at risk across open positions
    max_underlying_exposure: float = 40_000.0  # premium on one underlying (NIFTY / BANKNIFTY)
    max_data_age_seconds: float = 10.0  # underlying and option quote must be at least this fresh


@dataclass(frozen=True)
class OrderIntent:
    strategy: str
    underlying: str
    token: str
    symbol: str
    quantity: int
    lot_size: int
    price: float  # expected fill price (live ask)
    stop_loss_pct: float
    correlation_id: str
    reduce_only: bool = False  # an exit


@dataclass(frozen=True)
class RiskContext:
    now: datetime
    market_open: bool
    instrument_valid: bool
    underlying_age_s: float | None  # seconds since the underlying last ticked
    option_age_s: float | None
    available_cash: float
    unrealized_pnl: float = 0.0


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass
class RiskDecision:
    approved: bool
    checks: list[Check] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        return [f"{c.name}: {c.detail}" for c in self.checks if not c.passed]

    def to_dict(self) -> dict:
        return {"approved": self.approved, "checks": [asdict(c) for c in self.checks]}


@dataclass
class KillSwitch:
    active: bool = False
    reason: str = ""
    since: datetime | None = None


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self.kill_switch = KillSwitch()
        self.disabled_strategies: set[str] = set()
        self.day: date | None = None
        self.trades_today = 0
        self.day_pnl = 0.0
        self.consecutive_losses = 0
        self._open: dict[str, tuple[str, float]] = {}  # correlation_id -> (underlying, premium)

    # ---- controls ---------------------------------------------------------------------
    def trip_kill_switch(self, reason: str, now: datetime) -> None:
        if not self.kill_switch.active:
            self.kill_switch = KillSwitch(True, reason, now)

    def reset_kill_switch(self) -> None:
        self.kill_switch = KillSwitch()

    def set_strategy_enabled(self, name: str, enabled: bool) -> None:
        (self.disabled_strategies.discard if enabled else self.disabled_strategies.add)(name)

    # ---- state ------------------------------------------------------------------------
    def roll_day(self, day: date) -> None:
        if day != self.day:
            self.day, self.trades_today, self.day_pnl, self.consecutive_losses = day, 0, 0.0, 0

    def on_entry(self, correlation_id: str, underlying: str, premium: float) -> None:
        self._open[correlation_id] = (underlying, premium)
        self.trades_today += 1

    def update_entry(self, correlation_id: str, premium: float) -> None:
        """Replace the estimated premium with the actual fill."""
        if correlation_id in self._open:
            self._open[correlation_id] = (self._open[correlation_id][0], premium)

    def on_entry_failed(self, correlation_id: str) -> None:
        """The entry order was rejected or cancelled: release what on_entry reserved."""
        if self._open.pop(correlation_id, None) is not None:
            self.trades_today = max(0, self.trades_today - 1)

    def on_exit(self, correlation_id: str, net_pnl: float) -> None:
        self._open.pop(correlation_id, None)
        self.day_pnl += net_pnl
        self.consecutive_losses = self.consecutive_losses + 1 if net_pnl < 0 else 0

    @property
    def open_positions(self) -> int:
        return len(self._open)

    def exposure(self, underlying: str | None = None) -> float:
        return sum(p for u, p in self._open.values() if underlying is None or u == underlying)

    # ---- the pre-order checklist (PRD §10.5) -----------------------------------------------
    def check(self, i: OrderIntent, ctx: RiskContext) -> RiskDecision:
        if i.reduce_only:
            return RiskDecision(True, [Check("reduce_only", True, "exits are never blocked")])
        L = self.limits
        premium = i.price * i.quantity
        risk = premium * i.stop_loss_pct / 100
        lots = i.quantity // max(i.lot_size, 1)
        live_pnl = self.day_pnl + ctx.unrealized_pnl
        stale = [f"{n} {'missing' if a is None else f'{a:.0f}s'}" for n, a in (("underlying", ctx.underlying_age_s), ("option", ctx.option_age_s))
                 if a is None or a > L.max_data_age_seconds]
        cs = [
            Check("kill_switch", not self.kill_switch.active,
                  f"STOP active: {self.kill_switch.reason}" if self.kill_switch.active else "off"),
            Check("strategy_enabled", i.strategy not in self.disabled_strategies,
                  f"{i.strategy} disabled" if i.strategy in self.disabled_strategies else "enabled"),
            Check("market_open", ctx.market_open, "open" if ctx.market_open else "market is closed"),
            Check("instrument_valid", ctx.instrument_valid and i.price > 0,
                  "ok" if ctx.instrument_valid and i.price > 0 else "unknown instrument or no price"),
            Check("quantity_valid", i.quantity > 0 and i.quantity % max(i.lot_size, 1) == 0,
                  f"{i.quantity} units, lot size {i.lot_size}"),
            Check("data_fresh", not stale,
                  "stale or missing: " + ", ".join(stale) if stale else f"within {L.max_data_age_seconds:g}s"),
            Check("margin", premium <= ctx.available_cash,
                  f"premium {premium:,.0f} vs cash {ctx.available_cash:,.0f}"),
            Check("daily_loss", live_pnl > -L.max_daily_loss, f"day P&L {live_pnl:,.0f} vs -{L.max_daily_loss:,.0f}"),
            Check("consecutive_losses", self.consecutive_losses < L.max_consecutive_losses,
                  f"{self.consecutive_losses} in a row vs max {L.max_consecutive_losses}"),
            Check("trades_per_day", self.trades_today < L.max_trades_per_day,
                  f"{self.trades_today} vs max {L.max_trades_per_day}"),
            Check("open_positions", self.open_positions < L.max_open_positions,
                  f"{self.open_positions} open vs max {L.max_open_positions}"),
            Check("lots_per_trade", lots <= L.max_lots_per_trade, f"{lots} lots vs max {L.max_lots_per_trade}"),
            Check("risk_per_trade", risk <= L.max_risk_per_trade, f"risk {risk:,.0f} vs max {L.max_risk_per_trade:,.0f}"),
            Check("underlying_exposure", self.exposure(i.underlying) + premium <= L.max_underlying_exposure,
                  f"{i.underlying} {self.exposure(i.underlying) + premium:,.0f} vs max {L.max_underlying_exposure:,.0f}"),
            Check("premium_exposure", self.exposure() + premium <= L.max_premium_exposure,
                  f"total {self.exposure() + premium:,.0f} vs max {L.max_premium_exposure:,.0f}"),
        ]
        return RiskDecision(all(c.passed for c in cs), cs)

    def status(self) -> dict:
        L = self.limits
        return {
            "kill_switch": {"active": self.kill_switch.active, "reason": self.kill_switch.reason,
                            "since": self.kill_switch.since.isoformat() if self.kill_switch.since else None},
            "disabled_strategies": sorted(self.disabled_strategies),
            "day": self.day.isoformat() if self.day else None,
            "limits": asdict(L),
            "usage": {
                "day_pnl": round(self.day_pnl, 2), "trades_today": self.trades_today,
                "consecutive_losses": self.consecutive_losses, "open_positions": self.open_positions,
                "premium_exposure": round(self.exposure(), 2),
                "underlying_exposure": {u: round(self.exposure(u), 2) for u in ("NIFTY", "BANKNIFTY")},
            },
        }
