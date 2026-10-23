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
    # candlestick shapes on the last 1-3 closed candles: 1 = formed on this candle, 0 = not
    "cs_bull_engulfing": (False, None, "bullish engulfing candle", "flag"),
    "cs_bear_engulfing": (False, None, "bearish engulfing candle", "flag"),
    "cs_doji": (False, None, "doji candle", "flag"),
    "cs_hammer": (False, None, "hammer candle", "flag"),
    "cs_shooting_star": (False, None, "shooting-star candle", "flag"),
    "cs_morning_star": (False, None, "morning-star pattern", "flag"),
    "cs_evening_star": (False, None, "evening-star pattern", "flag"),
    # RSI(14) divergence between the last two confirmed swing points (3 bars each side), at most {p} bars apart
    "rsi_bull_div": (True, (5, 60), "bullish RSI divergence (swing lows within {p} bars)", "flag"),
    "rsi_bear_div": (True, (5, 60), "bearish RSI divergence (swing highs within {p} bars)", "flag"),
    # dynamic support / resistance: the latest confirmed swing low / high ({p} bars each side)
    "swing_high": (True, (2, 10), "latest swing high ({p} bars each side)", "index"),
    "swing_low": (True, (2, 10), "latest swing low ({p} bars each side)", "index"),
    # volume comes from the near-month futures (index candles carry none)
    "volume": (False, None, "futures volume", "volume"),
    "rel_volume": (True, (3, 100), "futures volume relative to its {p}-bar average", "volume"),
    # daily-timeframe context, from completed days only (today's unfinished day is never used)
    "daily_ema": (True, (2, 200), "daily EMA({p})", "index"),
    "daily_sma": (True, (2, 200), "daily SMA({p})", "index"),
    "daily_rsi": (True, (2, 100), "daily RSI({p})", "osc"),
}
from trader.strategy.htf import TF_MINUTES  # noqa: E402
from trader.strategy.extra_operands import (  # noqa: E402
    EXTRA_INDICATORS, MIRROR as _EXTRA_MIRROR, MULT_DEFAULTS, REFLECT_100, REFLECT_NEG,
)

INDICATORS.update(EXTRA_INDICATORS)
IndicatorName = Literal[tuple(INDICATORS)]  # type: ignore[valid-type]

MIRROR_NAME = {"highest": "lowest", "lowest": "highest", "or_high": "or_low", "or_low": "or_high",
               "prev_day_high": "prev_day_low", "prev_day_low": "prev_day_high", "bb_upper": "bb_lower",
               "bb_lower": "bb_upper", "plus_di": "minus_di", "minus_di": "plus_di", "high": "low", "low": "high",
               "cs_bull_engulfing": "cs_bear_engulfing", "cs_bear_engulfing": "cs_bull_engulfing",
               "cs_hammer": "cs_shooting_star", "cs_shooting_star": "cs_hammer",
               "cs_morning_star": "cs_evening_star", "cs_evening_star": "cs_morning_star",
               "rsi_bull_div": "rsi_bear_div", "rsi_bear_div": "rsi_bull_div",
               "swing_high": "swing_low", "swing_low": "swing_high"}
MIRROR_NAME.update(_EXTRA_MIRROR)
MIRROR_OP = {">": "<", "<": ">", ">=": "<=", "<=": ">="}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


TfName = Literal[tuple(TF_MINUTES)]  # type: ignore[valid-type]


class Ind(_Strict):
    ind: IndicatorName
    period: int | None = None
    # evaluate this operand on higher-timeframe candles built from the strategy's own bars (completed candles only);
    # `shift` then counts those candles. Omitted = the strategy's own timeframe.
    tf: TfName | None = None
    # second setting of a few indicators (Supertrend factor, Keltner / Bollinger multiplier); omitted = TradingView's default
    mult: float | None = Field(None, ge=0.1, le=10)
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
        if self.mult is not None and self.ind not in MULT_DEFAULTS:
            raise ValueError(f"{self.ind} takes no mult")
        return self

    @property
    def effective_mult(self) -> float | None:
        return self.mult if self.mult is not None else MULT_DEFAULTS.get(self.ind)

    @property
    def key(self) -> tuple[str, int | None, float | None, str | None]:
        return (self.ind, self.period, self.effective_mult, self.tf)


class Const(_Strict):
    const: float


