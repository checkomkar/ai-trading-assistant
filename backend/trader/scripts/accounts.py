"""Operator tasks for the hosted product (run from backend/).

  python -m trader.scripts.accounts grant you@example.com pro --days 30
  python -m trader.scripts.accounts claim-local you@example.com   # move the single-user data ("local") onto an account
  python -m trader.scripts.accounts list
  python -m trader.scripts.accounts gen-key   # a value for CREDENTIAL_KEY
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select, update

from trader.accounts import repo as accounts
from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.models import (AccountRow, BacktestRunRow, ChatConversationRow, KnowledgeChunkRow, KnowledgeDocRow,
                              MemoryNoteRow, StrategyFolderItemRow, StrategyFolderRow, StrategyRow)
from trader.db.session import create_engine, create_sessionmaker

OWNED = (StrategyRow, BacktestRunRow, ChatConversationRow, KnowledgeDocRow, KnowledgeChunkRow, MemoryNoteRow,
         StrategyFolderRow, StrategyFolderItemRow)


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("grant")
    g.add_argument("email")
    g.add_argument("plan")
    g.add_argument("--days", type=int, default=30)
    c = sub.add_parser("claim-local")
    c.add_argument("email")
    sub.add_parser("list")
    sub.add_parser("gen-key")
    args = ap.parse_args()
    if args.cmd == "gen-key":
        from trader.accounts.vault import generate_key

        print(generate_key())
        return

    url = get_settings().resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        if args.cmd == "list":
            for r in (await s.execute(select(AccountRow).order_by(AccountRow.created_at))).scalars():
                print(f"{r.email:36} {accounts.principal_from_row(r).plan:8} {r.created_at:%Y-%m-%d}")
        else:
            row = await accounts.get_by_email(s, args.email)
            if row is None:
                raise SystemExit(f"No account for {args.email}. Sign up in the app first.")
            if args.cmd == "grant":
                await accounts.set_plan(s, row, args.plan, args.days)
                print(f"{row.email} is now on {args.plan} for {args.days} days")
            else:
                for model in OWNED:
                    n = (await s.execute(update(model).where(model.owner == accounts.LOCAL_ID).values(owner=row.id))).rowcount
                    print(f"{model.__tablename__}: {n} rows")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
