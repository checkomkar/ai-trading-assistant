"""Indicator series for the backtest chart (PRD §24.3 B2): what the strategy saw, computed by the strategy's own code.

The chart used to draw two EMAs on the server and compute everything else in the browser, which could disagree
with the engine (warm-up, seeding, futures VWAP). Here every series is produced by the same `FeatureEngine` and
indicator bank (`rule_strategy._Bank`) the backtest ran, fed the same candles from the same first bar, so a value
on the chart is the value a condition compared.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from trader.features import FeatureEngine
from trader.market_data.candles import Candle
from trader.strategy import catalogue
from trader.strategy.rule_strategy import _Bank
from trader.strategy.spec import (All, Any_, Between, Cmp, Const, Cross, Ind, Not, StrategySpec, resolved_entry,
                                  walk_operands)

Key = tuple  # (name, period, effective mult, tf) as `Ind.key`

MAX_EXTRA = 10  # indicators a user may add to one chart


@dataclass
class SeriesSpec:
    key: Key
    source: str  # "strategy" | "user"
    thresholds: set[float] = field(default_factory=set)

    @property
    def id(self) -> str:
        n, p, m, tf = self.key
        return "|".join("" if x is None else (f"{x:g}" if isinstance(x, float) else str(x)) for x in (n, p, m, tf))

    @property
    def label(self) -> str:
        n, p, m, tf = self.key
        return catalogue.label(n, p, m) + (f" on {tf}" if tf else "")


def _conditions(spec: StrategySpec) -> list:
    out = [resolved_entry(spec, s) for s in ("long", "short")] + list(spec.filters.all)
    if spec.exits.conditions is not None:
        out.append(spec.exits.conditions)
    return [c for c in out if c is not None]


def _thresholds(c, found: dict[Key, set[float]]) -> None:  # noqa: ANN001
    """Constants an indicator is compared with (RSI > 55): drawn as dashed lines on that indicator's pane."""
    pairs: list[tuple] = []
    if isinstance(c, Cmp):
        pairs = [(c.cmp[0], c.cmp[2])]
    elif isinstance(c, Cross):
        pairs = [(c.cross[0], c.cross[2])]
    elif isinstance(c, Between):
        v, lo, hi = c.between
        pairs = [(v, lo), (v, hi)]
    elif isinstance(c, All):
        for x in c.all:
            _thresholds(x, found)
    elif isinstance(c, Any_):
        for x in c.any:
            _thresholds(x, found)
    elif isinstance(c, Not):
        _thresholds(c.not_, found)
    for a, b in pairs:
        for ind, const in ((a, b), (b, a)):
            if isinstance(ind, Ind) and isinstance(const, Const):
                found.setdefault(ind.key, set()).add(float(const.const))


def strategy_series(strategy_params: dict) -> list[SeriesSpec]:
    """The indicators a stored run's strategy referenced, with the constants it compared them with."""
    if "spec" in strategy_params:
        spec = StrategySpec.model_validate(strategy_params["spec"])
        conds = _conditions(spec)
        keys: list[Key] = []
        for c in conds:
            for o in walk_operands(c):
                if isinstance(o, Ind) and o.key not in keys and o.ind not in ("open", "high", "low", "close"):
                    keys.append(o.key)
        th: dict[Key, set[float]] = {}
        for c in conds:
            _thresholds(c, th)
        return [SeriesSpec(k, "strategy", th.get(k, set())) for k in keys]
    # Momentum #1 (strategy/momentum.py): its fixed rule set, at the periods the FeatureEngine uses.
    p = strategy_params
    out = [SeriesSpec(("ema", 9, None, None), "strategy"), SeriesSpec(("ema", 21, None, None), "strategy"),
           SeriesSpec(("rsi", 14, None, None), "strategy", {float(p.get("rsi_long", 55)), float(p.get("rsi_short", 45))}),
           SeriesSpec(("adx", 14, None, None), "strategy", {float(p.get("adx_min", 18))}),
           SeriesSpec(("atr_pct", 14, None, None), "strategy", {float(p.get("atr_pct_min", 0.04))}),
           SeriesSpec(("highest", 6, None, None), "strategy"), SeriesSpec(("lowest", 6, None, None), "strategy")]
    if p.get("use_vwap", True):
        out += [SeriesSpec(("fut_close", None, None, None), "strategy"), SeriesSpec(("vwap", None, None, None), "strategy")]
    return out


def parse_extra(raw: str | None) -> list[SeriesSpec]:
    """`name:period:mult` items, comma-separated (period/mult optional), validated like spec operands."""
    if not raw:
        return []
    out: list[SeriesSpec] = []
    for item in raw.split(",")[:MAX_EXTRA]:
        parts = item.strip().split(":")
        if not parts[0]:
            continue
        name = parts[0]
        period = int(parts[1]) if len(parts) > 1 and parts[1] else None
        mult = float(parts[2]) if len(parts) > 2 and parts[2] else None
        if period is None and name in catalogue.INDICATORS and catalogue.INDICATORS[name][0]:
            period = catalogue.default_period(name)
        ind = Ind(ind=name, period=period, mult=mult)  # raises ValueError on an unknown name or a bad period
        out.append(SeriesSpec(ind.key, "user"))
    return out


def compute(bars: list[Candle], futures: dict[datetime, Candle], vix: dict[datetime, float], specs: list[SeriesSpec],
            timeframe_minutes: int, keep_from: datetime | None = None) -> dict[str, list[list]]:
    """{series id: [[unix seconds, value], ...]} for every bar from `keep_from`, fed from the first bar like the engine."""
    if not specs:
        return {}
    keys = {s.key for s in specs}
    bank = _Bank(keys, timeframe_minutes)
    fe = FeatureEngine(timeframe_minutes)
    out: dict[str, list[list]] = {s.id: [] for s in specs}
    ids = {s.key: s.id for s in specs}
    for b in bars:
        f = fe.update(b, futures.get(b.ts), vix.get(b.ts))
        snap = bank.update(f)
        if keep_from is not None and b.ts < keep_from:
            continue
        t = int(b.ts.timestamp())
        for k in keys:
            v = snap.get(k)
            if isinstance(v, list):  # higher-timeframe operand: the latest completed candle's value
                v = v[0] if v else None
            if v is not None:
                out[ids[k]].append([t, round(float(v), 4)])
    return out


def describe(specs: list[SeriesSpec], values: dict[str, list[list]]) -> list[dict]:
    """The chart response: one entry per series, with its pane and threshold lines."""
    res = []
    for s in specs:
        name = s.key[0]
        level = catalogue.INDICATORS[name][3]
        res.append({"id": s.id, "name": name, "label": s.label, "source": s.source, "level": level,
                    "pane": catalogue.PANES.get(level, level), "thresholds": sorted(s.thresholds),
                    "points": values.get(s.id, [])})
    return res
