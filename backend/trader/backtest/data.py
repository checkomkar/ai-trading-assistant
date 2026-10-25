"""Load backtest inputs from the database.

Real broker data (e.g. `NSE:` tokens from Fyers) and simulator data (`SIM:` tokens) are never
mixed: a run uses one source, and the report says which.
"""

from __future__ import annotations

from array import array
from bisect import bisect_left
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from trader.db.models import CandleRow, InstrumentRow
from trader.instruments.calendar import IST, TradingCalendar, get_calendar
from trader.instruments.specs import VIX_SYMBOL
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle
from trader.market_data.resample import SUPPORTED, minutes, resample

SIM_PREFIX = "SIM:"


class CandleSeries(Mapping):
    """One contract's candles, sorted by time, held as plain number arrays (about 56 bytes a candle instead of ~300 for
    Candle objects plus their datetimes). Reads like the dict it replaces, {bar ts: Candle}: `series.get(ts)` builds the
    Candle on demand. A year of near-the-money 1m option candles fits in tens of MB instead of gigabytes."""

    __slots__ = ("token", "timeframe", "_t", "_o", "_h", "_l", "_c", "_v", "_oi")

    def __init__(self, token: str, timeframe: str) -> None:
        self.token, self.timeframe = token, timeframe
        self._t = array("q")  # unix seconds, ascending
        self._o, self._h, self._l, self._c = array("d"), array("d"), array("d"), array("d")
        self._v = array("q")
        self._oi = array("q")  # -1 = not reported

    @classmethod
    def of(cls, candles: list[Candle], token: str, timeframe: str) -> "CandleSeries":
        out = cls(token, timeframe)
        for c in sorted(candles, key=lambda c: c.ts):
            out.append(int(c.ts.timestamp()), c.open, c.high, c.low, c.close, c.volume or 0, c.oi)
        return out

    def append(self, t: int, o: float, h: float, l: float, c: float, v: int, oi: int | None) -> None:  # noqa: E741
        if self._t and t <= self._t[-1]:
            if t == self._t[-1]:
                return  # a duplicate bucket: keep the first, as the dict this replaces did with sorted input
            raise ValueError("CandleSeries.append needs ascending times")
        self._t.append(t)
        self._o.append(o)
        self._h.append(h)
        self._l.append(l)
        self._c.append(c)
        self._v.append(int(v or 0))
        self._oi.append(-1 if oi is None else int(oi))

    def _at(self, i: int) -> Candle:
        oi = self._oi[i]
        return Candle(self.token, self.timeframe, datetime.fromtimestamp(self._t[i], timezone.utc), self._o[i], self._h[i],
                      self._l[i], self._c[i], self._v[i], closed=True, oi=None if oi < 0 else oi)

    def _index(self, ts: datetime) -> int:
        t = int(ts.timestamp())
        i = bisect_left(self._t, t)
        return i if i < len(self._t) and self._t[i] == t else -1

    def __getitem__(self, ts: datetime) -> Candle:
        i = self._index(ts)
        if i < 0:
            raise KeyError(ts)
        return self._at(i)

    def get(self, ts: datetime, default=None):  # noqa: ANN001, ANN201 - the Mapping signature
        i = self._index(ts)
        return default if i < 0 else self._at(i)

    def __contains__(self, ts: object) -> bool:
        return isinstance(ts, datetime) and self._index(ts) >= 0

    def __iter__(self) -> Iterator[datetime]:
        return (datetime.fromtimestamp(t, timezone.utc) for t in self._t)

    def __len__(self) -> int:
        return len(self._t)

    def __getstate__(self) -> tuple:
        return tuple(getattr(self, k) for k in self.__slots__)

    def __setstate__(self, state: tuple) -> None:
        for k, v in zip(self.__slots__, state):
            setattr(self, k, v)


@dataclass
class BacktestData:
    source: str  # "real" | "simulated"
    bars: list[Candle]
    futures: dict[datetime, Candle]
    vix: dict[datetime, float]
    options: dict[tuple[date, float, OptionType], Mapping[datetime, Candle]] = field(default_factory=dict)
    option_lot_sizes: dict[tuple[date, float, OptionType], int] = field(default_factory=dict)
    built_from: str | None = None  # stored timeframe the candles were resampled from

    @property
    def trading_days(self) -> list[str]:
        return sorted({b.ts.astimezone(IST).date().isoformat() for b in self.bars})


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _source_filter(source: str):  # noqa: ANN202
    return InstrumentRow.token.like(f"{SIM_PREFIX}%") if source == "simulated" \
        else InstrumentRow.token.not_like(f"{SIM_PREFIX}%")


