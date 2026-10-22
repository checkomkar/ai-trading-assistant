"""Ready-made strategies (Strategy Spec v1) so the Strategies tab isn't empty.

These are starting points to backtest, not recommendations: none is validated out-of-sample.
Long sides buy a CE, short sides mirror into a PE. Save them with
`python -m trader.scripts.seed_strategies`.
"""

from __future__ import annotations

from trader.strategy.spec import MOMENTUM_V1


def _ind(name: str, period: int | None = None) -> dict:
    return {"ind": name} if period is None else {"ind": name, "period": period}


def _cmp(a: dict, op: str, b: dict | float) -> dict:
    return {"cmp": [a, op, b if isinstance(b, dict) else {"const": b}]}


def _cross(a: dict, d: str, b: dict | float) -> dict:
    return {"cross": [a, d, b if isinstance(b, dict) else {"const": b}]}


def _spec(name: str, description: str, timeframe: str, entry: list[dict], filters: list[dict], window: tuple[str, str],
          sl: float, target: float, trail: tuple[float, float] | None, trades: int,
          time_exit_bars: int | None = None) -> dict:
    exits: dict = {"stop_loss_pct": sl, "target_pct": target}
    if trail:
        exits["trail"] = {"activate_pct": trail[0], "trail_pct": trail[1]}
    if time_exit_bars:
        exits["time_exit_bars"] = time_exit_bars
    return {"name": name, "description": description, "underlyings": ["NIFTY"], "timeframe": timeframe,
            "sides": {"long": {"option": "CE", "entry": {"all": entry}}, "short": {"option": "PE", "mirror_of": "long"}},
            "filters": {"time_window": list(window), "all": filters}, "exits": exits,
            "risk": {"max_trades_per_day": trades}}


_close, _fut, _vwap = _ind("close"), _ind("fut_close"), _ind("vwap")
_adx, _rsi = _ind("adx", 14), _ind("rsi", 14)