Operand = Union[Ind, Const]
CmpOp = Literal[">", "<", ">=", "<="]


class Cmp(_Strict):
    cmp: tuple[Operand, CmpOp, Operand]


class Cross(_Strict):
    cross: tuple[Operand, Literal["above", "below"], Operand]
    # true if the cross happened on this bar OR on any of the previous `within` - 1 bars (a cross is a single-bar event, so
    # asking for several crosses at the same moment almost never happens; "crossed recently" is usually what is meant)
    within: int | None = Field(None, ge=2, le=120)


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
    option: Literal["CE", "PE"] | None = None  # options strategies only; a stock strategy just buys the shares
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


class IndexLevel(_Strict):
    """A distance on the index (not the option premium), measured from the index price at entry. Exactly one of
    `points`, `pct` (% of the index at the signal) or `atr_mult` (x ATR(`atr_period`) at the signal)."""
    points: float | None = Field(None, gt=0, le=5000)
    pct: float | None = Field(None, gt=0, le=10)
    atr_mult: float | None = Field(None, gt=0, le=20)
    atr_period: int = Field(14, ge=2, le=100)

    @model_validator(mode="after")
    def _one(self) -> "IndexLevel":
        if sum(x is not None for x in (self.points, self.pct, self.atr_mult)) != 1:
            raise ValueError("give exactly one of points, pct or atr_mult")
        return self


class Exits(_Strict):
    stop_loss_pct: float = Field(gt=0, lt=100)
    target_pct: float = Field(gt=0, le=1000)
    trail: Trail | None = None
    time_exit_bars: int | None = Field(None, ge=1, le=375)
    conditions: Condition | None = None  # exit the position when true (checked on bar close)
    eod: str = "15:15"
    # Exits on the INDEX (PRD §24.8 B7.2): e.g. stop 1 x ATR(14) below the index at entry for a call. They work next to
    # the premium stop/target above; whichever is hit first closes the trade (a stop wins a tie).
    index_stop: IndexLevel | None = None
    index_target: IndexLevel | None = None


class OptionSelection(_Strict):
    # ATM, or 1-3 strikes in / out of the money (PRD §24.8 B7.1)
    strike: Literal["ATM", "ITM1", "ITM2", "ITM3", "OTM1", "OTM2", "OTM3"] = "ATM"
    # buy the strike whose delta is closest to this instead (e.g. 0.4); volatility = INDIA VIX x multiplier
    delta: float | None = Field(None, ge=0.1, le=0.9)
    expiry: Literal["nearest"] = "nearest"
    roll_on_expiry_day: bool = True

    @property
    def code(self) -> str:
        """The selection as the backtester and the paper runner read it: ATM, ITM1, OTM2 or D40."""
        return f"D{round(self.delta * 100)}" if self.delta is not None else self.strike


class Risk(_Strict):
    max_trades_per_day: int = Field(3, ge=1, le=20)
    # Size each trade so that hitting the stop loses about this % of capital (lots = budget / loss per lot, rounded
    # down; a trade whose single lot would risk more than the budget is skipped). None = the fixed number of lots.
    risk_per_trade_pct: float | None = Field(None, gt=0, le=5)
    # Refuse the spec unless target % / stop-loss % is at least this (e.g. 3 = reward at least 3x the risk).
    min_reward_risk: float | None = Field(None, ge=1, le=20)
    # Stock strategies: the most positions held at the same time; each gets an equal share of capital (or less when
    # risk_per_trade_pct is set).
    max_positions: int = Field(5, ge=1, le=50)


