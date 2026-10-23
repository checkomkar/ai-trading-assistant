"""The indicator catalogue (PRD §24.4 B3.1): one list for the strategy spec, the chart and indicator search.

Every entry is an operand the spec interpreter already computes (`spec.INDICATORS`, which includes the extra
TradingView-style ones); this module only adds what a person searching for an indicator needs: a category,
search words, TradingView's default settings, and the pane it is drawn in. Nothing here computes a value, so the
chart can never show a number the engine would not have used.
"""

from __future__ import annotations

from trader.strategy.extra_operands import MULT_DEFAULTS
from trader.strategy.spec import INDICATORS

CATEGORIES: dict[str, tuple[str, ...]] = {
    "Price": ("open", "high", "low", "close", "hl2", "hlc3", "ohlc4"),
    "Moving averages": ("ema", "sma", "wma", "hma", "vwma", "dema", "tema", "rma", "linreg"),
    "Trend": ("supertrend", "supertrend_dir", "psar", "adx", "plus_di", "minus_di", "aroon_up", "aroon_down", "aroon_osc",
              "vi_plus", "vi_minus", "ichi_tenkan", "ichi_kijun", "ichi_span_a", "ichi_span_b", "macd", "macd_signal",
              "macd_hist"),
    "Oscillators": ("rsi", "stoch_k", "stoch_d", "stoch_rsi_k", "stoch_rsi_d", "cci", "williams_r", "mfi", "mom", "cmo", "ao",
                    "trix", "ult_osc", "roc"),
    "Volatility and bands": ("atr", "atr_pct", "true_range", "stddev", "bb_upper", "bb_mid", "bb_lower", "bb_pctb", "bb_width",
                             "keltner_upper", "keltner_mid", "keltner_lower", "donchian_high", "donchian_mid", "donchian_low",
                             "vix"),
    "Levels": ("highest", "lowest", "swing_high", "swing_low", "or_high", "or_low", "prev_day_high", "prev_day_low",
               "prev_day_close", "pivot", "pivot_r1", "pivot_r2", "pivot_s1", "pivot_s2", "cpr_tc", "cpr_bc"),
    "Volume and futures": ("volume", "rel_volume", "obv", "ad_line", "cmf", "vwap", "fut_close"),
    "Daily context": ("daily_ema", "daily_sma", "daily_rsi"),
    "Patterns": ("cs_bull_engulfing", "cs_bear_engulfing", "cs_doji", "cs_hammer", "cs_shooting_star", "cs_morning_star",
                 "cs_evening_star", "rsi_bull_div", "rsi_bear_div"),
}

