"""Save the ready-made strategy library (and Momentum #1) so they show up in the Strategies tab.

    python -m trader.scripts.seed_strategies

Idempotent: a strategy whose spec is unchanged is not saved again; a changed one becomes a new version.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from trader.config import get_settings
from trader.db.migrate import upgrade_db
from trader.db.models import StrategyRow
from trader.db.session import create_engine, create_sessionmaker
from trader.strategy.library import LIBRARY
from trader.strategy.spec import MOMENTUM_V1, StrategySpec, render_english, semantic_errors


async def main() -> None:
    settings = get_settings()
    await asyncio.to_thread(upgrade_db, settings.resolved_database_url)
    engine = create_engine(settings.resolved_database_url)
    sm = create_sessionmaker(engine)
    for raw in [MOMENTUM_V1, *LIBRARY]:
        s = StrategySpec.model_validate(raw)
        errs = semantic_errors(s)
        if errs:
            raise SystemExit(f"{s.name}: {'; '.join(errs)}")
        async with sm() as ses, ses.begin():
            latest = (await ses.execute(select(StrategyRow).where(StrategyRow.name == s.name)
                                        .order_by(StrategyRow.version.desc()).limit(1))).scalars().first()
            if latest is not None and latest.spec_hash == s.hash:
                print(f"unchanged  {s.name}")
                continue
            ses.add(StrategyRow(id=uuid.uuid4().hex[:12], name=s.name, version=(latest.version + 1) if latest else 1,
                                spec=s.model_dump(mode="json", by_alias=True, exclude_none=True), spec_hash=s.hash,
                                english=render_english(s), source_text=s.description or "library", assumptions=[],
                                translator_model=None, created_at=datetime.now(timezone.utc)))
            print(f"saved      {s.name}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
