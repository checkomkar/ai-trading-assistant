"""Shoonya (Finvasia) live market data: indices, near futures and an ATM window of NIFTY / BANKNIFTY options.

One provider instance serves one customer's session (the live feed gets the session token through `token_provider`).
Instrument tokens are Shoonya's own keys, "<exchange>|<token>" (for example "NSE|26000"), which are also exactly what
the websocket subscribes with. Lot sizes, tick sizes and expiries come from Shoonya's symbol master, never from
`instruments/specs.py`. History is only needed for the short chart warm-up: stored history is shared and not fetched here.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import zipfile
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable

from trader.brokers.shoonya import auth
from trader.brokers.shoonya.auth import ShoonyaAuthError, num
from trader.domain import Instrument, InstrumentType, Tick
from trader.instruments.calendar import IST
from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL
from trader.instruments.strikes import OptionType
from trader.logging import get_logger
from trader.market_data.base import MarketDataProvider, ProviderAuthError, SubscriptionPolicy
from trader.market_data.candles import Candle

log = get_logger("shoonya")

INDEX_TOKENS = {"NIFTY": "26000", "BANKNIFTY": "26009", VIX_SYMBOL: "26017"}  # NSE tokens, stable
INTERVALS = {"1m": "1", "3m": "3", "5m": "5", "10m": "10", "15m": "15", "30m": "30", "60m": "60"}
HEARTBEAT_S = 20
SUBSCRIBE_BATCH = 200
CONNECT_TIMEOUT_S = 15
_MASTER_CACHE: dict = {}


def _download(exch: str) -> str:
    with urllib.request.urlopen(auth.MASTER_URL.format(exch=exch), timeout=60) as resp:  # noqa: S310 - fixed https host
        raw = resp.read()
    z = zipfile.ZipFile(io.BytesIO(raw))
    return z.read(z.namelist()[0]).decode("utf-8", "replace")


def build_instruments(nfo_csv: str, today: date) -> list[Instrument]:
    """Indices (fixed tokens) + live NIFTY/BANKNIFTY index futures and options from Shoonya's NFO master."""
    return instruments_from_rows(csv.DictReader(io.StringIO(nfo_csv)), today)


def instruments_from_rows(rows, today: date) -> list[Instrument]:  # noqa: ANN001
    """Rows keyed like Shoonya's master (Token, LotSize, Symbol, TradingSymbol, Expiry, Instrument, OptionType, StrikePrice,
    TickSize): other brokers on the same exchange tokens map their own columns onto these first."""
    out = [Instrument(f"NSE|{tk}", sym, sym, "NSE", InstrumentType.INDEX, tick_size=0.01 if sym == VIX_SYMBOL else 0.05)
           for sym, tk in INDEX_TOKENS.items()]
    for r in rows:
        if r.get("Symbol") not in UNDERLYINGS or r.get("Instrument") not in ("OPTIDX", "FUTIDX"):
            continue
        try:
            exp = datetime.strptime(r["Expiry"], "%d-%b-%Y").date()
        except (KeyError, ValueError):
            continue
        if exp < today:
            continue
        common = dict(lot_size=int(r["LotSize"] or 1), tick_size=float(r.get("TickSize") or 0.05), expiry=exp)
        token = f"NFO|{r['Token']}"
        if r["Instrument"] == "FUTIDX":
            out.append(Instrument(token, r["TradingSymbol"], r["Symbol"], "NFO", InstrumentType.FUT, **common))
        elif r.get("OptionType") in ("CE", "PE"):
            out.append(Instrument(token, r["TradingSymbol"], r["Symbol"], "NFO", InstrumentType.OPT,
                                  strike=float(r["StrikePrice"]), option_type=OptionType(r["OptionType"]), **common))
    return out


def merge_frame(state: dict[str, dict], frame: dict) -> tuple[str, dict] | None:
    """Websocket touchline frames ('tk' = full, 'tf' = only the fields that changed) -> the merged state of that
    token. Returns (token, state) or None for frames that are not quotes."""
    if frame.get("t") not in ("tk", "tf") or "e" not in frame or "tk" not in frame:
        return None
    key = f"{frame['e']}|{frame['tk']}"
    cur = state.setdefault(key, {})
    cur.update({k: v for k, v in frame.items() if k not in ("t", "e", "tk")})
    return key, cur


