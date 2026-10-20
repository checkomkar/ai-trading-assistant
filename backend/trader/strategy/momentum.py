"""Strategy #1 — intraday momentum / breakout, long options only (PRD §7.1-7.3).

LONG (buy ATM CE) when all hold on a closed bar:
    futures close > futures session VWAP, EMA9 > EMA21, RSI > 55, close > highest high of last N bars
SHORT (buy ATM PE) is the mirror: below VWAP, EMA9 < EMA21, RSI < 45, close < lowest low of last N bars.

Explicit no-trade states (§7.3): outside the entry window, choppy (low ADX), low volatility
(ATR% of price), event days, daily loss reached, existing position, max trades reached.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, time

from trader.strategy.base import Direction, MarketState, NoTrade, Signal, Strategy


@dataclass(frozen=True)
class MomentumParams:
    rsi_long: float = 55.0
    rsi_short: float = 45.0
    use_vwap: bool = True
    adx_min: float = 18.0  # below = choppy
    atr_pct_min: float = 0.04  # ATR as % of price; below = too quiet to pay for theta
    entry_start: time = time(9, 30)
    entry_end: time = time(14, 45)
    max_trades_per_day: int = 3
    stop_loss_pct: float = 25.0
    target_pct: float = 50.0
    event_days: tuple[date, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["entry_start"], d["entry_end"] = self.entry_start.isoformat(), self.entry_end.isoformat()
        d["event_days"] = [x.isoformat() for x in self.event_days]
        return d


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(self, params: MomentumParams | None = None) -> None:
        self.p = params or MomentumParams()

    def generate_signal(self, state: MarketState) -> Signal | NoTrade | None:
        f, p = state.features, self.p
        # Bars are stamped with their start time; the decision is made at the bar's close.
        if not (p.entry_start <= f.time_ist < p.entry_end):
            return NoTrade("outside trading window")
        if f.day in p.event_days:
            return NoTrade("near major event")
        if state.daily_loss_hit:
            return NoTrade("daily loss reached")
        if state.has_position:
            return NoTrade("existing position")
        if state.trades_today >= p.max_trades_per_day:
            return NoTrade("max trades per day reached")

        ema_f, ema_s, rsi, adx, atr_pct = f["ema_fast"], f["ema_slow"], f["rsi"], f["adx"], f["atr_pct"]
        hi, lo = f["breakout_high"], f["breakout_low"]
        if None in (ema_f, ema_s, rsi, adx, atr_pct, hi, lo):
            return None  # indicators still warming up
        if adx < p.adx_min:
            return NoTrade("choppy market")
        if atr_pct < p.atr_pct_min:
            return NoTrade("low volatility")

        vwap, fut = f["fut_vwap"], f["fut_close"]
        vwap_ok_long = vwap_ok_short = True
        if p.use_vwap:
            if vwap is None or fut is None:
                return NoTrade("no futures VWAP")
            vwap_ok_long, vwap_ok_short = fut > vwap, fut < vwap

        if vwap_ok_long and ema_f > ema_s and rsi > p.rsi_long and f.close > hi:
            return self._signal(state, Direction.LONG, rsi, adx,
                                f"close {f.close:.2f} > {hi:.2f}: EMA{ema_f:.1f}>{ema_s:.1f}, "
                                f"RSI {rsi:.1f}, ADX {adx:.1f}"
                                + (f", fut {fut:.1f} > VWAP {vwap:.1f}" if p.use_vwap else ""))
        if vwap_ok_short and ema_f < ema_s and rsi < p.rsi_short and f.close < lo:
            return self._signal(state, Direction.SHORT, rsi, adx,
                                f"close {f.close:.2f} < {lo:.2f}: EMA{ema_f:.1f}<{ema_s:.1f}, "
                                f"RSI {rsi:.1f}, ADX {adx:.1f}"
                                + (f", fut {fut:.1f} < VWAP {vwap:.1f}" if p.use_vwap else ""))
        return None

    def _signal(self, state: MarketState, d: Direction, rsi: float, adx: float, reason: str) -> Signal:
        # Deterministic confidence: stronger RSI displacement and trend strength -> higher.
        conf = 0.5 + min(abs(rsi - 50) - 5, 25) / 100 + min(max(adx - 20, 0), 30) / 150
        f = state.features
        return Signal(d, round(min(max(conf, 0.05), 0.95), 3), state.underlying, "ATM", f.close,
                      self.p.stop_loss_pct, self.p.target_pct, reason, f.ts, self.name,
                      {k: (round(v, 4) if isinstance(v, float) else v) for k, v in f.values.items()})
