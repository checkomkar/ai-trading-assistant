"""Strategy Spec v1: declarative, validated strategy definitions (PRD §19.3).

A spec is data, never code. It is produced by hand (form/JSON) or by the natural-language
translator, validated here, rendered back to English deterministically, and executed by
`RuleStrategy`. Nothing in a spec can reference a future bar.
"""

from __future__ import annotations

import hashlib
import json
from datetime import time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---- operands --------------------------------------------------------------------------
# name -> (needs period, period bounds, english label, "level": which price scale it lives on)
INDICATORS: dict[str, tuple[bool, tuple[int, int] | None, str, str]] = {
    "open": (False, None, "open", "index"),
    "high": (False, None, "high", "index"),
    "low": (False, None, "low", "index"),
    "close": (False, None, "close", "index"),
    "ema": (True, (2, 200), "EMA({p})", "index"),
    "sma": (True, (2, 200), "SMA({p})", "index"),
    "highest": (True, (2, 100), "highest high of the previous {p} bars", "index"),
    "lowest": (True, (2, 100), "lowest low of the previous {p} bars", "index"),
    "bb_upper": (True, (5, 100), "upper Bollinger band({p})", "index"),
    "bb_lower": (True, (5, 100), "lower Bollinger band({p})", "index"),
    "bb_mid": (True, (5, 100), "middle Bollinger band({p})", "index"),
    "or_high": (False, None, "opening-range high", "index"),
    "or_low": (False, None, "opening-range low", "index"),
    "prev_day_high": (False, None, "previous day's high", "index"),
    "prev_day_low": (False, None, "previous day's low", "index"),
    "prev_day_close": (False, None, "previous day's close", "index"),
    "fut_close": (False, None, "futures price", "futures"),
    "vwap": (False, None, "futures session VWAP", "futures"),
    "rsi": (True, (2, 100), "RSI({p})", "osc"),
    "adx": (True, (2, 100), "ADX({p})", "osc"),
    "plus_di": (True, (2, 100), "+DI({p})", "osc"),
    "minus_di": (True, (2, 100), "-DI({p})", "osc"),
    "atr": (True, (2, 100), "ATR({p})", "points"),
    "atr_pct": (True, (2, 100), "ATR({p}) as % of price", "pct"),
    "roc": (True, (1, 100), "rate of change({p}) %", "pct"),
    "macd": (False, None, "MACD line (12,26)", "macd"),
    "macd_signal": (False, None, "MACD signal (9)", "macd"),
    "macd_hist": (False, None, "MACD histogram", "macd"),
    "vix": (False, None, "INDIA VIX", "vix"),
}
IndicatorName = Literal[tuple(INDICATORS)]  # type: ignore[valid-type]

MIRROR_NAME = {"highest": "lowest", "lowest": "highest", "or_high": "or_low", "or_low": "or_high",
               "prev_day_high": "prev_day_low", "prev_day_low": "prev_day_high", "bb_upper": "bb_lower",
               "bb_lower": "bb_upper", "plus_di": "minus_di", "minus_di": "plus_di", "high": "low", "low": "high"}
MIRROR_OP = {">": "<", "<": ">", ">=": "<=", "<=": ">="}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Ind(_Strict):
    ind: IndicatorName
    period: int | None = None
    shift: int = Field(0, ge=0, le=20, description="bars back; 0 = current closed bar")

    @model_validator(mode="after")
    def _period(self) -> "Ind":
        needs, bounds, _, _ = INDICATORS[self.ind]
        if needs:
            if self.period is None:
                raise ValueError(f"{self.ind} needs a period")
            lo, hi = bounds  # type: ignore[misc]
            if not lo <= self.period <= hi:
                raise ValueError(f"{self.ind} period must be between {lo} and {hi}")
        elif self.period is not None:
            raise ValueError(f"{self.ind} takes no period")
        return self

    @property
    def key(self) -> tuple[str, int | None]:
        return (self.ind, self.period)


class Const(_Strict):
    const: float


Operand = Union[Ind, Const]
CmpOp = Literal[">", "<", ">=", "<="]


class Cmp(_Strict):
    cmp: tuple[Operand, CmpOp, Operand]


class Cross(_Strict):
    cross: tuple[Operand, Literal["above", "below"], Operand]


class Between(_Strict):
    between: tuple[Operand, Operand, Operand]  # value, low, high (inclusive)


