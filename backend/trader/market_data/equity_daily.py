"""Daily candles for the cash-equity universe (NIFTY 50), fetched from the broker and upserted.

Like every other stored history this only ever inserts or updates; nothing is deleted. Re-running fetches from the last
stored day, so it doubles as the daily refresh after the close.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.db import repository as repo
from trader.db.models import CandleRow, InstrumentRow
from trader.instruments import equities as eq
from trader.instruments.calendar import IST
from trader.logging import get_logger
from trader.market_data.candles import Candle

log = get_logger("equity_daily")


def _ist_midnight(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=IST).astimezone(timezone.utc)


async def refresh(provider, sessionmaker: async_sessionmaker, symbols: list[str], years: int = 5,
                  today: date | None = None) -> dict:
    """Fetch daily candles for `symbols`. Returns {"stored": {symbol: n}, "failed": {symbol: reason}}.

    Only completed days are stored: today's candle is skipped until the market has closed (15:30 IST)."""
    now = datetime.now(IST)
    today = today or now.date()
    last_complete = today if now.time() >= time(15, 30) else today - timedelta(days=1)
    async with sessionmaker() as s, s.begin():
        ids = await repo.upsert_instruments(s, [eq.instrument(x) for x in symbols])
    stored: dict[str, int] = {}
    failed: dict[str, str] = {}
    for sym in symbols:
        inst = eq.instrument(sym)
        async with sessionmaker() as s:
            last = (await s.execute(select(CandleRow.ts).where(CandleRow.instrument_id == ids[inst.token],
                                                               CandleRow.timeframe == eq.DAILY)
                                    .order_by(CandleRow.ts.desc()).limit(1))).scalar_one_or_none()
        start_day = eq.day_of(last) + timedelta(days=1) if last else today - timedelta(days=365 * years)
        if start_day > last_complete:
            stored[sym] = 0
            continue
        try:
            raw = await provider.history(inst, eq.DAILY, _ist_midnight(start_day), _ist_midnight(last_complete + timedelta(days=1)))
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the rest
            failed[sym] = str(exc)[:160]
            log.warning("equity history failed", event="equity.history_failed", symbol=sym, error=str(exc)[:160])
            continue
        candles = [Candle(c.token, eq.DAILY, eq.normalise_daily(c.ts), c.open, c.high, c.low, c.close, c.volume, closed=True)
                   for c in raw if eq.day_of(c.ts) <= last_complete]
        if not candles:
            stored[sym] = 0
            continue
        async with sessionmaker() as s, s.begin():
            stored[sym] = await repo.upsert_candles(s, candles, ids)
    return {"stored": stored, "failed": failed}


async def load_daily(s, symbols: list[str], start: date, end: date) -> dict[str, list[Candle]]:
    """Stored daily candles per symbol between two dates (inclusive), oldest first."""
    lo, hi = _ist_midnight(start), _ist_midnight(end + timedelta(days=1))
    if s.bind is not None and s.bind.dialect.name == "sqlite":
        lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
    toks = {eq.token(x): x for x in symbols}
    rows = (await s.execute(
        select(InstrumentRow.token, CandleRow)
        .join(CandleRow, CandleRow.instrument_id == InstrumentRow.id)
        .where(InstrumentRow.token.in_(list(toks)), CandleRow.timeframe == eq.DAILY, CandleRow.ts >= lo, CandleRow.ts < hi)
        .order_by(CandleRow.ts))).all()
    out: dict[str, list[Candle]] = {}
    for tok, r in rows:
        ts = r.ts if r.ts.tzinfo else r.ts.replace(tzinfo=timezone.utc)
        out.setdefault(toks[tok], []).append(Candle(tok, eq.DAILY, ts, r.open, r.high, r.low, r.close, r.volume or 0, closed=True))
    return out


async def coverage(s) -> dict:
    """What daily history is stored: per symbol first/last day and candle count, summarised."""
    from sqlalchemy import func

    rows = (await s.execute(
        select(InstrumentRow.symbol, func.min(CandleRow.ts), func.max(CandleRow.ts), func.count())
        .join(CandleRow, CandleRow.instrument_id == InstrumentRow.id)
        .where(InstrumentRow.instrument_type == "EQ", CandleRow.timeframe == eq.DAILY)
        .group_by(InstrumentRow.symbol))).all()
    per = {sym: {"from": eq.day_of(lo).isoformat(), "to": eq.day_of(hi).isoformat(), "candles": n} for sym, lo, hi, n in rows}
    return {"symbols": len(per), "candles": sum(v["candles"] for v in per.values()),
            "from": min((v["from"] for v in per.values()), default=None),
            "to": max((v["to"] for v in per.values()), default=None), "per_symbol": per}
