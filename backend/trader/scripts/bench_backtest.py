"""Measure backtest speed and database health on your own data (PRD §24.5 B4.1, B4.5).

    python -m trader.scripts.bench_backtest                         # NIFTY, last 365 days, 5m
    python -m trader.scripts.bench_backtest --start 2025-10-01 --end 2026-10-01 --sweep 24

Prints: database size and rows per instrument type / timeframe; whether the candle index serves the backtest query;
one run's load / engine / metrics times in synthetic and hybrid pricing; and, with --sweep N, the same N settings run
serially and in parallel over one loaded dataset (the results must match). Send the output back so the speed targets
for the research jobs can be set from real numbers.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

from trader.backtest.runner import BacktestRequest, execute, load_for
from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.research.parallel import default_workers, run_many


def db_report(url: str) -> None:
    if not url.startswith("sqlite"):
        print("  (not SQLite: skipped)")
        return
    path = Path(url.split("///", 1)[1])
    print(f"  file {path} : {path.stat().st_size / 1e9:.2f} GB")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        for itype, tf, n in con.execute(
                "select i.instrument_type, c.timeframe, count(*) from candles c join instruments i on i.id = c.instrument_id "
                "group by 1, 2 order by 3 desc"):
            print(f"  {itype:<6} {tf:>4} {n:>12,} rows")
        idx = [r[1] for r in con.execute("pragma index_list(candles)")]
        print(f"  candle indexes: {idx}")
        plan = con.execute("explain query plan select * from candles where instrument_id in (1, 2) and timeframe = '1m' "
                           "and ts >= '2025-01-01' and ts <= '2025-12-31' order by ts").fetchall()
        print("  backtest query plan: " + " | ".join(r[-1] for r in plan))
    finally:
        con.close()


async def run(a: argparse.Namespace) -> None:
    settings = get_settings()
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    print("== database ==")
    db_report(url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    end = a.end or date.today()
    start = a.start or end - timedelta(days=365)
    try:
        for pricing in ("synthetic", "hybrid"):
            req = BacktestRequest(underlying=a.underlying, start=start, end=end, timeframe=a.timeframe, pricing=pricing)
            t0 = time.perf_counter()
            data = await load_for(sm, req)
            t_load = time.perf_counter() - t0
            out = execute(req, data)
            t = out["timings"]
            print(f"\n== one run, {pricing} ({start} .. {end}, {a.timeframe}) ==")
            print(f"  load {t_load:.2f}s ({len(data.bars):,} bars, {len(data.options):,} option contracts) | "
                  f"engine {t['engine_s']:.2f}s | metrics {t['metrics_s']:.2f}s | {out['metrics']['trades']} trades")
            if a.sweep and pricing == "synthetic":
                reqs = [BacktestRequest(underlying=a.underlying, start=start, end=end, timeframe=a.timeframe, pricing=pricing,
                                        strategy_params={"rsi_long": 52 + i % 8, "rsi_short": 48 - i % 8,
                                                         "stop_loss_pct": 20 + 5 * (i // 8)})
                        for i in range(a.sweep)]
                t0 = time.perf_counter()
                serial = run_many(reqs, data, workers=1)
                t_serial = time.perf_counter() - t0
                w = a.workers or default_workers()
                t0 = time.perf_counter()
                par = run_many(reqs, data, workers=w)
                t_par = time.perf_counter() - t0
                same = all(x and y and x["metrics"]["total_net_pnl"] == y["metrics"]["total_net_pnl"] for x, y in zip(serial, par))
                print(f"\n== sweep of {a.sweep} settings ==")
                print(f"  serial {t_serial:.1f}s | parallel ({w} workers of {os.cpu_count()} CPUs) {t_par:.1f}s | "
                      f"speed-up {t_serial / max(t_par, 1e-9):.1f}x | identical results: {same}")
    finally:
        await engine.dispose()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY"])
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--timeframe", default="5m")
    p.add_argument("--sweep", type=int, default=16, help="settings in the serial-vs-parallel check (0 = skip)")
    p.add_argument("--workers", type=int)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