# Words people type when they look for an indicator (TradingView's names and the common abbreviations).
ALIASES: dict[str, tuple[str, ...]] = {
    "ema": ("exponential moving average", "moving average", "ma"),
    "sma": ("simple moving average", "moving average", "ma"),
    "wma": ("weighted moving average", "moving average"),
    "hma": ("hull moving average", "hull", "moving average"),
    "vwma": ("volume weighted moving average", "moving average"),
    "dema": ("double exponential moving average", "moving average"),
    "tema": ("triple exponential moving average", "moving average"),
    "rma": ("wilder", "smoothed moving average", "smma", "moving average"),
    "linreg": ("linear regression", "lsma"),
    "supertrend": ("super trend",), "supertrend_dir": ("super trend", "supertrend direction"),
    "psar": ("parabolic sar", "sar", "stop and reverse"),
    "adx": ("average directional index", "dmi", "directional movement"),
    "plus_di": ("dmi", "directional movement", "+di"), "minus_di": ("dmi", "directional movement", "-di"),
    "aroon_up": ("aroon",), "aroon_down": ("aroon",), "aroon_osc": ("aroon oscillator", "aroon"),
    "vi_plus": ("vortex",), "vi_minus": ("vortex",),
    "ichi_tenkan": ("ichimoku", "conversion line", "tenkan"), "ichi_kijun": ("ichimoku", "base line", "kijun"),
    "ichi_span_a": ("ichimoku", "cloud", "kumo", "senkou"), "ichi_span_b": ("ichimoku", "cloud", "kumo", "senkou"),
    "macd": ("moving average convergence divergence",), "macd_signal": ("macd",), "macd_hist": ("macd", "histogram"),
    "rsi": ("relative strength index",),
    "stoch_k": ("stochastic", "stoch"), "stoch_d": ("stochastic", "stoch"),
    "stoch_rsi_k": ("stochastic rsi", "stoch rsi"), "stoch_rsi_d": ("stochastic rsi", "stoch rsi"),
    "cci": ("commodity channel index",), "williams_r": ("williams %r", "%r", "williams"),
    "mfi": ("money flow index",), "mom": ("momentum",), "cmo": ("chande momentum oscillator", "chande"),
    "ao": ("awesome oscillator",), "trix": ("triple exponential",), "ult_osc": ("ultimate oscillator",),
    "roc": ("rate of change",),
    "atr": ("average true range",), "atr_pct": ("average true range", "atr percent"), "true_range": ("tr",),
    "stddev": ("standard deviation",),
    "bb_upper": ("bollinger bands", "bollinger"), "bb_mid": ("bollinger bands", "bollinger", "basis"),
    "bb_lower": ("bollinger bands", "bollinger"), "bb_pctb": ("bollinger %b", "percent b", "bollinger"),
    "bb_width": ("bollinger bandwidth", "bollinger"),
    "keltner_upper": ("keltner channels", "kc"), "keltner_mid": ("keltner channels", "kc"),
    "keltner_lower": ("keltner channels", "kc"),
    "donchian_high": ("donchian channels", "dc", "channel"), "donchian_mid": ("donchian channels", "dc"),
    "donchian_low": ("donchian channels", "dc", "channel"),
    "vix": ("india vix", "volatility index"),
    "highest": ("highest high", "breakout"), "lowest": ("lowest low", "breakdown"),
    "swing_high": ("resistance", "pivot high"), "swing_low": ("support", "pivot low"),
    "or_high": ("opening range", "orb"), "or_low": ("opening range", "orb"),
    "prev_day_high": ("pdh", "yesterday high"), "prev_day_low": ("pdl", "yesterday low"),
    "prev_day_close": ("pdc", "yesterday close"),
    "pivot": ("pivot points", "floor pivots"), "pivot_r1": ("pivot points", "resistance"),
    "pivot_r2": ("pivot points", "resistance"), "pivot_s1": ("pivot points", "support"), "pivot_s2": ("pivot points", "support"),
    "cpr_tc": ("cpr", "central pivot range"), "cpr_bc": ("cpr", "central pivot range"),
    "volume": ("vol",), "rel_volume": ("relative volume", "rvol"), "obv": ("on balance volume",),
    "ad_line": ("accumulation distribution",), "cmf": ("chaikin money flow",),
    "vwap": ("volume weighted average price",), "fut_close": ("futures",),
    "cs_bull_engulfing": ("candlestick", "engulfing"), "cs_bear_engulfing": ("candlestick", "engulfing"),
    "cs_doji": ("candlestick",), "cs_hammer": ("candlestick",), "cs_shooting_star": ("candlestick",),
    "cs_morning_star": ("candlestick",), "cs_evening_star": ("candlestick",),
    "rsi_bull_div": ("divergence", "rsi divergence"), "rsi_bear_div": ("divergence", "rsi divergence"),
}