class StrategySpec(_Strict):
    spec_version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    # "options" = intraday NIFTY/BANKNIFTY option buying; "equity" = daily-bar delivery trades in NSE stocks
    market: Literal["options", "equity"] = "options"
    underlyings: list[str] = Field(default_factory=lambda: ["NIFTY"], min_length=1)  # equity: stock symbols or NIFTY50
    timeframe: Literal["1m", "2m", "3m", "5m", "10m", "15m", "20m", "30m", "60m", "1d"] = "5m"
    sides: Sides
    filters: Filters = Field(default_factory=Filters)
    option_selection: OptionSelection = Field(default_factory=OptionSelection)
    exits: Exits
    risk: Risk = Field(default_factory=Risk)

    @model_validator(mode="after")
    def _universe(self) -> "StrategySpec":
        from trader.instruments.equities import UNIVERSES, is_equity_symbol

        if self.market == "options":
            bad = [u for u in self.underlyings if u not in ("NIFTY", "BANKNIFTY")]
            if bad:
                raise ValueError(f"options strategies trade NIFTY or BANKNIFTY, not {', '.join(bad)}")
        else:
            bad = [u for u in self.underlyings if u not in UNIVERSES and not is_equity_symbol(u)]
            if bad:
                raise ValueError(f"unknown stock(s) {', '.join(bad)}: use NIFTY 50 symbols or the name NIFTY50")
        return self

    def canonical_json(self) -> str:
        d = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        if self.market == "options":  # keep the hash of every existing options strategy unchanged
            d.pop("market", None)
            d["risk"].pop("max_positions", None)
        return json.dumps(d, sort_keys=True, separators=(",", ":"))

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
    errs += _tf_errors(spec, conds)
    if spec.market == "equity":
        if spec.exits.index_stop or spec.exits.index_target or spec.option_selection.strike != "ATM" \
                or spec.option_selection.delta is not None:
            errs.append("index exits and strike choice are for option strategies")
        return errs + _equity_errors(spec, conds)
    if spec.timeframe == "1d":
        errs.append("daily candles are for stock strategies (market \"equity\"); option strategies use 1m-60m")
    if spec.sides.long and spec.sides.long.option != "CE":
        errs.append("long side must buy CE (long options only, PRD §3)")
    if spec.sides.short and spec.sides.short.option != "PE":
        errs.append("short side must buy PE (long options only, PRD §3)")
    rr = spec.risk.min_reward_risk
    if rr is not None and spec.exits.target_pct / spec.exits.stop_loss_pct < rr:
        errs.append(f"reward-to-risk is {spec.exits.target_pct / spec.exits.stop_loss_pct:.2f} "
                    f"(target {spec.exits.target_pct:g}% / stop {spec.exits.stop_loss_pct:g}%), below the minimum {rr:g}")
    tw_end = time.fromisoformat(spec.filters.time_window[1])
    if tw_end > time.fromisoformat(spec.exits.eod):
        errs.append("entries are allowed after the EOD exit time")
    return errs


TF_BLOCKED = {  # operands that exist only for the strategy's own bars (session or daily context), not for built candles
    "or_high", "or_low", "prev_day_high", "prev_day_low", "prev_day_close", "fut_close", "vwap", "vix", "daily_ema", "daily_sma",
    "daily_rsi", "pivot", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2", "cpr_tc", "cpr_bc",
}


def _tf_errors(spec: StrategySpec, conds: list[tuple[str, object]]) -> list[str]:
    from trader.market_data.resample import SUPPORTED

    errs: list[str] = []
    base = SUPPORTED.get(spec.timeframe)
    for where, c in conds:
        for o in walk_operands(c):
            if not (isinstance(o, Ind) and o.tf):
                continue
            if spec.market == "equity" or base is None:
                errs.append(f"{where}: other timeframes are for intraday strategies (the stock strategy already runs on daily candles)")
            elif TF_MINUTES[o.tf] <= base or TF_MINUTES[o.tf] % base:
                errs.append(f"{where}: {o.ind} on {o.tf} candles needs a timeframe that is a whole multiple of the strategy's "
                            f"own {spec.timeframe} candles and larger than it")
            if o.ind in TF_BLOCKED:
                errs.append(f"{where}: `{o.ind}` cannot be used on another timeframe (it is a session or daily value)")
    return list(dict.fromkeys(errs))


EQUITY_BLOCKED = {  # operands that only exist for intraday index/option data
    "or_high": "the opening range", "or_low": "the opening range", "fut_close": "futures", "vwap": "futures VWAP",
    "vix": "INDIA VIX", "daily_ema": "a separate daily context (the strategy already runs on daily candles)",
    "daily_sma": "a separate daily context (the strategy already runs on daily candles)",
    "daily_rsi": "a separate daily context (the strategy already runs on daily candles)",
}