class All(_Strict):
    all: list["Condition"] = Field(min_length=1)


class Any_(_Strict):
    any: list["Condition"] = Field(min_length=1)


class Not(_Strict):
    not_: "Condition" = Field(alias="not")


Condition = Annotated[Union[Cmp, Cross, Between, All, Any_, Not], Field(union_mode="left_to_right")]
for _m in (All, Any_, Not):
    _m.model_rebuild()


class Side(_Strict):
    option: Literal["CE", "PE"]
    entry: Condition | None = None
    mirror_of: Literal["long", "short"] | None = None

    @model_validator(mode="after")
    def _one(self) -> "Side":
        if (self.entry is None) == (self.mirror_of is None):
            raise ValueError("a side needs exactly one of `entry` or `mirror_of`")
        return self


class Sides(_Strict):
    long: Side | None = None
    short: Side | None = None

    @model_validator(mode="after")
    def _some(self) -> "Sides":
        if self.long is None and self.short is None:
            raise ValueError("define at least one side (long and/or short)")
        for name, side in (("long", self.long), ("short", self.short)):
            if side and side.mirror_of == name:
                raise ValueError(f"{name} cannot mirror itself")
            if side and side.mirror_of and getattr(self, side.mirror_of) is None:
                raise ValueError(f"{name} mirrors {side.mirror_of}, which is not defined")
            if side and side.mirror_of and getattr(self, side.mirror_of).mirror_of:
                raise ValueError("cannot mirror a side that is itself a mirror")
        return self


class Filters(_Strict):
    time_window: tuple[str, str] = ("09:30", "14:45")  # bar-start times, IST, [start, end)
    all: list[Condition] = Field(default_factory=list)
    event_days: list[str] = Field(default_factory=list)

    @field_validator("time_window")
    @classmethod
    def _tw(cls, v: tuple[str, str]) -> tuple[str, str]:
        a, b = (time.fromisoformat(x) for x in v)
        if not (time(9, 15) <= a < b <= time(15, 30)):
            raise ValueError("time window must be within 09:15-15:30 and start before it ends")
        return v


class Trail(_Strict):
    activate_pct: float = Field(gt=0, le=500)
    trail_pct: float = Field(gt=0, lt=100)


class Exits(_Strict):
    stop_loss_pct: float = Field(gt=0, lt=100)
    target_pct: float = Field(gt=0, le=1000)
    trail: Trail | None = None
    time_exit_bars: int | None = Field(None, ge=1, le=375)
    conditions: Condition | None = None  # exit the position when true (checked on bar close)
    eod: str = "15:15"


class OptionSelection(_Strict):
    strike: Literal["ATM"] = "ATM"
    expiry: Literal["nearest"] = "nearest"
    roll_on_expiry_day: bool = True


class Risk(_Strict):
    max_trades_per_day: int = Field(3, ge=1, le=20)


class StrategySpec(_Strict):
    spec_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    underlyings: list[Literal["NIFTY", "BANKNIFTY"]] = Field(default_factory=lambda: ["NIFTY"], min_length=1)
    timeframe: Literal["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m", "60m"] = "5m"
    sides: Sides
    filters: Filters = Field(default_factory=Filters)
    option_selection: OptionSelection = Field(default_factory=OptionSelection)
    exits: Exits
    risk: Risk = Field(default_factory=Risk)

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(mode="json", by_alias=True, exclude_none=True), sort_keys=True,
                          separators=(",", ":"))

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()[:16]


# ---- semantic validation ------------------------------------------------------------------
def walk_operands(c: object):  # noqa: ANN201
    if isinstance(c, Cmp):
        yield c.cmp[0]; yield c.cmp[2]  # noqa: E702
    elif isinstance(c, Cross):
        yield c.cross[0]; yield c.cross[2]  # noqa: E702
    elif isinstance(c, Between):
        yield from c.between
    elif isinstance(c, All):
        for x in c.all:
            yield from walk_operands(x)
    elif isinstance(c, Any_):
        for x in c.any:
            yield from walk_operands(x)
    elif isinstance(c, Not):
        yield from walk_operands(c.not_)


