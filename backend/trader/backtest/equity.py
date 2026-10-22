"""Daily-bar engine for stock (cash equity, delivery) strategies.

    day's close -> features -> strategy rules -> order for the NEXT day's open -> manage -> exit -> P&L

Same correctness rules as the option engine:
* Rules use only closed daily bars; an entry or rule exit fills at the next day's open.
* If one day's range touches both the stop and the target, the stop is assumed to hit first.
* A gap through the stop or target fills at the open, not at the level.
* A trailing stop only ratchets up after the bar that set the new high (no same-bar benefit).
* Costs are delivery charges on both legs; buys pay slippage up, sells receive it down.

Long only. Capital is split equally across `max_positions` slots (optionally less, when sizing by risk). When more
stocks signal than there are free slots, they are taken in alphabetical order, which is arbitrary but deterministic.
The whole run is a pure function of the stored bars, so a paper portfolio is simply a run that starts on its start
date and ends on the latest stored day.
"""

from __future__ import annotations

import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from trader.backtest.engine import Trade
from trader.backtest.equity_costs import EquityCosts
from trader.features import FeatureEngine
from trader.instruments import equities as eq
from trader.instruments.calendar import IST
from trader.market_data.candles import Candle
from trader.strategy.base import Direction, MarketState
from trader.strategy.rule_strategy import RuleStrategy
from trader.strategy.spec import StrategySpec

BAR_MINUTES = 375
OPEN_T, CLOSE_T = time(9, 15), time(15, 30)


@dataclass(frozen=True)
class EquityConfig:
    capital: float = 100_000.0
    slippage_pct: float = 0.05  # per side
    costs: EquityCosts = field(default_factory=EquityCosts)
    quantity: int | None = None  # fixed shares per trade; None = size from capital (and risk)


@dataclass
class Position:
    symbol: str
    quantity: int
    signal_day: date
    entry_day: date
    entry_price: float
    entry_charges: float
    stop: float
    target: float
    high_water: float
    reason: str
    features: dict
    lowest: float
    highest: float
    bars: int = 0
    pending_exit: str | None = None
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class _Symbol:
    strategy: RuleStrategy
    fe: FeatureEngine
    pos: Position | None = None
    pending_entry: dict | None = None  # {"reason", "features", "day"}
    last: Candle | None = None


@dataclass
class EquityResult:
    trades: list[Trade]
    open_positions: list[Position]
    pending_entries: list[dict]  # orders that would go in at the next open: {"symbol", "reason"}
    pending_exits: list[dict]
    equity_curve: list[dict]  # [{"date", "equity", "cash", "positions"}]
    days: list[str]  # live trading days
    signals: int
    skipped: dict[str, int]
    no_data: list[str]
    last_close: dict[str, float]
    warnings: list[str]


def _iso(d: date, t: time) -> str:
    return datetime.combine(d, t, tzinfo=IST).astimezone(timezone.utc).isoformat()


