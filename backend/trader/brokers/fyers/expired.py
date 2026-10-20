"""Expired F&O contract candles (PRD M2 item 6).

Endpoint: GET /data/history/fno/expired/historical-data (SDK `fno_historical_data`).
Response columns are read *by name* (`columns`); the docs show both `iv` and
`implied_volatility`, and optional columns may be absent.

Plan limits (Oct 2026): Standard = 10 intraday requests/day, Prime = unrestricted.
A small on-disk counter enforces the daily quota so a long download resumes tomorrow
instead of failing halfway.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from trader.domain import Instrument, InstrumentType
from trader.instruments.calendar import IST
from trader.instruments.specs import get_spec
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
WEEKLY_MONTH_CODE = {**{m: str(m) for m in range(1, 10)}, 10: "O", 11: "N", 12: "D"}
MAX_RANGE = timedelta(days=100)
RESOLUTION = {"1m": "1", "5m": "5", "15m": "15", "1d": "1D"}

_SYMBOL_RE = re.compile(
    r"^(?:NSE:)?(?P<und>[A-Z]+?)(?P<yy>\d{2})"
    r"(?:(?P<mon>JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)|(?P<m>[1-9OND])(?P<dd>\d{2}))"
    r"(?P<strike>\d+(?:\.\d+)?)(?P<ot>CE|PE)$"
)


def build_symbol(underlying: str, expiry: date, strike: float, option_type: OptionType, monthly: bool) -> str:
    """NSE option ticker. Monthly: NIFTY25MAR23000CE. Weekly: NIFTY25N0617350CE (YY, month code, DD)."""
    k = f"{strike:g}"
    yy = f"{expiry.year % 100:02d}"
    if monthly:
        return f"NSE:{underlying}{yy}{MONTHS[expiry.month - 1]}{k}{option_type.value}"
    return f"NSE:{underlying}{yy}{WEEKLY_MONTH_CODE[expiry.month]}{expiry.day:02d}{k}{option_type.value}"


@dataclass(frozen=True)
class ParsedSymbol:
    underlying: str
    strike: float
    option_type: OptionType
    weekly_expiry: date | None  # None for monthly symbols (day not encoded)


def parse_symbol(symbol: str) -> ParsedSymbol:
    m = _SYMBOL_RE.match(symbol)
    if not m:
        raise ValueError(f"Not an NSE option symbol: {symbol}")
    weekly = None
    if m["m"]:
        month = {"O": 10, "N": 11, "D": 12}.get(m["m"]) or int(m["m"])
        weekly = date(2000 + int(m["yy"]), month, int(m["dd"]))
    return ParsedSymbol(m["und"], float(m["strike"]), OptionType(m["ot"]), weekly)


def expired_instrument(symbol: str, expiry: date) -> Instrument:
    p = parse_symbol(symbol)
    if p.weekly_expiry and p.weekly_expiry != expiry:
        raise ValueError(f"{symbol} encodes expiry {p.weekly_expiry}, not {expiry}")
    spec = get_spec(p.underlying)
    token = symbol if symbol.startswith("NSE:") else f"NSE:{symbol}"
    return Instrument(token, token.split(":", 1)[1], p.underlying, "NFO", InstrumentType.OPT,
                      lot_size=spec.lot_size, tick_size=spec.tick_size, expiry=expiry,
                      strike=p.strike, option_type=p.option_type)


def request(symbol: str, timeframe: str, start: date, end: date) -> dict:
    return {"symbol": symbol, "resolution": RESOLUTION[timeframe], "date_format": "1",
            "range_from": start.isoformat(), "range_to": end.isoformat(),
            "include_oi": "1", "include_greeks": "1"}


def parse_response(resp: dict, token: str, timeframe: str) -> list[Candle]:
    if resp.get("s") == "no_data":
        return []
    if resp.get("s") != "ok":
        raise RuntimeError(f"expired data error: {resp.get('message') or resp}")
    cols = resp.get("columns") or ["timestamp", "open", "high", "low", "close", "volume"]
    idx = {name: i for i, name in enumerate(cols)}
    oi_i = idx.get("open_interest", idx.get("oi"))
    out = []
    for row in resp.get("candles") or []:
        vals = [row[idx[k]] if idx.get(k) is not None and idx[k] < len(row) else None
                for k in ("timestamp", "open", "high", "low", "close", "volume")]
        if None in vals[:5]:
            continue  # nulls mean "no trade in this bar"; never fill them
        ts, o, h, l, c, v = vals
        oi = row[oi_i] if oi_i is not None and oi_i < len(row) else None
        out.append(Candle(token, timeframe, datetime.fromtimestamp(int(ts), tz=timezone.utc),
                          float(o), float(h), float(l), float(c), int(v or 0), closed=True,
                          oi=int(oi) if oi is not None else None))
    return out


class DailyQuota:
    """Counts expired-intraday requests per IST day. limit <= 0 means unlimited (Prime)."""

    def __init__(self, path: Path, limit: int, now_fn=lambda: datetime.now(timezone.utc)) -> None:  # noqa: ANN001
        self.path, self.limit, self._now = path, limit, now_fn

    def _today(self) -> str:
        return self._now().astimezone(IST).date().isoformat()

    def used(self) -> int:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return 0
        return int(data.get(self._today(), 0))

    def remaining(self) -> int | None:
        return None if self.limit <= 0 else max(0, self.limit - self.used())

    def consume(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({self._today(): self.used() + 1}), encoding="utf-8")