def to_tick(token: str, q: dict, now: datetime) -> Tick | None:
    ltp = num(q.get("lp"))
    if ltp is None or ltp <= 0:
        return None
    vol, oi = num(q.get("v")), num(q.get("oi"))
    return Tick(token, now, ltp, bid=num(q.get("bp1")), ask=num(q.get("sp1")),
                volume=int(vol) if vol is not None else None, oi=int(oi) if oi is not None else None,
                open=num(q.get("o")), high=num(q.get("h")), low=num(q.get("l")), prev_close=num(q.get("c")))


def parse_series(rows: Any, token: str, timeframe: str) -> list[Candle]:
    """TPSeries answers newest first: [{"time": "22-10-2026 09:15:00", "into", "inth", "intl", "intc", "v", "oi"}]."""
    if not isinstance(rows, list):
        return []
    out = []
    for r in rows:
        try:
            ts = datetime.strptime(r["time"], "%d-%m-%Y %H:%M:%S").replace(tzinfo=IST).astimezone(timezone.utc)
            o, h, l, c = (float(r[k]) for k in ("into", "inth", "intl", "intc"))
        except (KeyError, ValueError, TypeError):
            continue
        oi = num(r.get("oi"))
        out.append(Candle(token, timeframe, ts, o, h, l, c, int(num(r.get("v")) or 0), closed=True,
                          oi=int(oi) if oi else None))
    return sorted(out, key=lambda c: c.ts)