# TradingView's default length for each indicator with a period (clamped into the spec's bounds below).
DEFAULT_PERIOD: dict[str, int] = {
    "ema": 9, "sma": 9, "wma": 9, "hma": 9, "vwma": 20, "dema": 9, "tema": 9, "rma": 14, "linreg": 25,
    "supertrend": 10, "supertrend_dir": 10, "adx": 14, "plus_di": 14, "minus_di": 14, "aroon_up": 14, "aroon_down": 14,
    "aroon_osc": 14, "vi_plus": 14, "vi_minus": 14, "rsi": 14, "stoch_k": 14, "stoch_d": 14, "stoch_rsi_k": 14,
    "stoch_rsi_d": 14, "cci": 20, "williams_r": 14, "mfi": 14, "mom": 10, "cmo": 9, "trix": 18, "roc": 9, "atr": 14,
    "atr_pct": 14, "stddev": 20, "bb_upper": 20, "bb_mid": 20, "bb_lower": 20, "bb_pctb": 20, "bb_width": 20,
    "keltner_upper": 20, "keltner_mid": 20, "keltner_lower": 20, "donchian_high": 20, "donchian_mid": 20,
    "donchian_low": 20, "highest": 20, "lowest": 20, "swing_high": 5, "swing_low": 5, "rel_volume": 20, "cmf": 20,
    "daily_ema": 20, "daily_sma": 20, "daily_rsi": 14, "rsi_bull_div": 30, "rsi_bear_div": 30,
}

# Where each "level" of `spec.INDICATORS` is drawn: on the candles, or in a pane of its own below them.
PANES = {"index": "price", "futures": "futures", "osc": "oscillator 0-100", "cci": "oscillator", "points": "points",
         "pct": "percent", "macd": "MACD", "vix": "VIX", "volume": "volume", "flag": "signals"}


def category_of(name: str) -> str:
    for cat, names in CATEGORIES.items():
        if name in names:
            return cat
    return "Other"


def default_period(name: str) -> int | None:
    needs, bounds, _, _ = INDICATORS[name]
    if not needs:
        return None
    lo, hi = bounds  # type: ignore[misc]
    return max(lo, min(hi, DEFAULT_PERIOD.get(name, lo)))


def label(name: str, period: int | None = None, mult: float | None = None) -> str:
    _, _, text, _ = INDICATORS[name]
    m = mult if mult is not None else MULT_DEFAULTS.get(name)
    return text.format(p=period if period is not None else "", m=f"{m:g}" if m is not None else "")


def catalogue() -> list[dict]:
    """Every indicator, in category order, with what the search box and the settings form need."""
    order = {n: i for i, n in enumerate(n for names in CATEGORIES.values() for n in names)}
    out = []
    for name, (needs, bounds, text, level) in sorted(INDICATORS.items(), key=lambda kv: (order.get(kv[0], 10_000), kv[0])):
        out.append({
            "name": name,
            "label": label(name, default_period(name)),
            "category": category_of(name),
            "aliases": list(ALIASES.get(name, ())),
            "pane": PANES.get(level, level),
            "level": level,
            "period": ({"default": default_period(name), "min": bounds[0], "max": bounds[1]} if needs else None),  # type: ignore[index]
            "mult": ({"default": MULT_DEFAULTS[name], "min": 0.1, "max": 10} if name in MULT_DEFAULTS else None),
        })
    return out


def search(query: str, limit: int = 30) -> list[dict]:
    """Catalogue entries matching every word of `query` in the name, label, aliases or category; best matches first."""
    words = [w for w in query.lower().split() if w]
    items = catalogue()
    if not words:
        return items[:limit]

    def score(e: dict) -> int | None:
        hay_name, hay_label = e["name"].lower(), e["label"].lower()
        hay = " ".join([hay_name, hay_label, e["category"].lower(), *e["aliases"]])
        if not all(w in hay for w in words):
            return None
        q = " ".join(words)
        return (0 if hay_name == q or q in e["aliases"] else 1 if hay_name.startswith(q) or hay_label.startswith(q) else 2)

    ranked = [(s, i, e) for i, e in enumerate(items) if (s := score(e)) is not None]
    return [e for _, _, e in sorted(ranked, key=lambda x: (x[0], x[1]))][:limit]
