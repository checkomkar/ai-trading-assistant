"""Run a backtest from the command line (PRD M3).

    python -m trader.scripts.backtest --underlying NIFTY --start 2025-11-01 --end 2026-10-17
    python -m trader.scripts.backtest --source simulated --sl 25 --target 50 --no-vwap

Prints the report and writes data/backtests/<run id>.json and <run id>_trades.csv.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
from datetime import date

from trader.backtest.runner import BacktestRequest, run_and_store
from trader.market_data.resample import SUPPORTED
from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker


def fmt(v: object) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:,.2f}"
    return str(v)


def print_report(r: dict) -> None:
    m = r["metrics"]
    print(f"\nBacktest {r['id']}  {r['underlying']} {r['start']} .. {r['end']}  ({r['timeframe']}, {r['strategy']})")
    print(f"Data: {r['data_source'].upper()}   Option pricing: {r['pricing_mode'].upper()}   "
          f"Bars: {r['bars']}   Days: {r['days']}   Signals: {r['signals']}")
    for w in r["warnings"]:
        print(f"  ! {w}")
    rows = [
        ("Trades", m["trades"]), ("Net P&L (Rs)", m["total_net_pnl"]), ("Gross P&L (Rs)", m["total_gross_pnl"]),
        ("Charges (Rs)", m["total_charges"]), ("Return on capital %", m["return_pct"]),
        ("Win rate %", m["win_rate"]), ("Profit factor", m["profit_factor"]),
        ("Average win (Rs)", m["average_win"]), ("Average loss (Rs)", m["average_loss"]),
        ("Expectancy / trade (Rs)", m["expectancy"]), ("Max drawdown (Rs)", m["max_drawdown"]),
        ("Max drawdown %", m["max_drawdown_pct"]), ("Sharpe (daily, ann.)", m["sharpe"]),
        ("Sortino (daily, ann.)", m["sortino"]), ("Avg holding (min)", m["average_holding_minutes"]),
        ("Avg MAE % / MFE %", f"{fmt(m['average_mae_pct'])} / {fmt(m['average_mfe_pct'])}"),
    ]
    print()
    for k, v in rows:
        print(f"  {k:<26}{fmt(v):>14}")
    print(f"\n  Exit reasons:  {m['exit_reasons']}")
    print(f"  By direction:  {m['by_direction']}")
    print(f"  No-trade:      {dict(sorted(r['no_trade_reasons'].items(), key=lambda x: -x[1]))}")
    if r["skipped_entries"]:
        print(f"  Skipped:       {r['skipped_entries']}")


async def run(a: argparse.Namespace) -> None:
    settings = get_settings()
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    strategy = {"stop_loss_pct": a.sl, "target_pct": a.target, "use_vwap": not a.no_vwap,
                "max_trades_per_day": a.max_trades}
    req = BacktestRequest(a.underlying.upper(), a.start, a.end, a.timeframe, a.source, a.pricing,
                          strategy_params=strategy,
                          engine_params={"lots": a.lots, "max_daily_loss": a.max_daily_loss, "capital": a.capital})
    try:
        r = await run_and_store(create_sessionmaker(engine), req)
    except LookupError as exc:
        raise SystemExit(str(exc)) from None
    finally:
        await engine.dispose()
    print_report(r)
    out = settings.data_dir / "backtests"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{r['id']}.json").write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    if r["trades"]:
        with (out / f"{r['id']}_trades.csv").open("w", newline="", encoding="utf-8") as f:
            cols = [k for k in r["trades"][0] if k != "features"]
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(r["trades"])
    print(f"\n  Saved: {out / (r['id'] + '.json')}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "nifty", "banknifty"])
    p.add_argument("--start", type=date.fromisoformat, default=date(2025, 10, 1))
    p.add_argument("--end", type=date.fromisoformat, default=date.today())
    p.add_argument("--timeframe", default="5m", choices=list(SUPPORTED),
                   help="built from finer stored candles when not stored itself")
    p.add_argument("--source", default="auto", choices=["auto", "real", "simulated"])
    p.add_argument("--pricing", default="synthetic", choices=["synthetic", "real", "hybrid"])
    p.add_argument("--sl", type=float, default=25.0, help="stop loss, %% of option premium")
    p.add_argument("--target", type=float, default=50.0, help="target, %% of option premium")
    p.add_argument("--max-trades", type=int, default=3)
    p.add_argument("--no-vwap", action="store_true")
    p.add_argument("--lots", type=int, default=1)
    p.add_argument("--max-daily-loss", type=float, default=5000.0)
    p.add_argument("--capital", type=float, default=100_000.0)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