def walk_pairs(c: object):  # noqa: ANN201
    if isinstance(c, (Cmp, Cross)):
        a, _, b = c.cmp if isinstance(c, Cmp) else c.cross
        yield a, b
    elif isinstance(c, Between):
        yield c.between[0], c.between[1]
        yield c.between[0], c.between[2]
    elif isinstance(c, All):
        for x in c.all:
            yield from walk_pairs(x)
    elif isinstance(c, Any_):
        for x in c.any:
            yield from walk_pairs(x)
    elif isinstance(c, Not):
        yield from walk_pairs(c.not_)


def _level(o: Operand) -> str | None:
    return INDICATORS[o.ind][3] if isinstance(o, Ind) else None


def semantic_errors(spec: StrategySpec) -> list[str]:
    """Problems schema validation can't see. Empty list = OK."""
    errs: list[str] = []
    conds: list[tuple[str, object]] = []
    for name in ("long", "short"):
        side = getattr(spec.sides, name)
        if side and side.entry is not None:
            conds.append((f"{name} entry", side.entry))
    conds += [("filters", c) for c in spec.filters.all]
    if spec.exits.conditions is not None:
        conds.append(("exit conditions", spec.exits.conditions))
    for where, c in conds:
        for a, b in walk_pairs(c):
            la, lb = _level(a), _level(b)
            if la and lb and la != lb and {la, lb} <= {"index", "futures"}:
                errs.append(f"{where}: compares an index-level value with a futures-level value "
                            f"({a.ind} vs {b.ind}); they differ by the futures basis. Use fut_close with vwap.")
            if isinstance(a, Const) and isinstance(b, Const):
                errs.append(f"{where}: compares two constants")
    if spec.sides.long and spec.sides.long.option != "CE":
        errs.append("long side must buy CE (long options only, PRD §3)")
    if spec.sides.short and spec.sides.short.option != "PE":
        errs.append("short side must buy PE (long options only, PRD §3)")
    tw_end = time.fromisoformat(spec.filters.time_window[1])
    if tw_end > time.fromisoformat(spec.exits.eod):
        errs.append("entries are allowed after the EOD exit time")
    return errs


# ---- mirroring ------------------------------------------------------------------------------
def mirror_condition(c: object) -> object:
    """Long <-> short: flip comparisons, swap high/low-type operands, reflect bounded oscillators."""
    def mo(o: Operand) -> Operand:
        if isinstance(o, Ind):
            return Ind(ind=MIRROR_NAME.get(o.ind, o.ind), period=o.period, shift=o.shift)
        return o

    def reflect(ind: Operand, const: Operand) -> Operand:
        if not isinstance(const, Const) or not isinstance(ind, Ind):
            return const
        if ind.ind == "rsi":
            return Const(const=100 - const.const)
        if ind.ind in ("macd", "macd_signal", "macd_hist", "roc"):
            return Const(const=-const.const)
        return const

    if isinstance(c, Cmp):
        a, op, b = c.cmp
        return Cmp(cmp=(mo(reflect(b, a)), MIRROR_OP[op], mo(reflect(a, b))))
    if isinstance(c, Cross):
        a, d, b = c.cross
        return Cross(cross=(mo(reflect(b, a)), "below" if d == "above" else "above", mo(reflect(a, b))))
    if isinstance(c, Between):
        v, lo, hi = c.between
        return Between(between=(mo(v), mo(reflect(v, hi)), mo(reflect(v, lo))))
    if isinstance(c, All):
        return All(all=[mirror_condition(x) for x in c.all])
    if isinstance(c, Any_):
        return Any_(any=[mirror_condition(x) for x in c.any])
    if isinstance(c, Not):
        return Not(**{"not": mirror_condition(c.not_)})
    raise TypeError(c)


def resolved_entry(spec: StrategySpec, side: str) -> object | None:
    s = getattr(spec.sides, side)
    if s is None:
        return None
    if s.entry is not None:
        return s.entry
    return mirror_condition(getattr(spec.sides, s.mirror_of).entry)


# ---- deterministic English rendering ----------------------------------------------------------------
def _op_text(o: Operand) -> str:
    if isinstance(o, Const):
        return f"{o.const:g}"
    _, _, label, _ = INDICATORS[o.ind]
    txt = label.format(p=o.period)
    if o.shift:
        txt += f" {o.shift} bar{'s' if o.shift > 1 else ''} ago"
    return txt


_OPS = {">": "is above", "<": "is below", ">=": "is at or above", "<=": "is at or below"}


