"""RuleStrategy: deterministic interpreter for a StrategySpec (PRD §19.5).

Conditions are evaluated with three-valued logic: an operand that is still warming up (None)
makes a comparison *unknown*, and unknown never triggers an entry. Values are snapshotted per
bar so `shift` and `crosses` look only at closed bars at or before the current one.
"""

from __future__ import annotations

from collections import deque
from datetime import date, time
from typing import Any

from trader.market_data.resample import minutes

from trader.indicators import extra as X
from trader.indicators.extra import pivot_levels
from trader.strategy.extra_operands import PIVOTS, SOURCE
from trader.strategy.htf import TF_MINUTES, HTFAggregator, label
from trader.indicators import ADX, ATR, EMA, MACD, ROC, RSI, SMA, BollingerBands, Pivots, RollingExtreme, candle_patterns
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
PATTERNS = {"cs_" + k: k for k in ("bull_engulfing", "bear_engulfing", "doji", "hammer", "shooting_star",
                                    "morning_star", "evening_star")}
DAILY = {"daily_ema": EMA, "daily_sma": SMA, "daily_rsi": RSI}
DIV_PIVOT_BARS = 3  # swing width used for RSI divergence


FACTORIES = {  # kind -> indicator object, from the operand's period and mult
    "wma": lambda p, m: X.WMA(p), "hma": lambda p, m: X.HMA(p), "vwma": lambda p, m: X.VWMA(p), "dema": lambda p, m: X.DEMA(p),
    "tema": lambda p, m: X.TEMA(p), "rma": lambda p, m: X.RMA(p), "linreg": lambda p, m: X.LinReg(p),
    "supertrend": lambda p, m: X.Supertrend(p, m), "psar": lambda p, m: X.ParabolicSAR(), "aroon": lambda p, m: X.Aroon(p),
    "vortex": lambda p, m: X.Vortex(p), "ichimoku": lambda p, m: X.Ichimoku(), "stoch": lambda p, m: X.Stochastic(p),
    "stoch_rsi": lambda p, m: X.StochRSI(p), "cci": lambda p, m: X.CCI(p), "williams_r": lambda p, m: X.WilliamsR(p),
    "mfi": lambda p, m: X.MFI(p), "mom": lambda p, m: X.Momentum(p), "cmo": lambda p, m: X.CMO(p),
    "ao": lambda p, m: X.AwesomeOscillator(), "trix": lambda p, m: X.TRIX(p), "ult_osc": lambda p, m: X.UltimateOscillator(),
    "obv": lambda p, m: X.OBV(), "ad_line": lambda p, m: X.ADLine(), "cmf": lambda p, m: X.CMF(p),
    "keltner": lambda p, m: X.Keltner(p, m), "donchian": lambda p, m: X.Donchian(p), "bb_extra": lambda p, m: X.BollingerExtra(p, m),
    "stddev": lambda p, m: X.StdDev(p), "prices": lambda p, m: X.PriceTransforms(),
}


