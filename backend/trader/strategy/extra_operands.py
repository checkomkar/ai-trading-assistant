"""Spec operands for the extra indicators (indicators/extra.py): their labels, valid periods and where each value comes from.

`EXTRA_INDICATORS` has the same shape as `spec.INDICATORS`: name -> (needs a period, period bounds, English label, scale).
`SOURCE` says which indicator object produces the operand and which of its outputs it is. Names follow TradingView's
(`ta.supertrend`, `ta.hma`, `ta.stoch` ...) and so do the default settings; where an indicator has a second setting
(Supertrend's factor, the Keltner and Bollinger multiplier) it is the optional `mult` on the operand.
"""

from __future__ import annotations

_P = (2, 200)
_P100 = (2, 100)

EXTRA_INDICATORS: dict[str, tuple[bool, tuple[int, int] | None, str, str]] = {
    # moving averages
    "wma": (True, _P, "WMA({p})", "index"),
    "hma": (True, _P, "Hull MA({p})", "index"),
    "vwma": (True, _P, "volume-weighted MA({p})", "index"),
    "dema": (True, _P, "DEMA({p})", "index"),
    "tema": (True, _P, "TEMA({p})", "index"),
    "rma": (True, _P, "RMA({p}) (Wilder's smoothed average)", "index"),
    "linreg": (True, _P, "linear regression value({p})", "index"),
    # trend
    "supertrend": (True, _P100, "Supertrend({p}, {m})", "index"),
    "supertrend_dir": (True, _P100, "Supertrend({p}, {m}) direction (+1 up, -1 down)", "osc"),
    "psar": (False, None, "parabolic SAR", "index"),
    "aroon_up": (True, _P100, "Aroon up({p})", "osc"),
    "aroon_down": (True, _P100, "Aroon down({p})", "osc"),
    "aroon_osc": (True, _P100, "Aroon oscillator({p})", "osc"),
    "vi_plus": (True, _P100, "Vortex +VI({p})", "osc"),
    "vi_minus": (True, _P100, "Vortex -VI({p})", "osc"),
    "ichi_tenkan": (False, None, "Ichimoku conversion line (9)", "index"),
    "ichi_kijun": (False, None, "Ichimoku base line (26)", "index"),
    "ichi_span_a": (False, None, "Ichimoku cloud span A (today's cloud)", "index"),
    "ichi_span_b": (False, None, "Ichimoku cloud span B (today's cloud)", "index"),
    # oscillators
    "stoch_k": (True, _P100, "Stochastic %K({p},3,3)", "osc"),
    "stoch_d": (True, _P100, "Stochastic %D({p},3,3)", "osc"),
    "stoch_rsi_k": (True, _P100, "Stochastic RSI %K({p})", "osc"),
    "stoch_rsi_d": (True, _P100, "Stochastic RSI %D({p})", "osc"),
    "cci": (True, _P, "CCI({p})", "cci"),
    "williams_r": (True, _P100, "Williams %R({p})", "osc"),
    "mfi": (True, _P100, "Money Flow Index({p})", "osc"),
    "mom": (True, (1, 100), "momentum({p})", "points"),
    "cmo": (True, _P100, "Chande momentum({p})", "cci"),
    "ao": (False, None, "Awesome Oscillator", "points"),
    "trix": (True, _P100, "TRIX({p})", "cci"),
    "ult_osc": (False, None, "Ultimate Oscillator", "osc"),
    # volume
    "obv": (False, None, "on-balance volume", "volume"),
    "ad_line": (False, None, "accumulation/distribution line", "volume"),
    "cmf": (True, _P100, "Chaikin money flow({p})", "pct"),
    # channels and bands
    "keltner_upper": (True, _P, "upper Keltner channel({p}, {m})", "index"),
    "keltner_mid": (True, _P, "middle Keltner channel({p})", "index"),
    "keltner_lower": (True, _P, "lower Keltner channel({p}, {m})", "index"),
    "donchian_high": (True, _P, "Donchian channel high({p})", "index"),
    "donchian_low": (True, _P, "Donchian channel low({p})", "index"),
    "donchian_mid": (True, _P, "Donchian channel middle({p})", "index"),
    "bb_pctb": (True, (5, 100), "Bollinger %B({p}, {m})", "pct"),
    "bb_width": (True, (5, 100), "Bollinger bandwidth({p}, {m})", "pct"),
    "stddev": (True, _P100, "standard deviation({p})", "points"),
    # prices and pivots
    "hl2": (False, None, "(high + low) / 2", "index"),
    "hlc3": (False, None, "(high + low + close) / 3", "index"),
    "ohlc4": (False, None, "(open + high + low + close) / 4", "index"),
    "true_range": (False, None, "true range", "points"),
    "pivot": (False, None, "classic pivot point", "index"),
    "pivot_r1": (False, None, "pivot resistance 1", "index"),
    "pivot_r2": (False, None, "pivot resistance 2", "index"),
    "pivot_s1": (False, None, "pivot support 1", "index"),
    "pivot_s2": (False, None, "pivot support 2", "index"),
}

