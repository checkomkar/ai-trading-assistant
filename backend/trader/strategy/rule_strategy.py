"""RuleStrategy: deterministic interpreter for a StrategySpec (PRD §19.5).

Conditions are evaluated with three-valued logic: an operand that is still warming up (None)
makes a comparison *unknown*, and unknown never triggers an entry. Values are snapshotted per
bar so `shift` and `crosses` look only at closed bars at or before the current one.
"""

from __future__ import annotations

from collections import deque
from datetime import date, time

from trader.indicators import ADX, ATR, EMA, MACD, ROC, RSI, SMA, BollingerBands, RollingExtreme
from trader.strategy.base import Direction, MarketState, NoTrade, Signal, Strategy
from trader.strategy.spec import (
    All,
    Any_,
    Between,
    Cmp,
    Const,
    Cross,
    Ind,
    Not,
    StrategySpec,
    render_condition,
    resolved_entry,
    mirror_condition,
    walk_operands,
)

FEATURE_OPERANDS = {  # operand -> Features key (computed by the shared FeatureEngine)
    "fut_close": "fut_close", "vwap": "fut_vwap", "vix": "vix", "or_high": "or_high", "or_low": "or_low",
    "prev_day_high": "prev_day_high", "prev_day_low": "prev_day_low", "prev_day_close": "prev_day_close",
}
PRICE_OPERANDS = ("open", "high", "low", "close")


class _Bank:
    """Indicators requested by a spec, keyed by (name, period); updated once per bar."""

    def __init__(self, keys: set[tuple[str, int | None]]) -> None:
        self.keys = keys
        self.objs: dict[tuple[str, int | None], object] = {}
        for name, p in keys:
            k = (name, p)
            if name == "ema":
                self.objs[k] = EMA(p)
            elif name == "sma":
                self.objs[k] = SMA(p)
            elif name == "rsi":
                self.objs[k] = RSI(p)
            elif name in ("atr", "atr_pct"):
                self.objs[("atr", p)] = self.objs.get(("atr", p)) or ATR(p)
            elif name in ("adx", "plus_di", "minus_di"):
                self.objs[("adx", p)] = self.objs.get(("adx", p)) or ADX(p)
            elif name == "roc":
                self.objs[k] = ROC(p)
            elif name in ("bb_upper", "bb_lower", "bb_mid"):
                self.objs[("bb", p)] = self.objs.get(("bb", p)) or BollingerBands(p)
            elif name in ("highest", "lowest"):
                self.objs[("extreme", p)] = self.objs.get(("extreme", p)) or RollingExtreme(p)
            elif name in ("macd", "macd_signal", "macd_hist"):
                self.objs[("macd", None)] = self.objs.get(("macd", None)) or MACD()

    def update(self, f) -> dict[tuple[str, int | None], float | None]:  # noqa: ANN001
        for (kind, p), o in self.objs.items():
            if kind in ("atr", "adx"):
                o.update(f.high, f.low, f.close)  # type: ignore[attr-defined]
            elif kind == "extreme":
                o.update(f.high, f.low)  # type: ignore[attr-defined]
            else:
                o.update(f.close)  # type: ignore[attr-defined]
        snap: dict[tuple[str, int | None], float | None] = {}
        for name, p in self.keys:
            if name in PRICE_OPERANDS:
                v = getattr(f, name)
            elif name in FEATURE_OPERANDS:
                v = f[FEATURE_OPERANDS[name]]
            elif name in ("ema", "sma", "rsi", "roc"):
                v = self.objs[(name, p)].value  # type: ignore[attr-defined]
            elif name == "atr":
                v = self.objs[("atr", p)].value  # type: ignore[attr-defined]
            elif name == "atr_pct":
                a = self.objs[("atr", p)].value  # type: ignore[attr-defined]
                v = a / f.close * 100 if a is not None and f.close else None
            elif name in ("adx", "plus_di", "minus_di"):
                o = self.objs[("adx", p)]
                v = o.value if name == "adx" else (o.plus_di if name == "plus_di" else o.minus_di)  # type: ignore[attr-defined]
            elif name in ("bb_upper", "bb_lower", "bb_mid"):
                o = self.objs[("bb", p)]
                v = {"bb_upper": o.upper, "bb_lower": o.lower, "bb_mid": o.mid}[name]  # type: ignore[attr-defined]
            elif name in ("highest", "lowest"):
                o = self.objs[("extreme", p)]
                v = o.high if name == "highest" else o.low  # type: ignore[attr-defined]
            elif name in ("macd", "macd_signal", "macd_hist"):
                o = self.objs[("macd", None)]
                v = {"macd": o.macd, "macd_signal": o.signal, "macd_hist": o.histogram}[name]  # type: ignore[attr-defined]
            else:  # pragma: no cover - schema whitelist makes this unreachable
                raise KeyError(name)
            snap[(name, p)] = v
        return snap


