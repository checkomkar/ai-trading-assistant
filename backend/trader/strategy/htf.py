"""Multiple timeframes: build higher-timeframe candles out of the strategy's own bars, with no look-ahead.

A strategy trades on its base timeframe (say 1m). A condition can ask for a value on a higher one ("close above EMA(11) on the
9-minute chart"). The 9-minute candles are built from the base bars, bucketed from the 09:15 open like every other candle, and a
candle is handed to the indicators only once its last base bar has closed. So at any base bar the strategy sees the latest
COMPLETED higher-timeframe candle, never the one still forming, which is what a live system would know at that moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any

SESSION_OPEN = 9 * 60 + 15
SESSION_MINUTES = 375  # 09:15-15:30

TF_MINUTES = {"2m": 2, "3m": 3, "5m": 5, "9m": 9, "10m": 10, "15m": 15, "20m": 20, "30m": 30, "45m": 45, "60m": 60}


@dataclass
class HFeat:
    """What an indicator bank reads from a bar: prices and (if known) the traded volume. Session-based values do not exist for
    built candles, which is why operands that need them are refused on a higher timeframe."""

    open: float
    high: float
    low: float
    close: float
    day: date
    time_ist: time
    volume: float | None = None
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def ts(self) -> Any:
        return (self.day, self.time_ist)

    def __getitem__(self, key: str) -> Any:
        return self.volume if key == "fut_volume" else self.values.get(key)


class HTFAggregator:
    def __init__(self, tf_minutes: int, base_minutes: int) -> None:
        self.t, self.b = tf_minutes, base_minutes
        self._bucket: tuple[date, int] | None = None
        self._bar: list[Any] | None = None  # open, high, low, close, volume, start time

    def _emit(self) -> HFeat | None:
        if self._bar is None or self._bucket is None:
            return None
        o, h, l, c, v, start = self._bar
        self._bar = None
        return HFeat(o, h, l, c, self._bucket[0], start, v)

    def update(self, f) -> list[HFeat]:  # noqa: ANN001
        """Feed one closed base bar (a Features). Returns the higher-timeframe candles this bar completed (usually none or one)."""
        mins = f.time_ist.hour * 60 + f.time_ist.minute - SESSION_OPEN
        bucket = (f.day, mins // self.t)
        out: list[HFeat] = []
        if bucket != self._bucket:
            if (late := self._emit()) is not None:  # a candle whose last base bar never arrived: nothing more can join it
                out.append(late)
            self._bucket = bucket
        vol = f["fut_volume"]
        if self._bar is None:
            self._bar = [f.open, f.high, f.low, f.close, vol, f.time_ist]
        else:
            bar = self._bar
            bar[1], bar[2], bar[3] = max(bar[1], f.high), min(bar[2], f.low), f.close
            bar[4] = (bar[4] or 0.0) + vol if vol is not None else bar[4]
        if mins + self.b >= min((bucket[1] + 1) * self.t, SESSION_MINUTES):  # this bar is the last one of the candle
            if (done := self._emit()) is not None:
                out.append(done)
        return out


def label(key: tuple) -> str:
    """A short name for an operand key, used in the audit snapshot of a signal."""
    name = f"{key[0]}{'' if key[1] is None else key[1]}"
    return name + (f"@{key[3]}" if len(key) > 3 and key[3] else "")
