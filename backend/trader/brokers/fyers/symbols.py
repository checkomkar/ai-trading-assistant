"""Fyers public symbol master -> `Instrument`s (PRD M2 item 2, §14.2).

Lot size, tick size, expiry (already holiday-adjusted by the exchange) and previous-day OI
come from the master — never from `specs.py`. The ~90 MB master is downloaded at most once
a day and reduced to a small cache containing only NIFTY / BANKNIFTY index futures & options.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Iterable

from trader.domain import Instrument, InstrumentType
from trader.instruments.calendar import IST
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL
from trader.instruments.strikes import OptionType

MASTER_URL = "https://public.fyers.in/sym_details/NSE_FO_sym_master.json"

# Indices live in the cash-segment master; their tickers and tick sizes are stable.
INDEX_TICKERS: dict[str, tuple[str, float]] = {
    "NIFTY": ("NSE:NIFTY50-INDEX", 0.05),
    "BANKNIFTY": ("NSE:NIFTYBANK-INDEX", 0.05),
    VIX_SYMBOL: ("NSE:INDIAVIX-INDEX", 0.01),
}

EX_INST_INDEX_FUT = 11
EX_INST_INDEX_OPT = 14
KEEP = ("symTicker", "exSymName", "exInstType", "underSym", "expiryDate", "optType", "strikePrice",
        "minLotSize", "tickSize", "previousOi", "previousClose")


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=180) as resp, dest.open("wb") as f:  # noqa: S310 - fixed https URL
        shutil.copyfileobj(resp, f, length=1 << 20)


def filter_master(master: dict | Iterable[dict]) -> list[dict]:
    values = master.values() if isinstance(master, dict) else master
    return [
        {k: row.get(k) for k in KEEP}
        for row in values
        if row.get("underSym") in UNDERLYINGS and row.get("exInstType") in (EX_INST_INDEX_FUT, EX_INST_INDEX_OPT)
    ]


def fetch_master(cache_dir: Path, today: date, download: Callable[[str, Path], None] = _download) -> list[dict]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"fo_master_{today.isoformat()}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    raw = cache_dir / "NSE_FO_sym_master.json.part"
    download(MASTER_URL, raw)
    try:
        rows = filter_master(json.loads(raw.read_text(encoding="utf-8")))
    finally:
        raw.unlink(missing_ok=True)
    if not rows:
        raise RuntimeError("Fyers symbol master contained no NIFTY/BANKNIFTY derivatives")
    cache.write_text(json.dumps(rows), encoding="utf-8")
    for old in cache_dir.glob("fo_master_*.json"):
        if old != cache:
            old.unlink(missing_ok=True)
    return rows


def expiry_date(epoch: str | int) -> date:
    return datetime.fromtimestamp(int(epoch), tz=IST).date()


def index_instruments() -> list[Instrument]:
    return [Instrument(ticker, sym, sym, "NSE", InstrumentType.INDEX, tick_size=tick)
            for sym, (ticker, tick) in INDEX_TICKERS.items()]


def build_instruments(rows: Iterable[dict], today: date) -> tuple[list[Instrument], dict[str, int]]:
    """Live (unexpired) futures and options + indices, and previous-day OI per token."""
    out = index_instruments()
    prev_oi: dict[str, int] = {}
    for r in rows:
        exp = expiry_date(r["expiryDate"])
        if exp < today:
            continue
        ticker = r["symTicker"]
        name = r.get("exSymName") or ticker.split(":", 1)[-1]
        common = dict(lot_size=int(r["minLotSize"]), tick_size=float(r["tickSize"]), expiry=exp)
        if r["exInstType"] == EX_INST_INDEX_FUT:
            out.append(Instrument(ticker, name, r["underSym"], "NFO", InstrumentType.FUT, **common))
        else:
            if r.get("optType") not in ("CE", "PE"):
                continue
            out.append(Instrument(ticker, name, r["underSym"], "NFO", InstrumentType.OPT,
                                  strike=float(r["strikePrice"]), option_type=OptionType(r["optType"]), **common))
        if r.get("previousOi"):
            prev_oi[ticker] = int(float(r["previousOi"]))
    return out, prev_oi
