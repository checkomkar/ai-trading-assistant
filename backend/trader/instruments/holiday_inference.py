"""Infer past NSE holidays from stored index candles (PRD §24.2 B1.4, §24.11).

`config/nse_holidays.yaml` only covers what we have checked so far. Years of downloaded history need the
holidays of those years too: expiry resolution rolls an expiry back over a holiday, and the expired-option
planner builds symbols from those dates. A weekday on which *every* stored index has no 1m candle at all,
inside a stretch where the index otherwise has data, is taken as a holiday and written with an
"inferred" note, as the existing entries are. A day with partial data is never treated as a holiday.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select

from trader.db.models import CandleRow, InstrumentRow

NOTE = "Holiday (inferred: no index 1m data on this weekday)"


async def stored_days(sm, underlyings: list[str], timeframe: str = "1m") -> set[date]:  # noqa: ANN001
    """Trading days with at least one candle for any of `underlyings` (real data only).

    The session (03:45-10:00 UTC) never crosses midnight in UTC or IST, so the UTC date of a stored candle is
    its IST trading day and the database can group by it directly."""
    day = func.date(CandleRow.ts)
    q = (select(day).join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
         .where(InstrumentRow.symbol.in_(underlyings), InstrumentRow.instrument_type == "INDEX",
                InstrumentRow.token.not_like("SIM:%"), CandleRow.timeframe == timeframe).distinct())
    async with sm() as s:
        rows = (await s.execute(q)).scalars().all()
    return {r if isinstance(r, date) else date.fromisoformat(str(r)[:10]) for r in rows}


def missing_weekdays(days: set[date], known: set[date]) -> list[date]:
    """Weekdays between the first and last stored day with no data that are not already listed."""
    if not days:
        return []
    out, d, last = [], min(days), max(days)
    while d <= last:
        if d.weekday() < 5 and d not in days and d not in known:
            out.append(d)
        d += timedelta(days=1)
    return out


def append_holidays(path: Path, new: list[date], note: str = NOTE) -> int:
    """Add `new` under `holidays:` in the YAML file, keeping every existing line as it is. Returns how many."""
    if not new:
        return 0
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        head = lines.index("holidays:")
    except ValueError:
        raise ValueError(f"{path} has no top-level 'holidays:' key") from None
    end = head + 1
    while end < len(lines) and (lines[end].startswith(" ") or not lines[end].strip()):
        end += 1
    while end > head + 1 and not lines[end - 1].strip():
        end -= 1  # keep the blank line before the next section
    block = ["  # Added by holiday inference from downloaded history; confirm against the NSE circulars."]
    block += [f'  {d.isoformat()}: "{note}"' for d in sorted(new)]
    path.write_text("\n".join(lines[:end] + block + lines[end:]) + "\n", encoding="utf-8")
    return len(new)
