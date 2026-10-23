"""Fyers market-data provider (PRD M2 items 3-5).

The official SDK's `FyersDataSocket` is thread-based; its callbacks are bridged into asyncio
through `loop.call_soon_threadsafe`. The SDK's own reconnect is disabled: the base class
reconnects with exponential backoff and re-subscribes, so behaviour matches every provider.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable

from trader.brokers.fyers.auth import FyersAuthError, load_token, token_path
from trader.brokers.fyers.symbols import build_instruments, fetch_master
from trader.config import Settings
from trader.domain import Instrument, InstrumentType, Tick
from trader.instruments.calendar import IST, get_calendar
from trader.instruments.expiry import upcoming_expiries
from trader.instruments.specs import UNDERLYINGS, get_spec
from trader.logging import get_logger
from trader.market_data.base import MarketDataProvider, SubscriptionPolicy
from trader.market_data.candles import Candle

log = get_logger("fyers")

# Fyers intraday resolutions (minutes). Daily ("1D"), weekly and monthly also exist; seconds need Prime.
RESOLUTION = {"1m": "1", "2m": "2", "3m": "3", "5m": "5", "10m": "10", "15m": "15", "20m": "20",
              "30m": "30", "60m": "60", "1d": "1D"}
DAILY_CHUNK = timedelta(days=360)  # daily requests allow up to a year
HISTORY_CHUNK = timedelta(days=90)  # API allows up to 100 days per intraday request
HISTORY_PAUSE_S = 0.35  # stay well under the per-second rate limit
QUOTES_BATCH = 50
SYMBOL_LIMIT_CODE = 51001
CONTROL_TYPES = {"cn", "ful", "lit", "sub", "unsub", "cp", "cr", "msg"}
_INSTRUMENT_CACHE: dict = {}
AUTH_HINT = "Run: python -m trader.brokers.fyers.login"


# ---- pure parsers (unit-tested) --------------------------------------------------------
def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def _pos(v: Any) -> float | None:
    f = _num(v)
    return f if f and f > 0 else None


def _ts(epoch: Any, fallback: datetime) -> datetime:
    f = _num(epoch)
    return datetime.fromtimestamp(f, tz=timezone.utc) if f and f > 0 else fallback


def parse_socket_message(msg: dict, prev_oi: dict[str, int], now: datetime) -> Tick | None:
    """Map a SymbolUpdate frame ('sf' for F&O, 'if' for indices) to a Tick."""
    if not isinstance(msg, dict) or msg.get("type") not in ("sf", "if"):
        return None
    symbol, ltp = msg.get("symbol"), _pos(msg.get("ltp"))
    if not symbol or ltp is None:
        return None
    ts = _ts(msg.get("exch_feed_time") or msg.get("last_traded_time"), now)
    common = dict(open=_pos(msg.get("open_price")), high=_pos(msg.get("high_price")),
                  low=_pos(msg.get("low_price")), prev_close=_pos(msg.get("prev_close_price")))
    if msg["type"] == "if":
        return Tick(symbol, ts, ltp, volume=0, **common)
    vol, oi = _num(msg.get("vol_traded_today")), _num(msg.get("OI"))
    return Tick(symbol, ts, ltp, bid=_pos(msg.get("bid_price")), ask=_pos(msg.get("ask_price")),
                volume=int(vol) if vol is not None else None, oi=int(oi) if oi is not None else None,
                prev_oi=prev_oi.get(symbol), **common)


def parse_quotes(resp: dict, prev_oi: dict[str, int], now: datetime) -> list[Tick]:
    """REST /quotes -> Ticks (used to seed state; quotes carry no OI)."""
    ticks: list[Tick] = []
    for item in resp.get("d") or []:
        v = item.get("v") or {}
        sym, ltp = item.get("n") or v.get("symbol"), _pos(v.get("lp"))
        if item.get("s") not in (None, "ok") or not sym or ltp is None:
            continue
        is_index = sym.endswith("-INDEX")
        ticks.append(Tick(
            sym, _ts(v.get("tt"), now), ltp,
            bid=None if is_index else _pos(v.get("bid")), ask=None if is_index else _pos(v.get("ask")),
            volume=0 if is_index else (int(v["volume"]) if _num(v.get("volume")) is not None else None),
            open=_pos(v.get("open_price")), high=_pos(v.get("high_price")), low=_pos(v.get("low_price")),
            prev_close=_pos(v.get("prev_close_price")), prev_oi=prev_oi.get(sym),
        ))
    return ticks


def parse_history(resp: dict, token: str, timeframe: str) -> list[Candle]:
    if resp.get("s") == "no_data":
        return []
    if resp.get("s") != "ok":
        raise RuntimeError(f"history error: {resp.get('message') or resp}")
    out = []
    for row in resp.get("candles") or []:
        ts, o, h, l, c, v = row[:6]
        if None in (ts, o, h, l, c):
            continue  # API returns nulls for missing bars; never fabricate them
        oi = row[6] if len(row) > 6 and row[6] is not None else None
        out.append(Candle(token, timeframe, datetime.fromtimestamp(int(ts), tz=timezone.utc),
                          float(o), float(h), float(l), float(c), int(v or 0), closed=True,
                          oi=int(oi) if oi is not None else None))
    return out


def history_windows(start: datetime, end: datetime, chunk: timedelta = HISTORY_CHUNK) -> list[tuple[int, int]]:
    out, cur = [], start
    while cur < end:
        nxt = min(cur + chunk, end)
        out.append((int(cur.timestamp()), int(nxt.timestamp())))
        cur = nxt
    return out


# ---- SDK factories (replaced by fakes in tests) ----------------------------------------
def _default_socket(**kwargs: Any) -> Any:
    from fyers_apiv3.FyersWebsocket import data_ws

    return data_ws.FyersDataSocket(litemode=False, write_to_file=False, reconnect=False, **kwargs)


def _default_rest(client_id: str, token: str, log_dir: str) -> Any:
    from fyers_apiv3 import fyersModel

    return fyersModel.FyersModel(client_id=client_id, token=token, is_async=False, log_path=log_dir)


class FyersMarketDataProvider(MarketDataProvider):
    name = "fyers"
    is_simulated = False

    def __init__(
        self,
        settings: Settings,
        *,
        token_provider: Callable[[], str | None] | None = None,
        socket_factory: Callable[..., Any] = _default_socket,
        rest_factory: Callable[[str, str, str], Any] = _default_rest,
        master_loader: Callable[[], list[dict]] | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(subscription=SubscriptionPolicy(
            strikes_each_side=settings.fyers_strikes_each_side,
            expiries_per_underlying=settings.fyers_expiries_per_underlying,
            max_symbols=settings.fyers_max_symbols,
        ))
        self.settings = settings
        self._now = now_fn or (lambda: datetime.now(timezone.utc))
        self._dir = settings.data_dir / "fyers"
        self._log_dir = self._dir / "logs"
        self._token_provider = token_provider or (
            lambda: load_token(token_path(settings), settings.fyers_client_id))
        self._socket_factory = socket_factory
        self._rest_factory = rest_factory
        self._master_loader = master_loader or (
            lambda: fetch_master(self._dir, self._now().astimezone(IST).date()))
        self._prev_oi: dict[str, int] = {}
        self._rest: Any = None
        self._socket: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue | None = None

    def reopen(self) -> None:
        super().reopen()
        self._rest = None  # built with the old token; the next call makes one with the fresh token

    # ---- credentials / clients ---------------------------------------------------------
    def _token(self) -> str:
        if not self.settings.fyers_client_id:
            raise FyersAuthError("FYERS_CLIENT_ID is not set in .env")
        token = self._token_provider()
        if not token:
            raise FyersAuthError(f"No valid Fyers access token (missing or expired). {AUTH_HINT}")
        return token

    def rest(self) -> Any:
        if self._rest is None:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            self._rest = self._rest_factory(self.settings.fyers_client_id, self._token(), str(self._log_dir))
        return self._rest

    # ---- discovery -----------------------------------------------------------------------
    async def discover_instruments(self) -> list[Instrument]:
        today = self._now().astimezone(IST).date()
        cached = _INSTRUMENT_CACHE.get(today)
        if cached is None:  # parsed once per day however many feeds run; Instrument is immutable, so sharing is safe
            rows = await asyncio.to_thread(self._master_loader)
            cached = _INSTRUMENT_CACHE[today] = build_instruments(rows, today)
            for old in [d for d in _INSTRUMENT_CACHE if d != today]:
                del _INSTRUMENT_CACHE[old]
        instruments, prev_oi = cached
        self._prev_oi = dict(prev_oi)
        self._check_expiries(instruments)
        return instruments

    def _check_expiries(self, instruments: list[Instrument]) -> None:
        """PRD M2 item 7: the exchange's expiries vs ours. A mismatch usually means a missing holiday."""
        now = self._now()
        for und in UNDERLYINGS:
            listed = sorted({i.expiry for i in instruments
                             if i.underlying == und and i.is_option and i.expiry})[:3]
            ours = upcoming_expiries(get_spec(und), now, count=len(listed), cal=get_calendar())
            if listed and listed != ours:
                log.warning("expiry mismatch: check config/nse_holidays.yaml", event="instruments.expiry_mismatch",
                            symbol=und, exchange=[d.isoformat() for d in listed], computed=[d.isoformat() for d in ours])

    # ---- websocket ------------------------------------------------------------------------
    async def _connect(self) -> None:
        token = self._token()
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._socket = self._socket_factory(
            access_token=f"{self.settings.fyers_client_id}:{token}",
            log_path=str(self._log_dir),
            on_connect=lambda: None,
            on_close=self._cb_close,
            on_error=self._cb_error,
            on_message=self._cb_message,
        )
        # connect() blocks ~2s while the SDK opens its websocket thread.
        await asyncio.to_thread(self._socket.connect)
        if hasattr(self._socket, "is_connected") and not self._socket.is_connected():
            raise ConnectionError("Fyers data socket failed to connect")

    async def _disconnect(self) -> None:
        sock, self._socket = self._socket, None
        if sock is not None:
            await asyncio.to_thread(sock.close_connection)

    async def _on_subscribe(self, tokens: set[str]) -> None:
        await asyncio.to_thread(self._socket.subscribe, symbols=sorted(tokens), data_type="SymbolUpdate")

    async def _on_unsubscribe(self, tokens: set[str]) -> None:
        await asyncio.to_thread(self._socket.unsubscribe, symbols=sorted(tokens), data_type="SymbolUpdate")

    def _put(self, item: Any) -> None:
        if self._loop is not None and self._queue is not None:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _cb_message(self, msg: Any) -> None:
        self._put(msg)

    def _cb_close(self, msg: Any) -> None:
        self._put(ConnectionError(f"Fyers socket closed: {msg}"))

    def _cb_error(self, msg: Any) -> None:
        text = str(msg)
        code = msg.get("code") if isinstance(msg, dict) else None
        if code == SYMBOL_LIMIT_CODE:
            log.error("symbol limit exceeded for this plan; lower FYERS_MAX_SYMBOLS",
                      event="fyers.symbol_limit", detail=text)
        elif "token" in text.lower() and ("expire" in text.lower() or "invalid" in text.lower()):
            self._put(FyersAuthError(f"Fyers rejected the token: {text}. {AUTH_HINT}"))
        else:
            log.warning("socket error", event="fyers.socket_error", detail=text)

    async def _stream(self) -> AsyncIterator[Tick]:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            if isinstance(item, BaseException):
                raise item
            self.heartbeat()
            tick = parse_socket_message(item, self._prev_oi, self._now())
            if tick is not None:
                yield tick

    # ---- REST: snapshot + history ------------------------------------------------------------
    async def snapshot(self, instruments: list[Instrument]) -> list[Tick]:
        tickers = [i.token for i in instruments]
        ticks: list[Tick] = []
        for i in range(0, len(tickers), QUOTES_BATCH):
            batch = ",".join(tickers[i:i + QUOTES_BATCH])
            resp = await asyncio.to_thread(self.rest().quotes, {"symbols": batch})
            ticks += parse_quotes(resp, self._prev_oi, self._now())
        return ticks

    async def history(self, instrument: Instrument, timeframe: str, start: datetime, end: datetime) -> list[Candle]:
        if timeframe not in RESOLUTION:
            raise ValueError(f"Unsupported timeframe {timeframe}")
        # Requests older than the plan's depth fail with a bare "Invalid input": clamp instead.
        earliest = self._now() - timedelta(days=self.settings.fyers_intraday_history_days)
        daily = timeframe == "1d"  # daily history is not limited to the intraday depth
        if start < earliest and not daily:
            log.info("history start clamped to plan depth", event="fyers.history_clamped",
                     symbol=instrument.symbol, requested=start.isoformat(), clamped=earliest.isoformat())
            start = earliest
        if start >= end:
            return []
        out: list[Candle] = []
        for frm, to in history_windows(start, end, DAILY_CHUNK if daily else HISTORY_CHUNK):
            req = {"symbol": instrument.token, "resolution": RESOLUTION[timeframe], "date_format": "0",
                   "range_from": str(frm), "range_to": str(to),
                   # Futures: continuous series across rollovers.
                   "cont_flag": "1" if instrument.instrument_type == InstrumentType.FUT else "0",
                   "oi_flag": "1" if instrument.instrument_type in (InstrumentType.FUT, InstrumentType.OPT) else "0"}
            resp = await asyncio.to_thread(self.rest().history, req)
            out += parse_history(resp, instrument.token, timeframe)
            await asyncio.sleep(HISTORY_PAUSE_S)
        dedup = {c.ts: c for c in out if start <= c.ts < end}
        return [dedup[k] for k in sorted(dedup)]