MULT_DEFAULTS: dict[str, float] = {  # operands with a second setting, and TradingView's default for it
    "supertrend": 3.0, "supertrend_dir": 3.0, "keltner_upper": 2.0, "keltner_mid": 2.0, "keltner_lower": 2.0,
    "bb_pctb": 2.0, "bb_width": 2.0,
}

# operand -> (indicator object kind, output name)
SOURCE: dict[str, tuple[str, str]] = {
    **{n: (n, "value") for n in ("wma", "hma", "vwma", "dema", "tema", "rma", "linreg", "cci", "williams_r", "mfi", "mom", "cmo",
                                 "ao", "trix", "ult_osc", "obv", "ad_line", "cmf", "stddev")},
    "supertrend": ("supertrend", "line"), "supertrend_dir": ("supertrend", "dir"), "psar": ("psar", "value"),
    "aroon_up": ("aroon", "up"), "aroon_down": ("aroon", "down"), "aroon_osc": ("aroon", "osc"),
    "vi_plus": ("vortex", "plus"), "vi_minus": ("vortex", "minus"),
    "ichi_tenkan": ("ichimoku", "tenkan"), "ichi_kijun": ("ichimoku", "kijun"),
    "ichi_span_a": ("ichimoku", "span_a"), "ichi_span_b": ("ichimoku", "span_b"),
    "stoch_k": ("stoch", "k"), "stoch_d": ("stoch", "d"), "stoch_rsi_k": ("stoch_rsi", "k"), "stoch_rsi_d": ("stoch_rsi", "d"),
    "keltner_upper": ("keltner", "upper"), "keltner_mid": ("keltner", "mid"), "keltner_lower": ("keltner", "lower"),
    "donchian_high": ("donchian", "high"), "donchian_low": ("donchian", "low"), "donchian_mid": ("donchian", "mid"),
    "bb_pctb": ("bb_extra", "pctb"), "bb_width": ("bb_extra", "width"),
    "hl2": ("prices", "hl2"), "hlc3": ("prices", "hlc3"), "ohlc4": ("prices", "ohlc4"), "true_range": ("prices", "tr"),
}
PIVOTS = {"pivot": "pivot", "pivot_r1": "r1", "pivot_r2": "r2", "pivot_s1": "s1", "pivot_s2": "s2"}  # from the previous day

# mirroring a long rule into a short one
MIRROR = {"keltner_upper": "keltner_lower", "keltner_lower": "keltner_upper", "donchian_high": "donchian_low",
          "donchian_low": "donchian_high", "pivot_r1": "pivot_s1", "pivot_s1": "pivot_r1", "pivot_r2": "pivot_s2",
          "pivot_s2": "pivot_r2"}
REFLECT_100 = {"stoch_k", "stoch_d", "stoch_rsi_k", "stoch_rsi_d", "mfi", "ult_osc", "aroon_up", "aroon_down"}  # x -> 100 - x
REFLECT_NEG = {"cci", "mom", "cmo", "ao", "trix", "aroon_osc", "supertrend_dir", "cmf", "obv", "ad_line"}  # x -> -x
