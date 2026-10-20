"""Rehearse stored trading days through the paper-trading pipeline and compare with a backtest.

    python -m trader.scripts.paper_rehearsal --underlying NIFTY --start 2026-10-12 --end 2026-10-19

Each day is replayed tick by tick (REPLAY: tokens, synthetic option quotes) through the same collector,
runner, risk engine and PaperBroker as live paper trading, in-process and deterministically.
Trades are written to the paper journal tagged data_source=replay. The same days are then run
through the backtester (synthetic pricing) so the two can be compared (PRD M4 DoD).
Each day starts from fresh capital and a fresh risk state.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy import delete

from trader.backtest import data as bt_data
from trader.backtest.runner import BacktestRequest, execute
from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.models import PaperTradeRow
from trader.db.session import create_engine, create_sessionmaker
from trader.execution.rehearsal import build, rehearse_day
from trader.execution.runner import LaneConfig
from trader.execution.summary import summarize
from trader.instruments.calendar import get_calendar
from trader.market_data.replay import ReplayProvider


async def main(args: argparse.Namespace) -> None:
    settings = get_settings()
    await asyncio.to_thread(upgrade_db, settings.resolved_database_url)
    engine = create_engine(settings.resolved_database_url)
    sm = create_sessionmaker(engine)
    cal = get_calendar()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    days = [d for d in days if cal.is_trading_day(d)]
    lane = LaneConfig(args.underlying, args.timeframe, "momentum")

    # A rehearsal is repeatable, so its journal rows replace the previous rehearsal's (events stay: append-only).
    async with sm() as session, session.begin():
        await session.execute(delete(PaperTradeRow).where(
            PaperTradeRow.data_source == "replay", PaperTradeRow.underlying == args.underlying,
            PaperTradeRow.trading_day.in_([d.isoformat() for d in days])))

    paper_trades: list[dict] = []
    print(f"{'day':<12}{'trades':>7}{'net P&L':>11}   exits")
    for d in days:
        prov = ReplayProvider(sm, d, speed=0, source=args.source, underlyings=(args.underlying,))
        md, paper = build(settings, prov, sm)
        try:
            await rehearse_day(md, paper, prov, [lane])
        except LookupError as exc:
            print(f"{d.isoformat():<12} skipped: {exc}")
            continue
        t = paper.store.trades
        paper_trades += t
        reasons = ", ".join(f"{k}:{v}" for k, v in sorted(summarize(t)["by_exit_reason"].items()))
        print(f"{d.isoformat():<12}{len(t):>7}{sum(x['net_pnl'] for x in t):>11,.0f}   {reasons}")
    s = summarize(paper_trades)
    print(f"\nPAPER (replay, synthetic option quotes): {s['trades']} trades, net {s['net_pnl']:,.0f}, "
          f"gross {s['gross_pnl']:,.0f}, charges {s['charges']:,.0f}, max DD {s['max_drawdown']:,.0f}, "
          f"win rate {s['win_rate']}")

    async with sm() as session:
        data = await bt_data.load(session, args.underlying, args.timeframe, start, end, args.source)
    req = BacktestRequest(args.underlying, start, end, args.timeframe, args.source, "synthetic", "momentum")
    out = await asyncio.to_thread(execute, req, data)
    m = out["metrics"]
    print(f"BACKTEST (synthetic, same days): {m['trades']} trades, net {m['total_net_pnl']:,.0f}, "
          f"gross {m['total_gross_pnl']:,.0f}, charges {m['total_charges']:,.0f}")
    print("Differences are expected: the backtest fills at bar open and checks stops on bar OHLC; paper fills "
          "at the live bid/ask 5 s steps later and checks stops on every tick. Risk limits also apply only to paper.")
    await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--underlying", default="NIFTY", choices=["NIFTY", "BANKNIFTY"])
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--timeframe", default="5m", choices=["1m", "5m"])
    ap.add_argument("--source", default="real", choices=["real", "simulated"])
    asyncio.run(main(ap.parse_args()))
