"""FeatureProvider (PRD §12): turns closed bars into the feature set strategies read.

Price indicators run on the index. VWAP runs on near-month futures, because index bars
carry no traded volume (PRD §14.11): the momentum rule compares futures close with futures
VWAP. VIX is carried forward as the volatility context and synthetic IV proxy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from trader.indicators import ADX, ATR, EMA, RSI, HistoricalVolatility, RollingExtreme, SessionVWAP
from trader.instruments.calendar import IST
from trader.market_data.candles import Candle


@dataclass(frozen=True)
class FeatureConfig:
    ema_fast: int = 9
    ema_slow: int = 21
    rsi_period: int = 14
    atr_period: int = 14
    adx_period: int = 14
    breakout_lookback: int = 6  # bars; 6 x 5m = 30 minutes
    opening_range_minutes: int = 15


@dataclass
class Features:
    ts: datetime  # bar start, UTC
    day: date
    time_ist: time
    open: float
    high: float
    low: float
    close: float
    values: dict[str, float | None] = field(default_factory=dict)

    def __getitem__(self, key: str) -> float | None:
        return self.values.get(key)


class FeatureEngine:
    def __init__(self, timeframe_minutes: int, cfg: FeatureConfig | None = None) -> None:
        self.cfg = cfg = cfg or FeatureConfig()
        self.tf = timeframe_minutes
        self.ema_fast, self.ema_slow = EMA(cfg.ema_fast), EMA(cfg.ema_slow)
        self.rsi, self.atr, self.adx = RSI(cfg.rsi_period), ATR(cfg.atr_period), ADX(cfg.adx_period)
        self.hv = HistoricalVolatility(20, bars_per_year=252 * 375 / timeframe_minutes)
        self.extreme = RollingExtreme(cfg.breakout_lookback)
        self.vwap = SessionVWAP()
        self.vix: float | None = None
        self._day: date | None = None
        self._day_high = self._day_low = None
        self.prev_day_high: float | None = None
        self.prev_day_low: float | None = None
        self.prev_day_close: float | None = None
        self._last_close: float | None = None
        self.or_high: float | None = None
        self.or_low: float | None = None
        self.bars_today = 0

    def update(self, bar: Candle, future: Candle | None = None, vix: float | None = None) -> Features:
        local = bar.ts.astimezone(IST)
        day = local.date()
        if day != self._day:
            if self._day is not None:
                self.prev_day_high, self.prev_day_low = self._day_high, self._day_low
                self.prev_day_close = self._last_close
            self._day, self._day_high, self._day_low = day, bar.high, bar.low
            self.or_high = self.or_low = None
            self.bars_today = 0
        self.bars_today += 1
        self._day_high, self._day_low = max(self._day_high, bar.high), min(self._day_low, bar.low)
        self._last_close = bar.close
        minutes_since_open = (local.hour * 60 + local.minute) - (9 * 60 + 15)
        if minutes_since_open < self.cfg.opening_range_minutes:
            self.or_high = bar.high if self.or_high is None else max(self.or_high, bar.high)
            self.or_low = bar.low if self.or_low is None else min(self.or_low, bar.low)

        self.extreme.update(bar.high, bar.low)  # .high/.low now = previous N bars
        ema_f, ema_s = self.ema_fast.update(bar.close), self.ema_slow.update(bar.close)
        rsi = self.rsi.update(bar.close)
        atr = self.atr.update(bar.high, bar.low, bar.close)
        adx = self.adx.update(bar.high, bar.low, bar.close)
        hv = self.hv.update(bar.close)
        if vix is not None:
            self.vix = vix
        fut_vwap = fut_close = fut_volume = None
        if future is not None:
            fut_vwap = self.vwap.update(day, future.high, future.low, future.close, future.volume)
            fut_close = future.close
            fut_volume = float(future.volume) if future.volume else None

        return Features(bar.ts, day, local.time(), bar.open, bar.high, bar.low, bar.close, {
            "ema_fast": ema_f, "ema_slow": ema_s, "rsi": rsi, "atr": atr,
            "atr_pct": atr / bar.close * 100 if atr else None,
            "adx": adx, "plus_di": self.adx.plus_di, "minus_di": self.adx.minus_di, "hv": hv,
            "breakout_high": self.extreme.high, "breakout_low": self.extreme.low,
            "fut_close": fut_close, "fut_vwap": fut_vwap, "fut_volume": fut_volume, "vix": self.vix,
            "or_high": self.or_high, "or_low": self.or_low,
            "prev_day_high": self.prev_day_high, "prev_day_low": self.prev_day_low,
            "prev_day_close": self.prev_day_close, "bars_today": float(self.bars_today),
        })