def _equity_errors(spec: StrategySpec, conds: list[tuple[str, object]]) -> list[str]:
    errs: list[str] = []
    if spec.timeframe != "1d":
        errs.append("stock strategies run on daily candles: set timeframe to \"1d\"")
    if spec.sides.short is not None:
        errs.append("stock strategies are long-only (buy and hold for days); remove the short side")
    for name in ("long",):
        side = getattr(spec.sides, name)
        if side and side.mirror_of:
            errs.append("a stock strategy needs its own long entry rules (nothing to mirror)")
    for where, c in conds:
        for o in walk_operands(c):
            if isinstance(o, Ind) and o.ind in EQUITY_BLOCKED:
                errs.append(f"{where}: `{o.ind}` is not available for stocks ({EQUITY_BLOCKED[o.ind]})")
    rr = spec.risk.min_reward_risk
    if rr is not None and spec.exits.target_pct / spec.exits.stop_loss_pct < rr:
        errs.append(f"reward-to-risk is {spec.exits.target_pct / spec.exits.stop_loss_pct:.2f} "
                    f"(target {spec.exits.target_pct:g}% / stop {spec.exits.stop_loss_pct:g}%), below the minimum {rr:g}")
    return errs


# ---- mirroring ------------------------------------------------------------------------------
def mirror_condition(c: object) -> object:
    """Long <-> short: flip comparisons, swap high/low-type operands, reflect bounded oscillators."""
    def mo(o: Operand) -> Operand:
        if isinstance(o, Ind):
            return Ind(ind=MIRROR_NAME.get(o.ind, o.ind), period=o.period, mult=o.mult, tf=o.tf, shift=o.shift)
        return o

    def reflect(ind: Operand, const: Operand) -> Operand:
        if not isinstance(const, Const) or not isinstance(ind, Ind):
            return const
        if ind.ind in ("rsi", "daily_rsi") or ind.ind in REFLECT_100:
            return Const(const=100 - const.const)
        if ind.ind == "williams_r":
            return Const(const=-100 - const.const)
        if ind.ind == "bb_pctb":
            return Const(const=1 - const.const)
        if ind.ind in REFLECT_NEG:
            return Const(const=0.0 - const.const)
        if ind.ind in ("macd", "macd_signal", "macd_hist", "roc"):
            return Const(const=0.0 - const.const)
        return const

    if isinstance(c, Cmp):
        a, op, b = c.cmp
        if any(isinstance(x, Ind) and INDICATORS[x.ind][3] == "flag" for x in (a, b)):
            return Cmp(cmp=(mo(a), op, mo(b)))  # a pattern flag stays "present": only its name is swapped
        return Cmp(cmp=(mo(reflect(b, a)), MIRROR_OP[op], mo(reflect(a, b))))
    if isinstance(c, Cross):
        a, d, b = c.cross
        return Cross(cross=(mo(reflect(b, a)), "below" if d == "above" else "above", mo(reflect(a, b))), within=c.within)
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
    txt = label.format(p=o.period, m=f"{o.effective_mult:g}" if o.effective_mult is not None else "")
    if o.tf:
        txt += f" on {o.tf} candles"
    if o.shift:
        txt += f" {o.shift} bar{'s' if o.shift > 1 else ''} ago"
    return txt


_OPS = {">": "is above", "<": "is below", ">=": "is at or above", "<=": "is at or below"}
_CMP = {">": lambda x, y: x > y, "<": lambda x, y: x < y, ">=": lambda x, y: x >= y, "<=": lambda x, y: x <= y}


def _flag_text(a: Operand, op: str, b: Operand) -> str | None:
    """'bullish engulfing candle is present' for a comparison of a 0/1 pattern flag with a constant."""
    if isinstance(a, Const) and isinstance(b, Ind):
        a, b, op = b, a, MIRROR_OP[op]
    if not (isinstance(a, Ind) and isinstance(b, Const) and INDICATORS[a.ind][3] == "flag"):
        return None
    on, off = _CMP[op](1.0, b.const), _CMP[op](0.0, b.const)
    if on and not off:
        return f"{_op_text(a)} is present"
    if off and not on:
        return f"{_op_text(a)} is not present"
    return None


