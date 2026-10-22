"""Event-driven backtest engine (PRD §8, §14.5).

    bar close -> features -> strategy signal -> (risk) -> fill at NEXT bar open -> manage -> exit -> P&L

Correctness rules:
* Signals use only closed bars; entries fill at the next bar's open. No same-bar fills.
* Buys pay the ask (mid + half spread + slippage); sells receive the bid.
* If one bar touches both stop and target, the stop is assumed to hit first (conservative).
* A gap through the stop/target fills at the bar's open, not at the level.
* Positions are flat by the EOD exit time; nothing is carried overnight.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Protocol

from trader.backtest.costs import CostModel
from trader.backtest.pricing import Contract, ExecutionModel, PremiumBar
from trader.features import FeatureConfig, FeatureEngine
from trader.instruments.calendar import IST
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle
from trader.strategy.sizing import lots_for_risk
from trader.strategy.base import MarketState, NoTrade, Signal, Strategy


class Pricer(Protocol):
    mode: str

    def contract(self, underlying: str, option_type: OptionType, spot: float, day: date,
                 lot_size: int | None = None) -> Contract | None: ...

    def bar(self, c: Contract, u: Candle, tf: timedelta, vix: float | None) -> PremiumBar | None: ...


@dataclass(frozen=True)
class EngineConfig:
    lots: int = 1
    lot_size: int | None = None  # None -> contract / spec lot size
    max_daily_loss: float = 5000.0  # rupees, realised, after costs
    max_trades_per_day: int | None = None  # engine-level cap, on top of the strategy's own
    trail_activate_pct: float | None = 30.0  # start trailing once premium is up this much
    trail_pct: float | None = 15.0  # trail this far below the high-water premium
    max_hold_bars: int | None = None
    eod_exit: time = time(15, 15)
    capital: float = 100_000.0  # for return-based ratios only


@dataclass
class Trade:
    trade_id: str
    correlation_id: str
    contract: str
    direction: str
    signal_time: str
    signal_reason: str
    confidence: float
    entry_time: str
    entry_price: float
    exit_time: str
    exit_bar_time: str  # start of the bar in which the exit happened (for chart markers)
    exit_price: float
    exit_reason: str
    quantity: int
    gross_pnl: float
    charges: float
    net_pnl: float
    mae_pct: float  # worst adverse premium move while open, % of entry
    mfe_pct: float  # best favourable premium move while open, % of entry
    bars_held: int
    minutes_held: float
    underlying_entry: float
    underlying_exit: float
    features: dict = field(default_factory=dict)
    pricing: str = ""  # "real" (stored option candles) or "modelled" (Black-Scholes); set for every option trade


@dataclass
class _Position:
    signal: Signal
    contract: Contract
    quantity: int
    entry_time: datetime
    entry_price: float  # paid (ask)
    entry_mid: float
    stop: float
    target: float
    high_water: float
    low_water: float
    underlying_entry: float
    initial_stop: float
    last_mid: float
    bars: int = 0
    correlation_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


@dataclass
class BacktestResult:
    trades: list[Trade]
    no_trade_reasons: dict[str, int]
    signals: int
    skipped_entries: dict[str, int]
    bars: int
    days: int
    pricing_mode: str
    warnings: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["trades"] = [asdict(t) for t in self.trades]
        return d


class BacktestEngine:
    def __init__(self, underlying: str, strategy: Strategy, pricer: Pricer, costs: CostModel,
                 timeframe_minutes: int = 5, config: EngineConfig | None = None,
                 execution: ExecutionModel | None = None, features: FeatureConfig | None = None) -> None:
        self.u, self.strategy, self.pricer, self.costs = underlying, strategy, pricer, costs
        self.tf = timedelta(minutes=timeframe_minutes)
        self.cfg = config or EngineConfig()
        self.exe = execution or ExecutionModel()
        self.features = FeatureEngine(timeframe_minutes, features)

    def run(self, bars: list[Candle], futures: dict[datetime, Candle] | None = None,
            vix: dict[datetime, float] | None = None, trade_from: date | None = None) -> BacktestResult:
        """`bars` may begin before `trade_from`: those days only warm the indicators up (a 61-period Hull MA on 9-minute candles
        needs a day and a half of history); no signal, entry or day is counted before it."""
        futures, vix = futures or {}, vix or {}
        trades: list[Trade] = []
        no_trade: Counter[str] = Counter()
        skipped: Counter[str] = Counter()
        signals = 0
        pos: _Position | None = None
        pending: Signal | None = None
        pending_exit: str | None = None
        day: date | None = None
        trades_today, day_pnl, loss_hit = 0, 0.0, False
        last_vix: float | None = None
        days: set[date] = set()

        warm_bars = 0
        for i, bar in enumerate(bars):
            local = bar.ts.astimezone(IST)
            if trade_from is not None and local.date() < trade_from:
                v = vix.get(bar.ts)
                last_vix = v if v is not None else last_vix
                feats = self.features.update(bar, futures.get(bar.ts), v)
                self.strategy.generate_signal(MarketState(self.u, feats))  # keeps the strategy's own indicators warm
                warm_bars += 1
                continue
            if local.date() != day:
                day, trades_today, day_pnl, loss_hit = local.date(), 0, 0.0, False
                pending = None  # signals never carry across days
                days.add(day)
            nxt = bars[i + 1] if i + 1 < len(bars) else None
            last_bar_of_day = nxt is None or nxt.ts.astimezone(IST).date() != day
            v = vix.get(bar.ts)
            last_vix = v if v is not None else last_vix

            # 1) fill the previous bar's signal at this bar's open
            if pending is not None and pos is None:
                if local.time() >= self.cfg.eod_exit:
                    skipped["entry after EOD cutoff"] += 1
                else:
                    pos = self._open(pending, bar, last_vix, skipped)
                    if pos is not None:
                        trades_today += 1
                pending = None

            # 2a) a rule exit decided on the previous bar's close fills at this bar's open
            if pos is not None and pending_exit is not None:
                pb = self.pricer.bar(pos.contract, bar, self.tf, last_vix)
                if pb is not None:
                    closed = self._close(pos, bar.ts, bar.ts, pb.open, pending_exit, bar.open)
                    trades.append(closed)
                    day_pnl += closed.net_pnl
                    loss_hit = day_pnl <= -abs(self.cfg.max_daily_loss)
                    pos = None
                    pending_exit = None
            if pos is None:
                pending_exit = None

            # 2b) manage the open position through this bar
            if pos is not None:
                closed = self._manage(pos, bar, last_vix, last_bar_of_day)
                if closed is not None:
                    trades.append(closed)
                    day_pnl += closed.net_pnl
                    loss_hit = day_pnl <= -abs(self.cfg.max_daily_loss)
                    pos = None

            # 3) features at bar close, 4) strategy decision for the next bar
            feats = self.features.update(bar, futures.get(bar.ts), v)
            state = MarketState(self.u, feats, has_position=pos is not None, trades_today=trades_today,
                                day_pnl=day_pnl, daily_loss_hit=loss_hit)
            decision = self.strategy.generate_signal(state)
            if pos is not None and not last_bar_of_day:
                pending_exit = self.strategy.exit_signal(state, pos.signal.direction)
            if isinstance(decision, NoTrade):
                no_trade[decision.reason] += 1
            elif isinstance(decision, Signal):
                signals += 1
                # Risk limits are enforced here, independent of what the strategy checked (PRD §2.2).
                if loss_hit:
                    skipped["daily loss limit"] += 1
                elif self.cfg.max_trades_per_day is not None and trades_today >= self.cfg.max_trades_per_day:
                    skipped["max trades per day"] += 1
                elif pos is not None:
                    skipped["position already open"] += 1
                elif last_bar_of_day:
                    skipped["signal on last bar of day"] += 1
                else:
                    pending = decision

        warnings = self._warnings(trades)
        return BacktestResult(trades, dict(no_trade), signals, dict(skipped), len(bars) - warm_bars, len(days),
                              self.pricer.mode, warnings)

    # ---- position lifecycle -------------------------------------------------------------------
    def _open(self, sig: Signal, bar: Candle, vix: float | None, skipped: Counter) -> _Position | None:
        # ATM is chosen from the price at entry (this bar's open), as a live trader would.
        c = self.pricer.contract(self.u, sig.direction.option_type, bar.open, bar.ts.astimezone(IST).date(),
                                 self.cfg.lot_size)
        if c is None:
            skipped["no option data for contract"] += 1
            return None
        pb = self.pricer.bar(c, bar, self.tf, vix)
        if pb is None and (alt := getattr(self.pricer, "fallback", lambda _c: None)(c)) is not None:
            c = alt  # hybrid pricing: no stored print at the entry bar, so this trade is priced by the model throughout
            pb = self.pricer.bar(c, bar, self.tf, vix)
        if pb is None:
            skipped["no option print at entry bar"] += 1
            return None
        mid = pb.open
        paid = self.exe.buy(mid)
        lots = self.cfg.lots
        if sig.risk_pct:
            lots = lots_for_risk(self.cfg.capital, sig.risk_pct, paid, sig.stop_loss_pct, c.lot_size)
            if lots == 0:
                skipped["risk budget below one lot"] += 1
                return None
        stop = paid * (1 - sig.stop_loss_pct / 100)
        target = paid * (1 + sig.target_pct / 100)
        return _Position(sig, c, lots * c.lot_size, bar.ts, paid, mid, stop, target, mid, mid, bar.open,
                         initial_stop=stop, last_mid=mid)

    def _manage(self, p: _Position, bar: Candle, vix: float | None, last_bar_of_day: bool) -> Trade | None:
        pb = self.pricer.bar(p.contract, bar, self.tf, vix)
        local_t = bar.ts.astimezone(IST).time()
        end = bar.ts + self.tf
        if pb is None:
            # No print this bar (real data): we can only exit at EOD, at the last traded premium.
            if last_bar_of_day or local_t >= self.cfg.eod_exit:
                return self._close(p, bar.ts, end, p.last_mid, "eod (no print)", bar.close)
            return None
        p.bars += 1

        if local_t >= self.cfg.eod_exit:
            return self._close(p, bar.ts, bar.ts, pb.open, "eod", bar.open)

        # Gaps through levels fill at the open, not at the level.
        if pb.open <= p.stop:
            return self._close(p, bar.ts, bar.ts, pb.open, "stop (gap)", bar.open)
        if pb.open >= p.target:
            return self._close(p, bar.ts, bar.ts, pb.open, "target (gap)", bar.open)

        p.low_water = min(p.low_water, pb.low)
        if pb.low <= p.stop:  # also when the target was touched too: assume the worse outcome
            reason = "trailing stop" if p.stop > p.initial_stop + 1e-9 else "stop"
            return self._close(p, bar.ts, end, p.stop, reason, bar.close)
        if pb.high >= p.target:
            p.high_water = max(p.high_water, pb.high)
            return self._close(p, bar.ts, end, p.target, "target", bar.close)
        p.last_mid = pb.close

        p.high_water = max(p.high_water, pb.high)
        c = self.cfg
        if c.trail_activate_pct is not None and c.trail_pct is not None \
                and p.high_water >= p.entry_price * (1 + c.trail_activate_pct / 100):
            p.stop = max(p.stop, p.high_water * (1 - c.trail_pct / 100))  # applies from the next bar

        if c.max_hold_bars is not None and p.bars >= c.max_hold_bars:
            return self._close(p, bar.ts, end, pb.close, "time exit", bar.close)
        if last_bar_of_day:
            return self._close(p, bar.ts, end, pb.close, "eod", bar.close)
        return None

    def _close(self, p: _Position, bar_ts: datetime, exit_ts: datetime, mid_level: float, reason: str,
               underlying_px: float) -> Trade:
        received = self.exe.sell(mid_level)
        d_entry = p.entry_time.astimezone(IST).date()
        d_exit = exit_ts.astimezone(IST).date()
        ch = (self.costs.order_charges(d_entry, p.entry_price, p.quantity, "BUY").total
              + self.costs.order_charges(d_exit, received, p.quantity, "SELL").total)
        gross = (received - p.entry_price) * p.quantity
        return Trade(
            trade_id=uuid.uuid4().hex[:10], correlation_id=p.correlation_id, contract=p.contract.label,
            direction=p.signal.direction.value, signal_time=p.signal.timestamp.isoformat(),
            signal_reason=p.signal.reason, confidence=p.signal.confidence,
            entry_time=p.entry_time.isoformat(), entry_price=round(p.entry_price, 2),
            exit_time=exit_ts.isoformat(), exit_bar_time=bar_ts.isoformat(), exit_price=round(received, 2),
            exit_reason=reason,
            quantity=p.quantity, gross_pnl=round(gross, 2), charges=round(ch, 2), net_pnl=round(gross - ch, 2),
            mae_pct=round((min(p.low_water, mid_level) / p.entry_price - 1) * 100, 2),
            mfe_pct=round((max(p.high_water, mid_level) / p.entry_price - 1) * 100, 2),
            bars_held=p.bars, minutes_held=(exit_ts - p.entry_time).total_seconds() / 60,
            underlying_entry=round(p.underlying_entry, 2), underlying_exit=round(underlying_px, 2),
            features=p.signal.features, pricing="modelled" if p.contract.synthetic or self.pricer.mode == "synthetic" else "real",
        )

    def _warnings(self, trades: list[Trade]) -> list[str]:
        w: list[str] = []
        if self.pricer.mode == "hybrid":
            n_real = sum(1 for t in trades if t.pricing == "real")
            w.append(f"Option prices are a MIX: {n_real} trade(s) used stored real option candles and {len(trades) - n_real} used the "
                     "Black-Scholes model (IV = INDIA VIX x multiplier) because no stored candle covered that contract at the entry. "
                     "Modelled trades are optimistic about liquidity and fills; see each trade's source in the table.")
            missing = getattr(self.pricer, "missing_vix_bars", 0)
            if missing:
                w.append(f"{missing} bars had no VIX; a default IV of {self.pricer.default_iv:.0%} was used.")
        if self.pricer.mode == "synthetic":
            w.append("Option premiums are SYNTHETIC (Black-Scholes, IV = INDIA VIX x multiplier, modelled spread). "
                     "Real fills, IV skew and liquidity are not captured; treat results as optimistic.")
            missing = getattr(self.pricer, "missing_vix_bars", 0)
            if missing:
                w.append(f"{missing} bars had no VIX; a default IV of {self.pricer.default_iv:.0%} was used.")  # type: ignore[attr-defined]
            old = getattr(self.pricer, "unverified_expiry_dates", set())
            if old:
                w.append(f"{len(old)} trade dates predate 2025-09-01; historical expiry weekdays for those "
                         "dates are best-effort assumptions.")
        missing_c = getattr(self.pricer, "missing_contracts", set()) if self.pricer.mode != "hybrid" else set()
        if missing_c:
            w.append(f"{len(missing_c)} signals had no stored option data and were skipped. Choose \"Real where stored, else modelled\" "
                     "to trade those with modelled prices instead (each trade is labelled with the source it used).")
        if not trades:
            w.append("No trades were taken in this period.")
        return w