def render_condition(c: object, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(c, Cmp):
        a, op, b = c.cmp
        return f"{pad}- {_op_text(a)} {_OPS[op]} {_op_text(b)}"
    if isinstance(c, Cross):
        a, d, b = c.cross
        return f"{pad}- {_op_text(a)} crosses {d} {_op_text(b)} (on this bar's close)"
    if isinstance(c, Between):
        v, lo, hi = c.between
        return f"{pad}- {_op_text(v)} is between {_op_text(lo)} and {_op_text(hi)}"
    if isinstance(c, All):
        if depth == 0:  # a top-level AND is just the bullet list ("when ALL of these are true")
            return "\n".join(render_condition(x, 0) for x in c.all)
        inner = "\n".join(render_condition(x, depth + 1) for x in c.all)
        return f"{pad}- ALL of:\n{inner}"
    if isinstance(c, Any_):
        inner = "\n".join(render_condition(x, depth + 1) for x in c.any)
        return f"{pad}- ANY of:\n{inner}"
    if isinstance(c, Not):
        return f"{pad}- NOT:\n{render_condition(c.not_, depth + 1)}"
    raise TypeError(c)


def render_english(spec: StrategySpec) -> str:
    lines = [f"Strategy: {spec.name}",
             f"Underlying: {', '.join(spec.underlyings)} · {spec.timeframe} candles · decisions on candle close, "
             f"orders at the next candle's open"]
    for side, verb in (("long", "Buy the ATM call (CE)"), ("short", "Buy the ATM put (PE)")):
        s = getattr(spec.sides, side)
        if s is None:
            continue
        cond = resolved_entry(spec, side)
        note = f" (mirror of the {s.mirror_of} rules)" if s.mirror_of else ""
        lines.append(f"\n{verb} when ALL of these are true{note}:")
        lines.append(render_condition(cond))
    f = spec.filters
    lines.append(f"\nOnly enter between {f.time_window[0]} and {f.time_window[1]} IST (candle start time)"
                 + (", and only when:" if f.all else "."))
    for c in f.all:
        lines.append(render_condition(c))
    if f.event_days:
        lines.append(f"Never trade on: {', '.join(f.event_days)}.")
    e = spec.exits
    lines.append(f"\nExit: stop loss {e.stop_loss_pct:g}% and target {e.target_pct:g}% of the option premium"
                 + (f"; once up {e.trail.activate_pct:g}%, trail the stop {e.trail.trail_pct:g}% below the best price"
                    if e.trail else "")
                 + (f"; exit after {e.time_exit_bars} candles" if e.time_exit_bars else "")
                 + f"; always flat by {e.eod} IST.")
    if e.conditions is not None:
        lines.append("Also exit at the next candle's open when:")
        lines.append(render_condition(e.conditions))
    n = spec.risk.max_trades_per_day
    lines.append(f"\nRisk: at most {n} trade{'s' if n != 1 else ''} per day, one position at a time. "
                 f"Expiry: nearest{' (rolled to the next one on expiry day)' if spec.option_selection.roll_on_expiry_day else ''}.")
    return "\n".join(lines)


# ---- reference spec: Momentum #1 (PRD §7.1/7.2) -------------------------------------------------------
MOMENTUM_V1 = {
    "name": "Momentum #1 (PRD 7.1)",
    "underlyings": ["NIFTY"],
    "timeframe": "5m",
    "sides": {
        "long": {"option": "CE", "entry": {"all": [
            {"cmp": [{"ind": "fut_close"}, ">", {"ind": "vwap"}]},
            {"cmp": [{"ind": "ema", "period": 9}, ">", {"ind": "ema", "period": 21}]},
            {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 55}]},
            {"cmp": [{"ind": "close"}, ">", {"ind": "highest", "period": 6}]},
        ]}},
        "short": {"option": "PE", "mirror_of": "long"},
    },
    "filters": {"time_window": ["09:30", "14:45"], "all": [
        {"cmp": [{"ind": "adx", "period": 14}, ">=", {"const": 18}]},
        {"cmp": [{"ind": "atr_pct", "period": 14}, ">=", {"const": 0.04}]},
    ]},
    "exits": {"stop_loss_pct": 25, "target_pct": 50, "trail": {"activate_pct": 30, "trail_pct": 15}},
    "risk": {"max_trades_per_day": 3},
}
