"""Free daily candles for NSE stocks from Yahoo Finance's public chart endpoint (no account, no login).

It plugs into `equity_daily.refresh` as a provider with the same `history()` the broker has. Prices are split-adjusted
(not dividend-adjusted), which is what price-based rules want. It is an unofficial endpoint: it can change or rate-limit
without notice, and Yahoo's terms restrict redistributing the data, so treat it as a research source and check the terms
before offering the data to paying users (the broker's own data or a licensed vendor is the commercial route).
"""

from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from trader.domain import Instrument
from trader.market_data.candles import Candle

URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d&events=div,splits"
PAUSE_S = 0.4  # be gentle: one request per stock, spaced out


def yahoo_symbol(symbol: str) -> str:
    return urllib.parse.quote(f"{symbol}.NS", safe="-")


def parse_chart(payload: dict, token: str) -> list[Candle]:
    res = (payload.get("chart") or {}).get("result")
    if not res:
        err = (payload.get("chart") or {}).get("error") or {}
        raise RuntimeError(f"Yahoo: {err.get('description') or 'no data'}")
    r = res[0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, ts in enumerate(r.get("timestamp") or []):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue  # never fabricate a missing day
        out.append(Candle(token, "1d", datetime.fromtimestamp(int(ts), tz=timezone.utc), float(o), float(h), float(l), float(c),
                          int(q["volume"][i] or 0), closed=True))
    return out


def _get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed https host
        return json.load(resp)


class YahooDailyProvider:
    is_simulated = False
    name = "yahoo"

    async def history(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        if timeframe != "1d":
            raise ValueError("Yahoo source only supplies daily candles")
        url = URL.format(sym=yahoo_symbol(instrument.symbol), p1=int(start.timestamp()), p2=int(end.timestamp()))
        try:
            payload = await asyncio.to_thread(_get, url)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise RuntimeError(f"Yahoo has no data for {instrument.symbol}.NS") from None
            raise
        await asyncio.sleep(PAUSE_S)
        return [c for c in parse_chart(payload, instrument.token) if start <= c.ts < end]