def render_condition(c: object, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(c, Cmp):
        a, op, b = c.cmp
        flag = _flag_text(a, op, b)
        return f"{pad}- {flag or f'{_op_text(a)} {_OPS[op]} {_op_text(b)}'}"
    if isinstance(c, Cross):
        a, d, b = c.cross
        when = f"on this bar or any of the previous {c.within - 1} bars" if c.within else "on this bar's close"
        return f"{pad}- {_op_text(a)} crosses {d} {_op_text(b)} ({when})"
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


def _render_equity(spec: StrategySpec) -> str:
    from trader.instruments.equities import UNIVERSES

    names = ", ".join(spec.underlyings)
    n = sum(len(UNIVERSES.get(u, (u,))) for u in spec.underlyings)
    lines = [f"Strategy: {spec.name}",
             f"Stocks: {names} ({n} stock{'s' if n != 1 else ''}) · daily candles · decisions on the day's close, "
             f"orders at the next day's open"]
    long_ = spec.sides.long
    if long_ is not None and long_.entry is not None:
        lines += ["\nBuy the shares (delivery) when ALL of these are true:", render_condition(long_.entry)]
    f = spec.filters
    if f.all:
        lines.append("\nAnd only when:")
        lines += [render_condition(c) for c in f.all]
    e = spec.exits
    lines.append(f"\nExit: stop loss {e.stop_loss_pct:g}% below and target {e.target_pct:g}% above the entry price"
                 + (f"; once up {e.trail.activate_pct:g}%, trail the stop {e.trail.trail_pct:g}% below the best price"
                    if e.trail else "")
                 + (f"; exit after {e.time_exit_bars} trading days" if e.time_exit_bars else "") + ".")
    if e.conditions is not None:
        lines += ["Also exit at the next day's open when:", render_condition(e.conditions)]
    r = spec.risk
    sizing = (f"size each trade so the stop loses about {r.risk_per_trade_pct:g}% of capital (never more than an equal "
              f"1/{r.max_positions} share of it); " if r.risk_per_trade_pct
              else f"put an equal 1/{r.max_positions} share of capital in each trade; ")
    lines.append(f"\nRisk: {sizing}at most {r.max_positions} position{'s' if r.max_positions != 1 else ''} at a time, "
                 f"one per stock.")
    return "\n".join(lines).replace("futures volume", "volume")


def render_english(spec: StrategySpec) -> str:
    if spec.market == "equity":
        return _render_equity(spec)
    lines = [f"Strategy: {spec.name}",
             f"Underlying: {', '.join(spec.underlyings)} · {spec.timeframe} candles · decisions on candle close, "
             f"orders at the next candle's open"]
    o = spec.option_selection
    which = (f"delta-{o.delta:g}" if o.delta is not None else "ATM" if o.strike == "ATM"
             else f"{o.strike[3]}-strike {'in' if o.strike.startswith('ITM') else 'out of'} the money")
    for side, verb in (("long", f"Buy the {which} call (CE)"), ("short", f"Buy the {which} put (PE)")):
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
    for lvl, what in ((e.index_stop, "stop"), (e.index_target, "target")):
        if lvl is not None:
            dist = (f"{lvl.points:g} points" if lvl.points is not None else f"{lvl.pct:g}% of the index"
                    if lvl.pct is not None else f"{lvl.atr_mult:g} x ATR({lvl.atr_period})")
            lines.append(f"Index {what}: exit when the index moves {dist} {'against' if what == 'stop' else 'in favour of'} "
                         "the trade from its price at entry.")
    if e.conditions is not None:
        lines.append("Also exit at the next candle's open when:")
        lines.append(render_condition(e.conditions))
    n = spec.risk.max_trades_per_day
    sizing = (f"size each trade so the stop loses about {spec.risk.risk_per_trade_pct:g}% of capital; "
              if spec.risk.risk_per_trade_pct else "")
    lines.append(f"\nRisk: {sizing}at most {n} trade{'s' if n != 1 else ''} per day, one position at a time. "
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


# ---- example stock spec (for the translator and the template picker) ---------------------------------------
EQUITY_EXAMPLE = {
    "market": "equity",
    "name": "Pullback in an uptrend (daily)",
    "underlyings": ["NIFTY50"],
    "timeframe": "1d",
    "sides": {"long": {"entry": {"all": [
        {"cmp": [{"ind": "close"}, ">", {"ind": "sma", "period": 200}]},
        {"cmp": [{"ind": "rsi", "period": 14}, "<", {"const": 40}]},
        {"cmp": [{"ind": "cs_bull_engulfing"}, ">", {"const": 0}]},
    ]}}},
    "exits": {"stop_loss_pct": 6, "target_pct": 18, "time_exit_bars": 20,
              "conditions": {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 70}]}},
    "risk": {"max_positions": 5, "risk_per_trade_pct": 1, "min_reward_risk": 3},
}
