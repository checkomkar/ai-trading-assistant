"""Portable archive of stored market history (PRD §24.11): gzip CSV files plus a checksummed manifest.

Paid history (Fyers Prime) is worth keeping outside the SQLite file: a plain-text copy survives a broken
database, a schema change or the end of the subscription, and can be read by anything. Layout:

    <root>/manifest.json                         format, per-file rows / first / last / sha256
    <root>/instruments.csv.gz                    every exported instrument (token, expiry, strike, lot size ...)
    <root>/candles/<TYPE>/<GROUP>/<tf>/<period>.csv.gz
        TYPE   INDEX | FUT | OPT | EQ
        GROUP  the underlying (NIFTY, BANKNIFTY) for derivatives, the symbol for indices and stocks
        period YYYY-MM for intraday timeframes, YYYY for daily
        columns token, ts (UTC, ISO 8601), open, high, low, close, volume, oi

* **Export is incremental:** a period file is rewritten only when the database holds a different number of
  rows or a different first/last timestamp for it (`full=True` rewrites everything). Files are written to a
  temporary name and renamed, so an interrupted export never leaves a half-written file under a real name.
* **Nothing is removed:** export only adds or replaces period files; restore only inserts rows that are not
  already in the database (existing candles and instruments are left exactly as they are).
* Simulated data (`SIM:` tokens) is never exported: the archive is broker data only.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select

from trader.db.models import CandleRow, InstrumentRow

FORMAT = 1
INSTRUMENT_COLS = ("token", "symbol", "underlying", "exchange", "instrument_type", "expiry", "strike",
                   "option_type", "lot_size", "tick_size", "is_active")
CANDLE_COLS = ("token", "ts", "open", "high", "low", "close", "volume", "oi")
BATCH = 5000


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _safe(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name) or "_"


def group_of(inst: InstrumentRow) -> str:
    return _safe(inst.underlying if inst.instrument_type in ("OPT", "FUT") else inst.symbol)


def period_of(ts: datetime, timeframe: str) -> str:
    ts = _utc(ts)
    return f"{ts.year:04d}" if timeframe == "1d" else f"{ts.year:04d}-{ts.month:02d}"


def period_bounds(period: str) -> tuple[datetime, datetime]:
    if len(period) == 4:
        y = int(period)
        return datetime(y, 1, 1, tzinfo=timezone.utc), datetime(y + 1, 1, 1, tzinfo=timezone.utc)
    y, m = int(period[:4]), int(period[5:7])
    nxt = (y + 1, 1) if m == 12 else (y, m + 1)
    return datetime(y, m, 1, tzinfo=timezone.utc), datetime(*nxt, 1, tzinfo=timezone.utc)


def rel_path(itype: str, group: str, timeframe: str, period: str) -> str:
    return f"candles/{itype}/{group}/{timeframe}/{period}.csv.gz"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _naive(s) -> bool:  # noqa: ANN001
    return s.bind is not None and s.bind.dialect.name == "sqlite"


def _period_expr(s):  # noqa: ANN001, ANN202
    """Year-month of a candle in SQL (both supported databases store UTC)."""
    if _naive(s):
        return func.strftime("%Y-%m", CandleRow.ts)
    return func.to_char(CandleRow.ts, "YYYY-MM")


def load_manifest(root: Path) -> dict:
    try:
        m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        m = {}
    m.setdefault("format", FORMAT)
    m.setdefault("files", {})
    return m


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _write_gz_csv(path: Path, header: tuple[str, ...], rows) -> int:  # noqa: ANN001
    """Write rows to `path` atomically; mtime 0 in the gzip header so identical data gives identical bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    n = 0
    with tmp.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz, \
            io.TextIOWrapper(gz, encoding="utf-8", newline="") as txt:
        w = csv.writer(txt)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
            n += 1
    os.replace(tmp, path)
    return n


@dataclass
class ExportResult:
    written: list[str] = field(default_factory=list)
    unchanged: int = 0
    rows: int = 0