class RuleStrategy(Strategy):
    name = "spec"

    def __init__(self, spec: StrategySpec) -> None:
        self.spec = spec
        self.entries = {d: resolved_entry(spec, side) for d, side in ((Direction.LONG, "long"), (Direction.SHORT, "short"))}
        conds = [c for c in self.entries.values() if c is not None] + list(spec.filters.all)
        if spec.exits.conditions is not None:
            conds.append(spec.exits.conditions)
        refs = [o for c in conds for o in walk_operands(c) if isinstance(o, Ind)]
        self.bank = _Bank({o.key for o in refs})
        depth = max([o.shift for o in refs] + [0]) + 2  # +1 for crosses
        self.hist: deque[dict] = deque(maxlen=depth)
        self.window = tuple(time.fromisoformat(x) for x in spec.filters.time_window)
        self.event_days = {date.fromisoformat(d) for d in spec.filters.event_days}
        self._last_ts = None
        # Exit conditions are written from the long side's point of view and mirrored for puts,
        # unless the spec only trades the short side (then they apply as written).
        c = spec.exits.conditions
        long_view = spec.sides.long is not None
        self.exits = {} if c is None else {
            Direction.LONG: c if long_view else None,
            Direction.SHORT: mirror_condition(c) if long_view else c,
        }

    # ---- evaluation -----------------------------------------------------------------------------
    def _val(self, o, extra_shift: int = 0) -> float | None:  # noqa: ANN001
        if isinstance(o, Const):
            return o.const
        i = o.shift + extra_shift
        if i >= len(self.hist):
            return None
        return self.hist[i].get(o.key)

    def eval(self, c, shift: int = 0) -> bool | None:  # noqa: ANN001
        if isinstance(c, Cmp):
            a, op, b = c.cmp
            x, y = self._val(a, shift), self._val(b, shift)
            if x is None or y is None:
                return None
            return {">": x > y, "<": x < y, ">=": x >= y, "<=": x <= y}[op]
        if isinstance(c, Cross):
            a, d, b = c.cross
            now = Cmp(cmp=(a, ">" if d == "above" else "<", b))
            before = Cmp(cmp=(a, "<=" if d == "above" else ">=", b))
            n, p = self.eval(now, shift), self.eval(before, shift + 1)
            return None if n is None or p is None else (n and p)
        if isinstance(c, Between):
            v, lo, hi = (self._val(o, shift) for o in c.between)
            return None if None in (v, lo, hi) else lo <= v <= hi  # type: ignore[operator]
        if isinstance(c, All):
            rs = [self.eval(x, shift) for x in c.all]
            return False if False in rs else (None if None in rs else True)
        if isinstance(c, Any_):
            rs = [self.eval(x, shift) for x in c.any]
            return True if True in rs else (None if None in rs else False)
        if isinstance(c, Not):
            r = self.eval(c.not_, shift)
            return None if r is None else not r
        raise TypeError(c)

    def _observe(self, state: MarketState) -> None:
        f = state.features
        if f.ts != self._last_ts:  # one update per bar, however many times we're asked
            self.hist.appendleft(self.bank.update(f))
            self._last_ts = f.ts

    # ---- Strategy API -----------------------------------------------------------------------------
    def generate_signal(self, state: MarketState) -> Signal | NoTrade | None:
        self._observe(state)
        f = state.features
        if not (self.window[0] <= f.time_ist < self.window[1]):
            return NoTrade("outside trading window")
        if f.day in self.event_days:
            return NoTrade("near major event")
        if state.daily_loss_hit:
            return NoTrade("daily loss reached")
        if state.has_position:
            return NoTrade("existing position")
        if state.trades_today >= self.spec.risk.max_trades_per_day:
            return NoTrade("max trades per day reached")
        for c in self.spec.filters.all:
            r = self.eval(c)
            if r is None:
                return None  # warming up
            if not r:
                return NoTrade("filter: " + render_condition(c).lstrip("- "))
        for d in (Direction.LONG, Direction.SHORT):
            cond = self.entries[d]
            if cond is not None and self.eval(cond) is True:
                return Signal(d, 0.5, state.underlying, "ATM", f.close, self.spec.exits.stop_loss_pct,
                              self.spec.exits.target_pct, f"{self.spec.name}: {d.value.lower()} rules met",
                              f.ts, f"spec:{self.spec.hash}",
                              {f"{k[0]}{'' if k[1] is None else k[1]}": (round(v, 4) if isinstance(v, float) else v)
                               for k, v in (self.hist[0] if self.hist else {}).items()})
        return None

    def exit_signal(self, state: MarketState, direction: Direction) -> str | None:
        self._observe(state)
        cond = self.exits.get(direction)
        return "rule exit" if cond is not None and self.eval(cond) is True else None