def _bounds(start: date, end: date) -> tuple[datetime, datetime]:
    lo = datetime.combine(start, time(0, 0), tzinfo=IST).astimezone(timezone.utc)
    hi = datetime.combine(end, time(23, 59, 59), tzinfo=IST).astimezone(timezone.utc)
    return lo, hi


_COLS = (CandleRow.instrument_id, CandleRow.ts, CandleRow.open, CandleRow.high, CandleRow.low, CandleRow.close,
         CandleRow.volume, CandleRow.oi)


def _candle_query(s: AsyncSession, ids: list[int], tf: str, start: date, end: date):  # noqa: ANN202
    lo, hi = _bounds(start, end)
    if _naive(s):  # SQLite stores UTC timestamps without an offset
        lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
    return select(*_COLS).where(CandleRow.instrument_id.in_(ids), CandleRow.timeframe == tf, CandleRow.ts >= lo, CandleRow.ts <= hi)


async def _candles(s: AsyncSession, ids: list[int], tf: str, start: date, end: date) -> list[tuple[int, object]]:
    """(instrument id, row) by time. Plain column rows, not ORM objects: the session does not keep every row alive."""
    if not ids:
        return []
    rows = (await s.execute(_candle_query(s, ids, tf, start, end).order_by(CandleRow.ts))).all()
    return [(r.instrument_id, r) for r in rows]


def _naive(s: AsyncSession) -> bool:
    return s.bind is not None and s.bind.dialect.name == "sqlite"


SESSION_START, SESSION_END = time(9, 15), time(15, 30)


def in_session(ts: datetime) -> bool:
    """Regular continuous session only. Fyers occasionally includes pre-open (09:08-09:14) prints."""
    t = ts.astimezone(IST).time()
    return SESSION_START <= t < SESSION_END


def _to_candle(token: str, tf: str, r) -> Candle:  # noqa: ANN001 - a CandleRow or a column row with the same names
    return Candle(token, tf, _utc(r.ts), r.open, r.high, r.low, r.close, r.volume or 0, closed=True, oi=r.oi)


async def stored_timeframes(s: AsyncSession, underlying: str, source: str) -> list[str]:
    q = (select(func.distinct(CandleRow.timeframe))
         .join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
         .where(InstrumentRow.symbol == underlying, InstrumentRow.instrument_type == "INDEX", _source_filter(source)))
    return sorted((await s.execute(q)).scalars().all(), key=lambda tf: SUPPORTED.get(tf, 10_000))


def _usable(timeframe: str, stored: list[str]) -> list[str]:
    tgt = SUPPORTED[timeframe]
    return [tf for tf in stored if tf in SUPPORTED and SUPPORTED[tf] <= tgt and tgt % SUPPORTED[tf] == 0]


async def _tf_stats(s: AsyncSession, underlying: str, source: str, tf: str,
                    start: date | None = None, end: date | None = None) -> tuple:
    q = (select(func.min(CandleRow.ts), func.max(CandleRow.ts), func.count())
         .join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
         .where(InstrumentRow.symbol == underlying, InstrumentRow.instrument_type == "INDEX",
                CandleRow.timeframe == tf, _source_filter(source)))
    if start and end:
        lo, hi = _bounds(start, end)
        if _naive(s):
            lo, hi = lo.replace(tzinfo=None), hi.replace(tzinfo=None)
        q = q.where(CandleRow.ts >= lo, CandleRow.ts <= hi)
    return (await s.execute(q)).one()


async def pick_source_tf(s: AsyncSession, underlying: str, source: str, timeframe: str,
                         start: date | None = None, end: date | None = None) -> str | None:
    """Stored timeframe that covers the most market minutes of the range (ties -> coarser).

    A few days of native 5m candles from the live collector must not win over a year of 1m.
    """
    best: tuple[int, int, str] | None = None
    for tf in _usable(timeframe, await stored_timeframes(s, underlying, source)):
        _, _, n = await _tf_stats(s, underlying, source, tf, start, end)
        key = (n * SUPPORTED[tf], SUPPORTED[tf], tf)
        if n and (best is None or key > best):
            best = key
    return best[2] if best else None