class EquityEngine:
    def __init__(self, spec: StrategySpec, config: EquityConfig | None = None) -> None:
        if spec.market != "equity":
            raise ValueError('EquityEngine needs a stock (market "equity") strategy')
        self.spec = spec
        self.cfg = config or EquityConfig()
        e, r = spec.exits, spec.risk
        self.stop_pct, self.target_pct = e.stop_loss_pct, e.target_pct
        self.trail = e.trail
        self.time_exit = e.time_exit_bars
        self.max_positions = r.max_positions
        self.risk_pct = r.risk_per_trade_pct
        self.entry_cond = RuleStrategy(spec).entries[Direction.LONG]

    # ---- helpers ------------------------------------------------------------------------------
    def _new_symbol(self) -> _Symbol:
        return _Symbol(RuleStrategy(self.spec), FeatureEngine(BAR_MINUTES))

    def _slip_up(self, p: float) -> float:
        return p * (1 + self.cfg.slippage_pct / 100)

    def _slip_down(self, p: float) -> float:
        return p * (1 - self.cfg.slippage_pct / 100)

    def _qty(self, price: float, cash: float) -> int:
        if self.cfg.quantity:  # the trader chose the size: all or nothing, never a smaller position than asked
            q = self.cfg.quantity
            return q if q * price + self.cfg.costs.order_charges(price, q, "BUY") <= cash else 0
        slot = self.cfg.capital / self.max_positions
        qty = math.floor(slot / price)
        if self.risk_pct:
            risk_qty = math.floor(self.cfg.capital * self.risk_pct / 100 / (price * self.stop_pct / 100))
            qty = min(qty, risk_qty)
        while qty > 0 and qty * price + self.cfg.costs.order_charges(price, qty, "BUY") > cash:
            qty -= 1
        return qty

    def _close(self, st: _Symbol, day: date, price: float, reason: str, bar: Candle, at: time) -> Trade:
        p = st.pos
        assert p is not None
        charges = p.entry_charges + self.cfg.costs.order_charges(price, p.quantity, "SELL")
        gross = (price - p.entry_price) * p.quantity
        t = Trade(
            trade_id=uuid.uuid4().hex[:12], correlation_id=p.correlation_id, contract=p.symbol, direction="LONG",
            signal_time=_iso(p.signal_day, CLOSE_T), signal_reason=p.reason, confidence=0.5,
            entry_time=_iso(p.entry_day, OPEN_T), entry_price=round(p.entry_price, 2), exit_time=_iso(day, at),
            exit_bar_time=bar.ts.isoformat(), exit_price=round(price, 2), exit_reason=reason, quantity=p.quantity,
            gross_pnl=round(gross, 2), charges=round(charges, 2), net_pnl=round(gross - charges, 2),
            mae_pct=round((p.lowest / p.entry_price - 1) * 100, 2), mfe_pct=round((p.highest / p.entry_price - 1) * 100, 2),
            bars_held=p.bars, minutes_held=float(p.bars * BAR_MINUTES), underlying_entry=round(p.entry_price, 2),
            underlying_exit=round(price, 2), features=p.features)
        st.pos = None
        return t

    def _entry_ok(self, strat: RuleStrategy) -> bool | None:
        for c in self.spec.filters.all:
            r = strat.eval(c)
            if r is not True:
                return r
        return strat.eval(self.entry_cond) if self.entry_cond is not None else False

    # ---- the run --------------------------------------------------------------------------------
    def run(self, bars: dict[str, list[Candle]], start: date, end: date | None = None) -> EquityResult:
        """`bars` may start well before `start`: those days only warm the indicators up. Nothing trades before `start`."""
        by_day: dict[date, dict[str, Candle]] = defaultdict(dict)
        for sym, cs in bars.items():
            for c in cs:
                d = eq.day_of(c.ts)
                if end is None or d <= end:
                    by_day[d][sym] = c
        states = {sym: self._new_symbol() for sym in bars}
        cash = self.cfg.capital
        trades: list[Trade] = []
        curve: list[dict] = []
        live_days: list[str] = []
        signals = 0
        skipped: Counter[str] = Counter()
        last_close: dict[str, float] = {}

        for d in sorted(by_day):
            today = by_day[d]
            live = d >= start
            order = sorted(today)
            if live:
                live_days.append(d.isoformat())
                # 1. opening auction: exits first (they free slots and cash), then entries
                for sym in order:
                    st, bar = states[sym], today[sym]
                    p = st.pos
                    if p is None:
                        continue
                    if p.pending_exit:
                        px, why = self._slip_down(bar.open), p.pending_exit
                    elif bar.open <= p.stop:
                        px, why = self._slip_down(bar.open), "stop loss (gap)"
                    elif bar.open >= p.target:
                        px, why = bar.open, "target (gap)"
                    else:
                        continue
                    cash += p.quantity * px - self.cfg.costs.order_charges(px, p.quantity, "SELL")
                    trades.append(self._close(st, d, px, why, bar, OPEN_T))
                held = sum(1 for s in states.values() if s.pos)
                for sym in order:
                    st, bar = states[sym], today[sym]
                    pe = st.pending_entry
                    if pe is None:
                        continue
                    st.pending_entry = None
                    if st.pos is not None:
                        continue
                    if held >= self.max_positions:
                        skipped["no free position slot"] += 1
                        continue
                    px = self._slip_up(bar.open)
                    qty = self._qty(px, cash)
                    if qty < 1:
                        skipped["not enough cash for the chosen quantity" if self.cfg.quantity else
                                "one share costs more than the position size" if self.cfg.capital / self.max_positions < px
                                else "not enough cash or risk budget"] += 1
                        continue
                    ch = self.cfg.costs.order_charges(px, qty, "BUY")
                    cash -= qty * px + ch
                    st.pos = Position(sym, qty, pe["day"], d, px, ch, px * (1 - self.stop_pct / 100),
                                      px * (1 + self.target_pct / 100), px, pe["reason"], pe["features"], px, px)
                    held += 1
                # 2. through the day: stop, target, trail
                for sym in order:
                    st, bar = states[sym], today[sym]
                    p = st.pos
                    if p is None:
                        continue
                    p.lowest, p.highest = min(p.lowest, bar.low), max(p.highest, bar.high)
                    p.bars += 1
                    if bar.low <= p.stop:  # stop first when both are touched
                        px = self._slip_down(p.stop)
                        cash += p.quantity * px - self.cfg.costs.order_charges(px, p.quantity, "SELL")
                        trades.append(self._close(st, d, px, "stop loss", bar, CLOSE_T))
                        continue
                    if bar.high >= p.target:
                        cash += p.quantity * p.target - self.cfg.costs.order_charges(p.target, p.quantity, "SELL")
                        trades.append(self._close(st, d, p.target, "target", bar, CLOSE_T))
                        continue
                    p.high_water = max(p.high_water, bar.high)
                    if self.trail and p.high_water >= p.entry_price * (1 + self.trail.activate_pct / 100):
                        p.stop = max(p.stop, p.high_water * (1 - self.trail.trail_pct / 100))
                    if self.time_exit and p.bars >= self.time_exit and not p.pending_exit:
                        p.pending_exit = f"time exit after {p.bars} days"
            # 3. the close: update indicators with the finished day, then decide for tomorrow
            for sym in order:
                st, bar = states[sym], today[sym]
                f = st.fe.update(bar, future=bar)  # the stock's own volume plays the role futures volume has for indices
                st.last = bar
                last_close[sym] = bar.close
                state = MarketState(sym, f, has_position=st.pos is not None)
                st.strategy._observe(state)
                if not live:
                    continue
                if st.pos is not None:
                    if not st.pos.pending_exit:
                        why = st.strategy.exit_signal(state, Direction.LONG)
                        if why:
                            st.pos.pending_exit = why
                    continue
                if self._entry_ok(st.strategy) is True:
                    signals += 1
                    snap = {f"{k[0]}{'' if k[1] is None else k[1]}": (round(v, 4) if isinstance(v, float) else v)
                            for k, v in (st.strategy.hist[0] if st.strategy.hist else {}).items()}
                    st.pending_entry = {"day": d, "features": snap, "reason": f"{self.spec.name}: entry rules met"}
                else:
                    st.pending_entry = None
            if live:
                mtm = sum(s.pos.quantity * s.last.close for s in states.values() if s.pos and s.last)
                curve.append({"date": d.isoformat(), "equity": round(cash + mtm, 2), "cash": round(cash, 2),
                              "positions": sum(1 for s in states.values() if s.pos)})

        warnings: list[str] = []
        no_data = sorted(s for s, c in bars.items() if not c)
        if no_data:
            warnings.append(f"No daily candles stored for {len(no_data)} stock(s): {', '.join(no_data[:8])}"
                            f"{'...' if len(no_data) > 8 else ''}")
        return EquityResult(
            trades=trades, open_positions=[s.pos for s in states.values() if s.pos],
            pending_entries=[{"symbol": sym, "reason": s.pending_entry["reason"]} for sym, s in sorted(states.items())
                             if s.pending_entry],
            pending_exits=[{"symbol": sym, "reason": s.pos.pending_exit} for sym, s in sorted(states.items())
                           if s.pos and s.pos.pending_exit],
            equity_curve=curve, days=live_days, signals=signals, skipped=dict(skipped), no_data=no_data,
            last_close=last_close, warnings=warnings)

    def finish_open(self, res: EquityResult, bars: dict[str, list[Candle]]) -> list[Trade]:
        """Close the positions still open at the end of a backtest at the last close, flagged as such."""
        out: list[Trade] = []
        for p in res.open_positions:
            last = bars[p.symbol][-1]
            st = _Symbol(RuleStrategy(self.spec), FeatureEngine(BAR_MINUTES), pos=p)
            out.append(self._close(st, eq.day_of(last.ts), self._slip_down(last.close),
                                   "still open at the end of the test", last, CLOSE_T))
        return out