class ShoonyaMarketDataProvider(MarketDataProvider):
    """Also the base of every NorenAPI broker (Flattrade): they share the websocket frames, the REST calls and the exchange
    tokens, and differ in addresses, symbol master and sign-in. Subclasses override the hooks below."""

    name = "shoonya"
    label = "Shoonya"
    is_simulated = False
    ws_urls = auth.WS_URLS

    def _user_id(self, settings) -> str:  # noqa: ANN001
        return settings.shoonya_user_id

    def _build(self, text: str, today: date) -> list[Instrument]:
        return build_instruments(text, today)

    def _download_master(self) -> str:
        return _download("NFO")

    def _default_http(self) -> Callable[..., Any]:
        return auth.post

    def __init__(self, settings, *, token_provider: Callable[[], str | None] | None = None,
                 http: Callable[..., Any] | None = None, ws_connect: Callable[..., Any] | None = None,
                 master_loader: Callable[[], str] | None = None, now_fn: Callable[[], datetime] | None = None) -> None:
        super().__init__(subscription=SubscriptionPolicy(
            strikes_each_side=getattr(settings, "fyers_strikes_each_side", 20),
            expiries_per_underlying=getattr(settings, "fyers_expiries_per_underlying", 2),
            max_symbols=None))
        self.settings = settings
        self._user = self._user_id(settings)
        self._token_provider = token_provider or (lambda: None)
        self._http = http or self._default_http()
        self._ws_connect = ws_connect
        self._master_loader = master_loader or self._download_master
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._ws: Any = None
        self._beat: asyncio.Task | None = None
        self._state: dict[str, dict] = {}

    # ---- credentials -------------------------------------------------------------------------
    def _token(self) -> str:
        token = self._token_provider()
        if not token or not self._user:
            raise ShoonyaAuthError(f"Not signed in to {self.label}. Sign in on the Brokers page.")
        return token

    # ---- discovery -----------------------------------------------------------------------------
    async def discover_instruments(self) -> list[Instrument]:
        today = self._now().astimezone(IST).date()
        key = (self.name, today)
        cached = _MASTER_CACHE.get(key)
        if cached is None:  # one download a day however many customers are connected; Instrument is immutable
            text = await asyncio.to_thread(self._master_loader)
            cached = _MASTER_CACHE[key] = self._build(text, today)
            for old in [k for k in _MASTER_CACHE if k[1] != today]:
                del _MASTER_CACHE[old]
        if len(cached) <= len(INDEX_TOKENS):
            raise RuntimeError(f"{self.label}'s symbol master had no NIFTY / BANKNIFTY contracts")
        return cached

    # ---- websocket -------------------------------------------------------------------------------
    async def _connect(self) -> None:
        token = self._token()
        connect = self._ws_connect
        if connect is None:
            import websockets  # bundled with uvicorn[standard]

            connect = websockets.connect
        self._state = {}
        last: Exception | None = None
        for url in self.ws_urls:  # the feed has been served from more than one address
            try:
                self._ws = await asyncio.wait_for(connect(url), CONNECT_TIMEOUT_S)
                await self._ws.send(json.dumps({"t": "c", "uid": self._user, "actid": self._user, "susertoken": token, "source": "API"}))
                ack = json.loads(await asyncio.wait_for(self._ws.recv(), CONNECT_TIMEOUT_S))
            except (OSError, asyncio.TimeoutError, ValueError) as exc:
                last = exc
                await self._close_ws()
                continue
            if ack.get("t") == "ck" and ack.get("s") == "OK":
                self._beat = asyncio.create_task(self._heartbeat())
                return
            await self._close_ws()
            raise ShoonyaAuthError(f"{self.label} rejected the session ({ack.get('s') or ack}). Sign in again on the Brokers page.")
        raise ConnectionError(f"{self.label} data socket failed to connect: {last}")

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                await self._ws.send(json.dumps({"t": "h"}))
            except Exception:  # noqa: BLE001 - the stream notices a dead socket itself
                return

    async def _close_ws(self) -> None:
        if self._beat is not None:
            self._beat.cancel()
            self._beat = None
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def _disconnect(self) -> None:
        await self._close_ws()

    async def _on_subscribe(self, tokens: set[str]) -> None:
        keys = sorted(tokens)
        for i in range(0, len(keys), SUBSCRIBE_BATCH):
            await self._ws.send(json.dumps({"t": "t", "k": "#".join(keys[i:i + SUBSCRIBE_BATCH])}))

    async def _on_unsubscribe(self, tokens: set[str]) -> None:
        await self._ws.send(json.dumps({"t": "u", "k": "#".join(sorted(tokens))}))

    async def _stream(self) -> AsyncIterator[Tick]:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                self.heartbeat()
                if frame.get("t") == "ck" and frame.get("s") != "OK":
                    raise ShoonyaAuthError(f"{self.label} rejected the session ({frame.get('s')}). Sign in again on the Brokers page.")
                merged = merge_frame(self._state, frame)
                if merged is not None:
                    tick = to_tick(merged[0], merged[1], self._now())
                    if tick is not None:
                        yield tick
        except ProviderAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - websockets' own ConnectionClosed and friends
            raise ConnectionError(f"{self.label} data socket closed: {exc}") from None
        raise ConnectionError(f"{self.label} data socket closed")

    # ---- REST: first quotes + chart warm-up --------------------------------------------------------
    async def snapshot(self, instruments: list[Instrument]) -> list[Tick]:
        """First prices before the stream delivers them. Only the few core instruments are asked for: one request each,
        and every subscribed contract's first full quote arrives on the websocket anyway."""
        token, ticks = self._token(), []
        for inst in instruments:
            exch, tk = inst.token.split("|", 1)
            q = await asyncio.to_thread(auth.quote, self._user, token, exch, tk, self._http)
            t = to_tick(inst.token, q, self._now())
            if t is not None:
                ticks.append(t)
        return ticks

    async def history(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        if timeframe not in INTERVALS or start >= end:
            return []  # 2m / 20m / daily are not offered by Shoonya's series call
        token = self._token()
        exch, tk = instrument.token.split("|", 1)
        out: list[Candle] = []
        cur = start
        while cur < end:  # ask in modest windows so a long warm-up never hits a per-call row limit
            nxt = min(cur + timedelta(days=7), end)
            rows = await asyncio.to_thread(self._http, "/TPSeries", {
                "ordersource": "API", "uid": self._user, "exch": exch, "token": tk, "st": str(int(cur.timestamp())),
                "et": str(int(nxt.timestamp())), "intrv": INTERVALS[timeframe]}, token)
            if isinstance(rows, dict):
                if rows.get("stat") == "Not_Ok" and "no data" not in str(rows.get("emsg", "")).lower():
                    auth.check(rows, "the chart request")
            else:
                out += parse_series(rows, instrument.token, timeframe)
            cur = nxt
            await asyncio.sleep(0.3)
        dedup = {c.ts: c for c in out if start <= c.ts < end}
        return [dedup[k] for k in sorted(dedup)]