async def export(sm, root: Path, *, full: bool = False, timeframes: tuple[str, ...] | None = None,  # noqa: ANN001
                 say: Callable[[str], None] = print) -> ExportResult:
    root.mkdir(parents=True, exist_ok=True)
    man = load_manifest(root)
    res = ExportResult()
    async with sm() as s:
        insts = {i.id: i for i in (await s.execute(
            select(InstrumentRow).where(InstrumentRow.token.not_like("SIM:%")))).scalars()}
        if not insts:
            say("Nothing to export: no broker instruments stored.")
            return res
        # Per instrument, timeframe and month: rows, first and last candle. Daily files group months into years.
        per = _period_expr(s)
        q = (select(CandleRow.instrument_id, CandleRow.timeframe, per, func.count(), func.min(CandleRow.ts),
                    func.max(CandleRow.ts))
             .where(CandleRow.instrument_id.in_(list(insts)))
             .group_by(CandleRow.instrument_id, CandleRow.timeframe, per))
        stats: dict[str, dict] = defaultdict(lambda: {"rows": 0, "first": None, "last": None, "ids": set()})
        for iid, tf, month, n, lo, hi in (await s.execute(q)).all():
            if timeframes and tf not in timeframes:
                continue
            inst = insts[iid]
            period = str(month)[:4] if tf == "1d" else str(month)[:7]
            key = rel_path(inst.instrument_type, group_of(inst), tf, period)
            st = stats[key]
            st["rows"] += n
            lo, hi = _utc(lo).isoformat(), _utc(hi).isoformat()
            st["first"] = lo if st["first"] is None else min(st["first"], lo)
            st["last"] = hi if st["last"] is None else max(st["last"], hi)
            st["ids"].add(iid)
            st.update(instrument_type=inst.instrument_type, group=group_of(inst), timeframe=tf, period=period)

        _write_gz_csv(root / "instruments.csv.gz", INSTRUMENT_COLS, (
            [getattr(i, c).isoformat() if isinstance(getattr(i, c), date) else getattr(i, c) for c in INSTRUMENT_COLS]
            for i in sorted(insts.values(), key=lambda x: x.token)))

        for key in sorted(stats):
            st = stats[key]
            old = man["files"].get(key)
            path = root / key
            if (not full and old and path.exists() and old["rows"] == st["rows"]
                    and old["first"] == st["first"] and old["last"] == st["last"]):
                res.unchanged += 1
                continue
            lo, hi = period_bounds(st["period"])
            if _naive(s):
                lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
            tokens = {iid: insts[iid].token for iid in st["ids"]}
            rq = (select(CandleRow).where(CandleRow.instrument_id.in_(list(tokens)), CandleRow.timeframe == st["timeframe"],
                                          CandleRow.ts >= lo, CandleRow.ts < hi)
                  .order_by(CandleRow.instrument_id, CandleRow.ts))
            rows = (await s.execute(rq)).scalars().all()
            n = _write_gz_csv(path, CANDLE_COLS, (
                [tokens[r.instrument_id], _utc(r.ts).isoformat(), r.open, r.high, r.low, r.close, r.volume,
                 "" if r.oi is None else r.oi] for r in rows))
            man["files"][key] = {"rows": n, "first": st["first"], "last": st["last"], "sha256": sha256(path),
                                 "instrument_type": st["instrument_type"], "group": st["group"],
                                 "timeframe": st["timeframe"], "period": st["period"]}
            res.written.append(key)
            res.rows += n
            if len(res.written) % 50 == 0:
                _write_json(root / "manifest.json", man)  # progress survives an interruption
                say(f"  {len(res.written)} files written ...")
    man["format"] = FORMAT
    man["updated_at"] = datetime.now(timezone.utc).isoformat()
    _write_json(root / "manifest.json", man)
    return res


def verify(root: Path) -> list[str]:
    """Problems found (missing file, checksum mismatch). Empty list = the archive matches its manifest."""
    man = load_manifest(root)
    bad = []
    if not (root / "instruments.csv.gz").exists():
        bad.append("instruments.csv.gz missing")
    for key, meta in sorted(man["files"].items()):
        path = root / key
        if not path.exists():
            bad.append(f"{key}: missing")
        elif sha256(path) != meta["sha256"]:
            bad.append(f"{key}: checksum mismatch")
    return bad


def _read_gz_csv(path: Path):  # noqa: ANN202
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def _num(v: str, cast):  # noqa: ANN001, ANN202
    return None if v in ("", None) else cast(v)


@dataclass
class RestoreResult:
    instruments_added: int = 0
    candles_added: int = 0
    files: int = 0


async def restore(sm, root: Path, *, say: Callable[[str], None] = print) -> RestoreResult:  # noqa: ANN001
    """Insert archived instruments and candles that the database does not have. Existing rows are untouched."""
    from trader.db.repository import _insert

    problems = verify(root)
    if problems:
        raise ValueError("archive does not match its manifest: " + "; ".join(problems[:5]))
    man = load_manifest(root)
    res = RestoreResult()
    async with sm() as s, s.begin():
        before = (await s.execute(select(func.count()).select_from(InstrumentRow))).scalar_one()
        rows = []
        for r in _read_gz_csv(root / "instruments.csv.gz"):
            rows.append(dict(token=r["token"], symbol=r["symbol"], underlying=r["underlying"], exchange=r["exchange"],
                             instrument_type=r["instrument_type"],
                             expiry=date.fromisoformat(r["expiry"]) if r["expiry"] else None,
                             strike=_num(r["strike"], float), option_type=r["option_type"] or None,
                             lot_size=int(r["lot_size"]), tick_size=float(r["tick_size"]),
                             is_active=r["is_active"] in ("True", "true", "1")))
        for i in range(0, len(rows), 500):
            await s.execute(_insert(s, InstrumentRow).values(rows[i:i + 500]).on_conflict_do_nothing(
                index_elements=["token"]))
        after = (await s.execute(select(func.count()).select_from(InstrumentRow))).scalar_one()
        res.instruments_added = after - before
        ids = dict((await s.execute(select(InstrumentRow.token, InstrumentRow.id))).all())

    for key, meta in sorted(man["files"].items()):
        batch: list[dict] = []
        added = 0
        async with sm() as s, s.begin():
            for r in _read_gz_csv(root / key):
                batch.append(dict(instrument_id=ids[r["token"]], timeframe=meta["timeframe"],
                                  ts=datetime.fromisoformat(r["ts"]), open=float(r["open"]), high=float(r["high"]),
                                  low=float(r["low"]), close=float(r["close"]), volume=int(r["volume"] or 0),
                                  oi=_num(r["oi"], int)))
                if len(batch) >= BATCH:
                    added += await _insert_ignore(s, _insert, batch)
                    batch = []
            if batch:
                added += await _insert_ignore(s, _insert, batch)
        res.candles_added += added
        res.files += 1
        if added:
            say(f"  {key}: +{added}")
    return res


async def _insert_ignore(s, _insert, batch: list[dict]) -> int:  # noqa: ANN001
    """Insert rows not already present; returns how many were new (the driver's inserted-row count)."""
    n = 0
    for i in range(0, len(batch), 500):
        r = await s.execute(_insert(s, CandleRow).values(batch[i:i + 500]).on_conflict_do_nothing(
            index_elements=["instrument_id", "timeframe", "ts"]))
        n += max(r.rowcount or 0, 0)
    return n