async def coverage(s: AsyncSession, underlying: str, timeframe: str) -> dict:
    """Stored index history per source, for the UI and for `source="auto"`.

    If `timeframe` itself isn't stored, coverage is reported for the timeframe it would be built from.
    """
    out = {}
    for source in ("real", "simulated"):
        stored = await stored_timeframes(s, underlying, source)
        src_tf = await pick_source_tf(s, underlying, source, timeframe)
        lo = hi = None
        n = 0
        if src_tf:
            lo, hi, n = await _tf_stats(s, underlying, source, src_tf)
        out[source] = {
            "candles": n,
            "from": _utc(lo).astimezone(IST).date().isoformat() if lo else None,
            "to": _utc(hi).astimezone(IST).date().isoformat() if hi else None,
            "stored_timeframes": stored,
            "built_from": src_tf,
        }
    opts = (await s.execute(
        select(func.count(func.distinct(CandleRow.instrument_id)))
        .join(InstrumentRow, InstrumentRow.id == CandleRow.instrument_id)
        .where(InstrumentRow.underlying == underlying, InstrumentRow.instrument_type == "OPT", _source_filter("real"))
    )).scalar_one()
    out["real"]["option_contracts_with_candles"] = opts
    return out


def near_money(bars: list[Candle], underlying: str, cal: TradingCalendar, pad: int = 2,
               overrides: dict | None = None) -> set[tuple[date, float]]:
    """(expiry, strike) pairs a strategy could trade over `bars`: for each day, the calendar's expiry (rolled or not on
    expiry day) and every strike within `pad` strikes of that day's range. Option candles outside it are not loaded."""
    from trader.backtest.pricing import expiry_for
    from trader.instruments.specs import get_spec
    from trader.instruments.strikes import atm_strike

    step = get_spec(underlying).strike_interval
    days: dict[date, tuple[float, float]] = {}
    for b in bars:
        d = b.ts.astimezone(IST).date()
        lo, hi = days.get(d, (b.open, b.open))
        days[d] = (min(lo, b.low), max(hi, b.high))
    out: set[tuple[date, float]] = set()
    for d, (lo, hi) in days.items():
        exps = {expiry_for(underlying, d, cal, roll, overrides) for roll in (True, False)}
        for k in range(atm_strike(underlying, lo) - pad * step, atm_strike(underlying, hi) + pad * step + 1, step):
            out.update((e, float(k)) for e in exps)
    return out


async def load(s: AsyncSession, underlying: str, timeframe: str, start: date, end: date,
               source: str = "auto", with_options: bool = False, strike_pad: int = 2) -> BacktestData:
    """Load index, futures, VIX (and option) candles at `timeframe`, resampling from a finer
    stored timeframe when `timeframe` itself was never downloaded."""
    minutes(timeframe)  # validate
    if source == "auto":
        cov = await coverage(s, underlying, timeframe)
        source = "real" if cov["real"]["candles"] else "simulated"
    src_tf = await pick_source_tf(s, underlying, source, timeframe, start, end)
    if src_tf is None:
        return BacktestData(source, [], {}, {}, built_from=None)

    async def instruments(**where) -> list[InstrumentRow]:  # noqa: ANN003
        q = select(InstrumentRow).where(_source_filter(source),
                                        *[getattr(InstrumentRow, k) == v for k, v in where.items()])
        return list((await s.execute(q)).scalars().all())

    holidays = set(get_calendar().holidays)

    def up(cs: list[Candle]) -> list[Candle]:
        # Exchange holidays can still carry a short special session (Diwali Muhurat trading, e.g. 13:45-14:45 on
        # 21 Oct 2025): those candles are not a normal trading day and are left out of backtests.
        cs = sorted({c.ts: c for c in cs if in_session(c.ts) and c.ts.astimezone(IST).date() not in holidays}.values(),
                    key=lambda c: c.ts)
        return resample(cs, timeframe, src_tf) if src_tf != timeframe else cs

    idx = await instruments(symbol=underlying, instrument_type="INDEX")
    bars = up([_to_candle(underlying, src_tf, r) for _, r in await _candles(s, [i.id for i in idx], src_tf, start, end)])

    futs = sorted(await instruments(underlying=underlying, instrument_type="FUT"),
                  key=lambda i: i.expiry or date.max, reverse=True)
    rank = {f.id: n for n, f in enumerate(futs)}  # later in list = nearer expiry, wins on overlap
    merged: dict[datetime, Candle] = {}
    for iid, r in sorted(await _candles(s, list(rank), src_tf, start, end), key=lambda x: rank[x[0]]):
        merged[_utc(r.ts)] = _to_candle(f"{underlying}-FUT", src_tf, r)
    futures = {c.ts: c for c in up(list(merged.values()))}

    vix_inst = await instruments(symbol=VIX_SYMBOL, instrument_type="INDEX")
    vix_c = up([_to_candle(VIX_SYMBOL, src_tf, r) for _, r in await _candles(s, [i.id for i in vix_inst], src_tf, start, end)])
    vix = {c.ts: c.close for c in vix_c}

    data = BacktestData(source, bars, futures, vix, built_from=src_tf)
    if with_options and source == "real":
        from trader.instruments.contract_history import default_expiry_overrides

        # Only contracts that could have been near the money: years of downloads hold many more than a run can use.
        wanted = near_money(bars, underlying, get_calendar(), strike_pad, default_expiry_overrides())
        opts = {i.id: i for i in await instruments(underlying=underlying, instrument_type="OPT")
                if (i.expiry, float(i.strike or 0)) in wanted}
        await _load_options(s, data, opts, src_tf, start, end, up)
    return data