LIBRARY: list[dict] = [
    # ---- momentum / trend ----
    _spec("Opening Range Breakout (5m)",
          "Momentum: close breaks the opening range with futures on the right side of VWAP and a trending ADX.",
          "5m", [_cmp(_close, ">", _ind("or_high")), _cmp(_fut, ">", _vwap), _cmp(_rsi, ">", 55)],
          [_cmp(_adx, ">=", 18)], ("09:45", "13:30"), 25, 50, (30, 15), 2),
    _spec("Previous-Day High Breakout (5m)",
          "Momentum: close above yesterday's high, with RSI and futures-VWAP confirming; mirrored under yesterday's low.",
          "5m", [_cmp(_close, ">", _ind("prev_day_high")), _cmp(_fut, ">", _vwap), _cmp(_rsi, ">", 55)],
          [_cmp(_adx, ">=", 18)], ("09:30", "14:00"), 25, 50, (30, 15), 2),
    _spec("MACD Momentum (5m)",
          "Momentum: MACD crosses its signal line above zero while price is above EMA(50).",
          "5m", [_cross(_ind("macd"), "above", _ind("macd_signal")), _cmp(_ind("macd"), ">", 0),
                 _cmp(_close, ">", _ind("ema", 50)), _cmp(_rsi, ">", 50)],
          [_cmp(_adx, ">=", 18)], ("09:45", "14:30"), 25, 50, (30, 15), 3),
    _spec("Trend Pullback to EMA (5m)",
          "Trend: EMA(21) above EMA(50) and price reclaims EMA(9) after a dip, futures above VWAP.",
          "5m", [_cmp(_ind("ema", 21), ">", _ind("ema", 50)), _cross(_close, "above", _ind("ema", 9)),
                 _cmp(_fut, ">", _vwap)],
          [_cmp(_adx, ">=", 20)], ("09:45", "14:30"), 20, 40, (25, 12), 3),
    _spec("ADX Trend Rider (15m)",
          "Trend: strong ADX with +DI over -DI and price above EMA(20) on 15-minute candles.",
          "15m", [_cmp(_adx, ">", 25), _cmp(_ind("plus_di", 14), ">", _ind("minus_di", 14)),
                  _cmp(_close, ">", _ind("ema", 20)), _cmp(_fut, ">", _vwap)],
          [], ("09:45", "14:00"), 25, 60, (35, 15), 2),
    # ---- scalping (short holding time, tight stops) ----
    _spec("VWAP Cross Scalp (3m)",
          "Scalp: futures cross VWAP with EMA(9) over EMA(21) and RSI support; exits within 10 candles.",
          "3m", [_cross(_fut, "above", _vwap), _cmp(_ind("ema", 9), ">", _ind("ema", 21)), _cmp(_rsi, ">", 52)],
          [_cmp(_adx, ">=", 15)], ("09:30", "14:30"), 12, 20, (12, 6), 6, time_exit_bars=10),
    _spec("EMA 5/13 Cross Scalp (1m)",
          "Scalp: fast EMA(5) crosses EMA(13) on 1-minute candles with futures above VWAP; exits within 15 candles.",
          "1m", [_cross(_ind("ema", 5), "above", _ind("ema", 13)), _cmp(_fut, ">", _vwap)],
          [_cmp(_adx, ">=", 20)], ("09:30", "14:30"), 10, 18, (10, 5), 6, time_exit_bars=15),
    _spec("Momentum Burst Scalp (3m)",
          "Scalp: sharp rate-of-change with a close above the last 5 bars' high when volatility is awake.",
          "3m", [_cmp(_ind("roc", 3), ">", 0.12), _cmp(_close, ">", _ind("highest", 5)), _cmp(_fut, ">", _vwap)],
          [_cmp(_ind("atr_pct", 14), ">=", 0.05)], ("09:30", "14:30"), 12, 22, (12, 6), 5, time_exit_bars=8),
    # ---- sideways / mean reversion ----
    _spec("Bollinger Band Reversion (5m)",
          "Sideways: buy the call on a close below the lower band with RSI oversold; puts on the upper band. Needs low ADX.",
          "5m", [_cmp(_close, "<", _ind("bb_lower", 20)), _cmp(_rsi, "<", 35)],
          [_cmp(_adx, "<", 20)], ("10:00", "14:00"), 15, 25, None, 3, time_exit_bars=12),
    _spec("RSI Range Reversal (5m)",
          "Sideways: RSI turns back up through 30 (calls) or down through 70 (puts) when the market is not trending.",
          "5m", [_cross(_rsi, "above", 30)],
          [_cmp(_adx, "<", 20)], ("10:00", "14:00"), 15, 25, None, 3, time_exit_bars=12),
    _spec("VWAP Mean Reversion (5m)",
          "Sideways: fade a stretch below VWAP (calls) or above it (puts) when RSI is extreme and ADX is low.",
          "5m", [_cmp(_fut, "<", _vwap), _cmp(_rsi, "<", 32), _cmp(_close, "<", _ind("bb_mid", 20))],
          [_cmp(_adx, "<", 18)], ("10:00", "14:00"), 15, 25, None, 3, time_exit_bars=12),
]

assert MOMENTUM_V1  # the PRD reference spec stays the first saved strategy


def _stock(name: str, description: str, entry: list[dict], sl: float, target: float, trail: tuple[float, float] | None = None,
           days: int | None = None, exit_when: dict | None = None, risk_pct: float | None = 1.0, positions: int = 5) -> dict:
    """A daily-chart, long-only, delivery strategy on the NIFTY 50 (see backtest/equity.py)."""
    exits: dict = {"stop_loss_pct": sl, "target_pct": target}
    if trail:
        exits["trail"] = {"activate_pct": trail[0], "trail_pct": trail[1]}
    if days:
        exits["time_exit_bars"] = days
    if exit_when:
        exits["conditions"] = exit_when
    risk: dict = {"max_positions": positions}
    if risk_pct:
        risk["risk_per_trade_pct"] = risk_pct
    return {"market": "equity", "name": name, "description": description, "underlyings": ["NIFTY50"], "timeframe": "1d",
            "sides": {"long": {"entry": {"all": entry}}}, "exits": exits, "risk": risk}