class _Bank:
    """Indicators requested by a spec, keyed by (name, period); updated once per bar."""

    def __init__(self, keys: set[tuple], base_minutes: int = 1) -> None:
        # Operands on a higher timeframe are served by their own bank, fed with candles built from these bars.
        self.htf_keys = [k for k in keys if k[3]]
        by_tf: dict[str, set] = {}
        for k in self.htf_keys:
            by_tf.setdefault(k[3], set()).add((*k[:3], None))
        self.htf: dict[str, tuple[HTFAggregator, "_Bank", deque]] = {
            tf: (HTFAggregator(TF_MINUTES[tf], base_minutes), _Bank(ks), deque(maxlen=21)) for tf, ks in by_tf.items()}
        keys = {k[:3] for k in keys if not k[3]}
        self.keys = keys
        self.objs: dict[tuple[str, int | None], object] = {}
        self.extras: dict[tuple, object] = {}  # indicator objects shared by every operand that reads them
        self.recent: deque[tuple[float, float, float, float]] = deque(maxlen=3)  # (o, h, l, c) for candle patterns
        self.volumes: dict[int, deque[float]] = {}  # rel_volume: previous volumes per period
        self.daily: dict[tuple[str, int | None], object] = {}  # indicators fed once a day with the previous close
        self._day = None
        self._pats: dict[str, float] = {}
        for name, p, m in keys:
            k = (name, p)
            if name in SOURCE:
                kind = SOURCE[name][0]
                self.extras.setdefault((kind, p, m), FACTORIES[kind](p, m))
            elif name in DAILY:
                self.daily[k] = DAILY[name](p)
            elif name == "rel_volume":
                self.volumes[p] = deque(maxlen=p)
            elif name in ("swing_high", "swing_low"):
                self.objs[("pivots", p)] = self.objs.get(("pivots", p)) or Pivots(p)
            elif name in ("rsi_bull_div", "rsi_bear_div"):
                self.objs[("divrsi", 14)] = self.objs.get(("divrsi", 14)) or RSI(14)
                self.objs[("pivots", DIV_PIVOT_BARS)] = self.objs.get(("pivots", DIV_PIVOT_BARS)) or Pivots(DIV_PIVOT_BARS)
            elif name == "ema":
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

    def update(self, f) -> dict[tuple, Any]:  # noqa: ANN001
        for agg, bank, hist in self.htf.values():  # a candle completed by this bar becomes visible now, not before
            for candle in agg.update(f):
                hist.appendleft(bank.update(candle))
        if self.daily and f.day != self._day:  # a new day began: yesterday's close is now final
            self._day = f.day
            if f["prev_day_close"] is not None:
                for o in self.daily.values():
                    o.update(f["prev_day_close"])  # type: ignore[attr-defined]
        self.recent.append((f.open, f.high, f.low, f.close))
        if any(n in PATTERNS for n, _, _ in self.keys):
            self._pats = candle_patterns(list(self.recent))
        if ("divrsi", 14) in self.objs:  # first, so the pivots below see this bar's RSI
            self.objs[("divrsi", 14)].update(f.close)  # type: ignore[attr-defined]
        for (kind, p), o in self.objs.items():
            if kind == "divrsi":
                continue
            if kind in ("atr", "adx"):
                o.update(f.high, f.low, f.close)  # type: ignore[attr-defined]
            elif kind == "extreme":
                o.update(f.high, f.low)  # type: ignore[attr-defined]
            elif kind == "pivots":
                rsi = self.objs[("divrsi", 14)].value if ("divrsi", 14) in self.objs else None  # type: ignore[attr-defined]
                o.update(f.high, f.low, rsi if p == DIV_PIVOT_BARS else None)  # type: ignore[attr-defined]
            else:
                o.update(f.close)  # type: ignore[attr-defined]
        vol = f["fut_volume"]
        for ex in self.extras.values():
            ex.update(f.open, f.high, f.low, f.close, vol)  # type: ignore[attr-defined]
        rel = {}
        for per, dq in self.volumes.items():
            rel[per] = vol / (sum(dq) / per) if vol and len(dq) == per and sum(dq) > 0 else None
            if vol:
                dq.append(vol)
        snap: dict[tuple, Any] = {}
        for name, p, m in self.keys:
            if name in SOURCE:
                v = self.extras[(SOURCE[name][0], p, m)].values.get(SOURCE[name][1])  # type: ignore[attr-defined]
            elif name in PIVOTS:
                ph, pl, pc = f["prev_day_high"], f["prev_day_low"], f["prev_day_close"]
                v = pivot_levels(ph, pl, pc)[PIVOTS[name]] if None not in (ph, pl, pc) else None
            elif name in PRICE_OPERANDS:
                v = getattr(f, name)
            elif name in PATTERNS:
                v = self._pats.get(PATTERNS[name])
            elif name in DAILY:
                v = self.daily[(name, p)].value  # type: ignore[attr-defined]
            elif name == "volume":
                v = f["fut_volume"]
            elif name == "rel_volume":
                v = rel[p]
            elif name in ("swing_high", "swing_low"):
                o = self.objs[("pivots", p)]
                v = o.last_high if name == "swing_high" else o.last_low  # type: ignore[attr-defined]
            elif name in ("rsi_bull_div", "rsi_bear_div"):
                bull, bear = self.objs[("pivots", DIV_PIVOT_BARS)].divergence(p)  # type: ignore[attr-defined]
                v = float(bull if name == "rsi_bull_div" else bear)
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
            snap[(name, p, m, None)] = v
        for k in self.htf_keys:  # higher-timeframe operands: the latest completed candles, newest first (shift indexes into this)
            snap[k] = [h.get((*k[:3], None)) for h in self.htf[k[3]][2]]
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
        self.bank = _Bank({o.key for o in refs}, minutes(spec.timeframe) if spec.timeframe != "1d" else 375)
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
        if o.tf:  # higher timeframe: `shift` counts that timeframe's candles; extra_shift steps back one BASE bar (for crosses)
            if extra_shift >= len(self.hist):
                return None
            vals = self.hist[extra_shift].get(o.key)
            return vals[o.shift] if vals and o.shift < len(vals) else None
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
                              {label(k): (round(v, 4) if isinstance(v, float) else v)
                               for k, v in ((k, v[0] if isinstance(v, list) else v)
                                            for k, v in (self.hist[0] if self.hist else {}).items())},
                              risk_pct=self.spec.risk.risk_per_trade_pct)
        return None

    def exit_signal(self, state: MarketState, direction: Direction) -> str | None:
        self._observe(state)
        cond = self.exits.get(direction)
        return "rule exit" if cond is not None and self.eval(cond) is True else None