async def _load_options(s: AsyncSession, data: BacktestData, opts: dict[int, InstrumentRow], src_tf: str,
                        start: date, end: date, up) -> None:  # noqa: ANN001
    """Stream option candles one contract at a time (the index orders them by contract, then time) and keep each as a
    compact CandleSeries, so memory holds numbers, not millions of row and Candle objects."""
    if not opts:
        return
    q = _candle_query(s, list(opts), src_tf, start, end).order_by(CandleRow.instrument_id, CandleRow.ts)
    current: int | None = None
    rows: list = []

    def flush() -> None:
        if current is None or not rows:
            return
        inst = opts[current]
        key = (inst.expiry, float(inst.strike), OptionType(inst.option_type))
        data.options[key] = CandleSeries.of(up([_to_candle(inst.token, src_tf, r) for r in rows]), inst.token, data_tf)
        data.option_lot_sizes[key] = inst.lot_size

    data_tf = data.bars[0].timeframe if data.bars else src_tf
    result = await s.stream(q.execution_options(yield_per=20_000))
    async for part in result.partitions(20_000):
        for r in part:
            if r.instrument_id != current:
                flush()
                current, rows = r.instrument_id, []
            rows.append(r)
    flush()


def option_coverage(data: BacktestData, underlying: str, start: date, cal: TradingCalendar, roll_on_expiry_day: bool = True,
                    overrides: dict | None = None) -> dict:
    """How much of what a strategy could trade is stored (PRD §24.2 B1.8): for every bar from `start`, the contract
    at the money at that bar's open on the expiry the calendar gives, both CE and PE. Reported before a Real or
    hybrid run is trusted, so a 20% coverage run is not read as a market result."""
    from trader.backtest.pricing import expiry_for
    from trader.instruments.strikes import atm_strike

    needed: set[tuple[date, float, OptionType]] = set()
    by_day: dict[date, date] = {}
    for b in data.bars:
        d = b.ts.astimezone(IST).date()
        if d < start:
            continue
        e = by_day.get(d) or by_day.setdefault(d, expiry_for(underlying, d, cal, roll_on_expiry_day, overrides))
        k = float(atm_strike(underlying, b.open))
        needed.update({(e, k, OptionType.CE), (e, k, OptionType.PE)})
    have = sum(1 for k in needed if k in data.options)
    exps = sorted({k[0] for k in needed})
    missing_exps = [e for e in exps if not any(k[0] == e for k in data.options)]
    return {"expected_contracts": len(needed), "stored_contracts": have,
            "pct": round(100 * have / len(needed), 1) if needed else None,
            "expiries": len(exps), "expiries_without_any_contract": len(missing_exps),
            "missing_expiries_sample": [e.isoformat() for e in missing_exps[:5]]}