_sma200, _sma50 = _ind("sma", 200), _ind("sma", 50)

STOCK_LIBRARY: list[dict] = [
    _stock("Pullback in an Uptrend (daily)",
           "Stocks: buy a dip in a stock above its 200-day average when RSI is low and a bullish engulfing candle forms; "
           "sold at RSI 70, after 20 days, or at the stop / target.",
           [_cmp(_close, ">", _sma200), _cmp(_rsi, "<", 40), _cmp(_ind("cs_bull_engulfing"), ">", 0)],
           6, 18, days=20, exit_when=_cmp(_rsi, ">", 70)),
    _stock("50-Day Breakout on Volume (daily)",
           "Stocks: close above the previous 50 days' high on heavy volume while above the 200-day average; a trailing "
           "stop lets winners run.",
           [_cmp(_close, ">", _ind("highest", 50)), _cmp(_ind("rel_volume", 20), ">", 1.5), _cmp(_close, ">", _sma200)],
           7, 30, trail=(10, 6)),
    _stock("Golden Cross 50/200 (daily)",
           "Stocks: 50-day average crosses above the 200-day average; held until it crosses back below (a long-term "
           "trend follower, few trades).",
           [_cross(_sma50, "above", _sma200)], 12, 80, exit_when=_cross(_sma50, "below", _sma200), risk_pct=None, positions=10),
    _stock("RSI Oversold Bounce (daily)",
           "Stocks: RSI under 30 while the stock is still above its 200-day average; exits when RSI recovers above 55 "
           "or after 10 days.",
           [_cmp(_rsi, "<", 30), _cmp(_close, ">", _sma200)], 8, 12, days=10, exit_when=_cmp(_rsi, ">", 55)),
    _stock("Hammer at a Swing Low (daily)",
           "Stocks: a hammer candle that probes below the latest swing low (3 bars each side) in an uptrend; a 1:3 "
           "reward-to-risk setup.",
           [_cmp(_ind("cs_hammer"), ">", 0), _cmp(_ind("low"), "<=", _ind("swing_low", 3)), _cmp(_close, ">", _sma200)],
           5, 15, days=15),
    _stock("MACD Trend Follower (daily)",
           "Stocks: MACD crosses above its signal line while the stock is above its 50-day average; exits when MACD "
           "crosses back below.",
           [_cross(_ind("macd"), "above", _ind("macd_signal")), _cmp(_close, ">", _sma50)],
           8, 40, trail=(12, 7), exit_when=_cross(_ind("macd"), "below", _ind("macd_signal"))),
]

CATEGORIES = ("Momentum", "Trend", "Scalp", "Sideways", "Stocks")


def catalogue() -> list[dict]:
    """The ready-made strategies for the Strategies tab, grouped by style. The style is the description's first word
    (Momentum / Trend / Scalp / Sideways); the PRD reference spec is listed under Momentum."""
    from trader.strategy.spec import StrategySpec, render_english

    out = []
    for raw in [MOMENTUM_V1, *LIBRARY, *STOCK_LIBRARY]:
        spec = StrategySpec.model_validate(raw)
        head = (raw.get("description") or "").split(":", 1)[0].strip()
        category = head if head in CATEGORIES else "Momentum"
        out.append({"name": spec.name, "category": category, "description": raw.get("description", ""),
                    "timeframe": spec.timeframe, "english": render_english(spec),
                    "spec": spec.model_dump(mode="json", by_alias=True, exclude_none=True)})
    return sorted(out, key=lambda e: CATEGORIES.index(e["category"]))
