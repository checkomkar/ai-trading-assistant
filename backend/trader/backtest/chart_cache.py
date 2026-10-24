"""Memory cache for backtest charts, so switching timeframe back, adding an indicator or reopening a chart is instant.

A stored run never changes, so what its chart reads (the candles of its range) and what it computes (one indicator
series over a window) can be kept and reused. Two small LRUs: the loaded candles per (run, timeframe), which are large
and few, and the computed points per (run, timeframe, window, series), which are small and many. One cache lives on
the app (`app.state`), not in a module global; it holds no account data beyond run ids the routes already checked.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Hashable


class _LRU:
    def __init__(self, size: int) -> None:
        self.size = size
        self._d: OrderedDict[Hashable, Any] = OrderedDict()

    def get(self, key: Hashable) -> Any:
        if key not in self._d:
            return None
        self._d.move_to_end(key)
        return self._d[key]

    def put(self, key: Hashable, value: Any) -> None:
        self._d[key] = value
        self._d.move_to_end(key)
        while len(self._d) > self.size:
            self._d.popitem(last=False)

    def __len__(self) -> int:
        return len(self._d)


class ChartCache:
    def __init__(self, data_size: int = 4, series_size: int = 600) -> None:
        self.data = _LRU(data_size)  # (run id, timeframe, feed from) -> BacktestData
        self.series = _LRU(series_size)  # (run id, timeframe, window start, series id or "bars") -> points


def for_app(state: Any) -> ChartCache:  # noqa: ANN401 - starlette State
    cache = getattr(state, "chart_cache", None)
    if cache is None:
        cache = state.chart_cache = ChartCache()
    return cache
