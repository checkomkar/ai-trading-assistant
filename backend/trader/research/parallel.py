"""Run many backtests over one loaded dataset (PRD §24.5 B4.2-B4.3).

The data is loaded once by the caller and handed to each worker process once (the pool initializer), so a sweep of
200 settings reads the database once, not 200 times. Each run is independent and deterministic, so the results are
the same whether the runs go serially or in parallel (trade ids aside, which are random labels).
"""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Callable

from trader.backtest import data as bt_data
from trader.backtest.runner import BacktestRequest, execute

_DATA: bt_data.BacktestData | None = None  # one dataset per worker process, set by the pool initializer


def _init(data: bt_data.BacktestData) -> None:
    global _DATA
    _DATA = data


def slim(out: dict, keep_trades: bool) -> dict:
    """A run's result without the bulky parts a sweep does not need (curves; trades unless asked for)."""
    m = {k: v for k, v in out["metrics"].items() if k not in ("equity_curve", "daily_pnl")}
    res = {"metrics": m, "signals": out["signals"], "skipped_entries": out["skipped_entries"],
           "warnings": out["warnings"], "timings": out.get("timings", {})}
    if keep_trades:
        res["trades"] = [{k: t[k] for k in ("entry_time", "exit_time", "net_pnl", "direction", "exit_reason", "features",
                                              "entry_price", "contract", "regime") if k in t} for t in out["trades"]]
        res["daily_pnl"] = out["metrics"].get("daily_pnl")
    return res


def _run(args: tuple[BacktestRequest, bool]) -> dict:
    req, keep = args
    assert _DATA is not None
    return slim(execute(req, _DATA), keep)


def default_workers(cap: int = 8) -> int:
    return max(1, min(cap, (os.cpu_count() or 2) - 1))


def run_many(reqs: list[BacktestRequest], data: bt_data.BacktestData, *, workers: int | None = None, keep_trades: bool = False,
             progress: Callable[[int, int], None] | None = None, cancelled: Callable[[], bool] | None = None) -> list[dict | None]:
    """Results in the order of `reqs`; an entry is None if the job was cancelled before it ran, or a dict with "error"
    if that run raised (a bad parameter combination must not sink the whole sweep)."""
    n = len(reqs)
    out: list[dict | None] = [None] * n
    workers = workers or default_workers()
    done = 0
    if workers <= 1 or n <= 1:
        _init(data)
        for i, r in enumerate(reqs):
            if cancelled and cancelled():
                break
            try:
                out[i] = _run((r, keep_trades))
            except Exception as exc:  # noqa: BLE001
                out[i] = {"error": f"{type(exc).__name__}: {exc}"}
            done += 1
            if progress:
                progress(done, n)
        return out
    # "spawn" everywhere: it is what Windows uses, and forking a multi-threaded server process can deadlock.
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=min(workers, n), mp_context=ctx, initializer=_init, initargs=(data,)) as pool:
        pending = {pool.submit(_run, (r, keep_trades)): i for i, r in enumerate(reqs)}
        while pending:
            finished, _ = wait(pending, timeout=0.5, return_when=FIRST_COMPLETED)
            for f in finished:
                i = pending.pop(f)
                try:
                    out[i] = f.result()
                except Exception as exc:  # noqa: BLE001
                    out[i] = {"error": f"{type(exc).__name__}: {exc}"}
                done += 1
                if progress:
                    progress(done, n)
            if cancelled and cancelled():
                for f in pending:
                    f.cancel()
                break
    return out
