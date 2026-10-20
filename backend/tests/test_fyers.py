"""Fyers adapter tests. Fixtures mirror the real symbol master / SDK message formats (Oct 2026)."""

import asyncio
import base64
import json
import threading
from datetime import date, datetime, timedelta, timezone

import pytest

from trader.brokers.fyers import auth, expired
from trader.brokers.fyers.provider import (
    FyersMarketDataProvider,
    history_windows,
    parse_history,
    parse_quotes,
    parse_socket_message,
)
from trader.brokers.fyers.symbols import build_instruments, fetch_master, filter_master
from trader.config import Settings
from trader.domain import InstrumentType
from trader.instruments.calendar import IST
from trader.instruments.strikes import OptionType
from trader.market_data.service import MarketDataService

NOW = datetime(2026, 10, 21, 10, 30, tzinfo=IST).astimezone(timezone.utc)
EXP_27OCT = "1793095800"  # 2026-10-27 15:40 IST
EXP_03NOV = "1793700600"


def settings(**kw):
    base = dict(_env_file=None, fyers_client_id="ABCD1234-100", fyers_secret_key="s3cret",
                fyers_max_symbols=20, fyers_strikes_each_side=3, fyers_expiries_per_underlying=1)
    return Settings(**{**base, **kw})


def fake_jwt(exp: datetime) -> str:
    def enc(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': 'HS256'})}.{enc({'exp': int(exp.timestamp())})}.sig"


def opt_row(und, k, ot, exp=EXP_27OCT, lot=65, prev_oi=1000.0, ticker=None):
    return {"symTicker": ticker or f"NSE:{und}26OCT{k}{ot}", "exSymName": f"{und}26OCT{k}{ot}", "exInstType": 14,
            "underSym": und, "expiryDate": exp, "optType": ot, "strikePrice": float(k), "minLotSize": lot,
            "tickSize": 0.05, "previousOi": prev_oi, "previousClose": 10.0}


def master_rows():
    rows = [{"symTicker": "NSE:BANKNIFTY26OCTFUT", "exSymName": "BANKNIFTY26OCTFUT", "exInstType": 11,
             "underSym": "BANKNIFTY", "expiryDate": EXP_27OCT, "optType": "XX", "strikePrice": -1.0,
             "minLotSize": 30, "tickSize": 0.2, "previousOi": 0, "previousClose": 0},
            {"symTicker": "NSE:NIFTY26OCTFUT", "exSymName": "NIFTY26OCTFUT", "exInstType": 11,
             "underSym": "NIFTY", "expiryDate": EXP_27OCT, "optType": "XX", "strikePrice": -1.0,
             "minLotSize": 65, "tickSize": 0.1, "previousOi": 0, "previousClose": 0}]
    for k in range(22700, 23101, 50):
        for ot in ("CE", "PE"):
            rows.append(opt_row("NIFTY", k, ot))
            rows.append(opt_row("NIFTY", k, ot, exp=EXP_03NOV, ticker=f"NSE:NIFTY26N03{k}{ot}"))
    for k in range(51500, 52501, 100):
        for ot in ("CE", "PE"):
            rows.append(opt_row("BANKNIFTY", k, ot, lot=30))
    rows.append(opt_row("NIFTY", 22000, "CE", exp="1792491000"))  # 2026-10-20: expired by NOW
    return rows


# ---- auth ----------------------------------------------------------------------------
def test_token_cache_roundtrip_and_expiry(tmp_path):
    p = tmp_path / "tok.json"
    good = fake_jwt(NOW + timedelta(hours=10))
    auth.save_token(p, "ABCD1234-100", good)
    assert auth.load_token(p, "ABCD1234-100", now=NOW) == good
    assert auth.load_token(p, "OTHER-100", now=NOW) is None  # different app
    assert auth.load_token(p, "ABCD1234-100", now=NOW + timedelta(hours=10)) is None  # expired
    assert auth.load_token(tmp_path / "missing.json", "ABCD1234-100") is None


def test_login_url_and_callback():
    url, state = auth.login_url(settings())
    assert "client_id=ABCD1234-100" in url and f"state={state}" in url and "response_type=code" in url
    q = auth.parse_callback(f"http://127.0.0.1:8765/fyers/callback?s=ok&code=200&auth_code=XYZ&state={state}")
    assert q["auth_code"] == "XYZ" and q["state"] == state


def test_exchange_auth_code():
    class Resp:
        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    sent = {}

    def ok_post(url, json, timeout):
        sent.update(url=url, body=json)
        return Resp({"s": "ok", "access_token": "TOKEN"})

    s = settings()
    assert auth.exchange_auth_code(s, "CODE", post=ok_post) == "TOKEN"
    assert sent["url"].endswith("/validate-authcode")
    assert sent["body"]["appIdHash"] == auth.app_id_hash("ABCD1234-100", "s3cret")
    with pytest.raises(auth.FyersAuthError):
        auth.exchange_auth_code(s, "CODE", post=lambda *a, **k: Resp({"s": "error", "message": "bad code"}))


# ---- symbol master ----------------------------------------------------------------------
def test_filter_and_build_instruments():
    master = {r["symTicker"]: r for r in master_rows()}
    master["NSE:FINNIFTY26OCTFUT"] = {**master_rows()[0], "underSym": "FINNIFTY", "symTicker": "NSE:FINNIFTY26OCTFUT"}
    rows = filter_master(master)
    assert all(r["underSym"] in ("NIFTY", "BANKNIFTY") for r in rows)
    insts, prev_oi = build_instruments(rows, NOW.astimezone(IST).date())
    by = {i.token: i for i in insts}
    assert by["NSE:NIFTY50-INDEX"].instrument_type == InstrumentType.INDEX and by["NSE:NIFTY50-INDEX"].symbol == "NIFTY"
    fut = by["NSE:BANKNIFTY26OCTFUT"]
    assert fut.tick_size == 0.2 and fut.lot_size == 30 and fut.expiry == date(2026, 10, 27)  # from master, not specs
    ce = by["NSE:NIFTY26OCT22900CE"]
    assert ce.strike == 22900 and ce.option_type == OptionType.CE and ce.lot_size == 65
    assert "NSE:NIFTY26OCT22000CE" not in by  # expired contract dropped
    assert prev_oi["NSE:NIFTY26OCT22900CE"] == 1000


def test_fetch_master_caches_daily(tmp_path):
    calls = []

    def fake_download(url, dest):
        calls.append(url)
        dest.write_text(json.dumps({r["symTicker"]: r for r in master_rows()}), encoding="utf-8")

    d = date(2026, 10, 21)
    rows = fetch_master(tmp_path, d, download=fake_download)
    again = fetch_master(tmp_path, d, download=fake_download)
    assert rows == again and len(calls) == 1
    assert not list(tmp_path.glob("*.part"))


# ---- parsers ---------------------------------------------------------------------------------
def test_parse_socket_messages():
    feed = 1792557000
    sf = {"type": "sf", "symbol": "NSE:NIFTY26OCT22900CE", "ltp": 120.5, "vol_traded_today": 1500,
          "exch_feed_time": feed, "bid_price": 120.4, "ask_price": 120.6, "OI": 1300, "open_price": 100,
          "high_price": 130, "low_price": 95, "prev_close_price": 110}
    t = parse_socket_message(sf, {"NSE:NIFTY26OCT22900CE": 1000}, NOW)
    assert t.ltp == 120.5 and t.bid == 120.4 and t.oi == 1300 and t.oi_change == 300 and t.volume == 1500
    assert t.ts == datetime.fromtimestamp(feed, tz=timezone.utc)
    idx = {"type": "if", "symbol": "NSE:NIFTY50-INDEX", "ltp": 22950.5, "prev_close_price": 22900,
           "exch_feed_time": feed, "high_price": 23000, "low_price": 22880, "open_price": 22910}
    ti = parse_socket_message(idx, {}, NOW)
    assert ti.ltp == 22950.5 and ti.prev_close == 22900 and ti.bid is None and ti.volume == 0
    assert parse_socket_message({"type": "sub", "code": 200}, {}, NOW) is None
    assert parse_socket_message({**sf, "ltp": 0}, {}, NOW) is None
    assert parse_socket_message({**sf, "bid_price": 0}, {}, NOW).bid is None


def test_parse_quotes():
    resp = {"s": "ok", "d": [
        {"n": "NSE:NIFTY50-INDEX", "s": "ok", "v": {"lp": 22950, "open_price": 22910, "high_price": 23000,
                                                     "low_price": 22880, "prev_close_price": 22900, "tt": "1792557000"}},
        {"n": "NSE:NIFTY26OCTFUT", "s": "ok", "v": {"lp": 23010, "bid": 23009.9, "ask": 23010.1, "volume": 12345,
                                                     "prev_close_price": 22950, "tt": 1792557000}},
        {"n": "NSE:BAD", "s": "error", "v": {}},
    ]}
    ticks = parse_quotes(resp, {}, NOW)
    assert [t.token for t in ticks] == ["NSE:NIFTY50-INDEX", "NSE:NIFTY26OCTFUT"]
    assert ticks[0].bid is None and ticks[1].bid == 23009.9 and ticks[1].volume == 12345


def test_parse_history_and_windows():
    resp = {"s": "ok", "candles": [[1792557000, 1, 2, 0.5, 1.5, 100, 5000], [1792557060, None, None, None, None, 0]]}
    c = parse_history(resp, "NSE:NIFTY26OCTFUT", "1m")
    assert len(c) == 1 and c[0].oi == 5000 and c[0].closed  # null bar skipped, not fabricated
    assert parse_history({"s": "no_data"}, "x", "1m") == []
    with pytest.raises(RuntimeError):
        parse_history({"s": "error", "message": "bad"}, "x", "1m")
    w = history_windows(NOW - timedelta(days=200), NOW)
    assert len(w) == 3 and w[0][0] == int((NOW - timedelta(days=200)).timestamp()) and w[-1][1] == int(NOW.timestamp())


# ---- expired contracts -------------------------------------------------------------------------
def test_expired_symbols_roundtrip():
    assert expired.build_symbol("NIFTY", date(2025, 3, 27), 23000, OptionType.CE, True) == "NSE:NIFTY25MAR23000CE"
    assert expired.build_symbol("NIFTY", date(2026, 11, 3), 17350, OptionType.PE, False) == "NSE:NIFTY26N0317350PE"
    assert expired.build_symbol("NIFTY", date(2025, 4, 9), 22500, OptionType.CE, False) == "NSE:NIFTY2540922500CE"
    p = expired.parse_symbol("NSE:NIFTY26N0317350PE")
    assert (p.underlying, p.strike, p.option_type, p.weekly_expiry) == ("NIFTY", 17350, OptionType.PE, date(2026, 11, 3))
    m = expired.parse_symbol("BANKNIFTY25MAR48000CE")
    assert m.underlying == "BANKNIFTY" and m.weekly_expiry is None
    inst = expired.expired_instrument("NSE:NIFTY25MAR23000CE", date(2025, 3, 27))
    assert inst.expiry == date(2025, 3, 27) and inst.strike == 23000
    with pytest.raises(ValueError):
        expired.expired_instrument("NSE:NIFTY26N0317350PE", date(2026, 11, 4))


def test_expired_response_by_column_name():
    resp = {"s": "ok", "columns": ["timestamp", "open", "high", "low", "close", "volume", "open_interest", "iv"],
            "candles": [[1742960700, 730, 750, 645, 706, 359100, 5131225, 17.84]]}
    c = expired.parse_response(resp, "NSE:NIFTY25MAR23000CE", "5m")
    assert c[0].close == 706 and c[0].oi == 5131225
    assert expired.parse_response({"s": "no_data", "candles": []}, "x", "5m") == []


def test_daily_quota(tmp_path):
    q = expired.DailyQuota(tmp_path / "q.json", 2, now_fn=lambda: NOW)
    assert q.remaining() == 2
    q.consume(); q.consume()
    assert q.remaining() == 0
    tomorrow = expired.DailyQuota(tmp_path / "q.json", 2, now_fn=lambda: NOW + timedelta(days=1))
    assert tomorrow.remaining() == 2
    assert expired.DailyQuota(tmp_path / "q.json", 0).remaining() is None  # Prime: unlimited


# ---- provider with a fake SDK socket ----------------------------------------------------------------
class FakeSocket:
    instances: list["FakeSocket"] = []

    def __init__(self, **kw):
        self.kw = kw
        self.subscribed: set[str] = set()
        self.closed = False
        FakeSocket.instances.append(self)

    def connect(self):
        self.kw["on_connect"]()

    def is_connected(self):
        return True

    def subscribe(self, symbols, data_type):
        assert data_type == "SymbolUpdate"
        self.subscribed |= set(symbols)

    def unsubscribe(self, symbols, data_type):
        self.subscribed -= set(symbols)

    def close_connection(self):
        self.closed = True

    def emit(self, msg):  # called from a non-asyncio thread, like the SDK does
        threading.Thread(target=self.kw["on_message"], args=(msg,)).start()

    def drop(self):
        threading.Thread(target=self.kw["on_close"], args=({"code": 1006},)).start()


class FakeRest:
    def quotes(self, data):
        out = []
        for sym in data["symbols"].split(","):
            lp = {"NSE:NIFTY50-INDEX": 22912.0, "NSE:NIFTYBANK-INDEX": 51980.0, "NSE:INDIAVIX-INDEX": 12.5}.get(sym, 100.0)
            out.append({"n": sym, "s": "ok", "v": {"lp": lp, "prev_close_price": lp, "tt": int(NOW.timestamp())}})
        return {"s": "ok", "d": out}

    def history(self, data):
        start = int(data["range_from"])
        return {"s": "ok", "candles": [[start + 60 * i, 1, 2, 0.5, 1.5, 10] for i in range(3)]}


def make_provider(**kw):
    FakeSocket.instances.clear()
    p = FyersMarketDataProvider(
        settings(**kw), token_provider=lambda: "TOKEN", socket_factory=FakeSocket,
        rest_factory=lambda *a: FakeRest(), master_loader=master_rows, now_fn=lambda: NOW)
    p._policy.initial_delay = 0.01
    return p


async def test_provider_requires_token():
    p = FyersMarketDataProvider(settings(), token_provider=lambda: None, socket_factory=FakeSocket,
                                master_loader=master_rows, now_fn=lambda: NOW)
    with pytest.raises(auth.FyersAuthError, match="login"):
        await p.connect()


async def test_service_subscribes_atm_window_within_symbol_cap():
    p = make_provider()
    md = MarketDataService(p, None, p.settings, now_fn=lambda: NOW)
    await md.start()
    try:
        tokens = set(p.subscribed)
        assert len(tokens) == 20  # capped at FYERS_MAX_SYMBOLS
        assert {"NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX", "NSE:INDIAVIX-INDEX",
                "NSE:NIFTY26OCTFUT", "NSE:BANKNIFTY26OCTFUT"} <= tokens
        # ATM for 22912 is 22900; nearest strikes are subscribed first, only the nearest expiry.
        assert {"NSE:NIFTY26OCT22900CE", "NSE:NIFTY26OCT22900PE", "NSE:BANKNIFTY26OCT52000CE"} <= tokens
        assert not any("N03" in t for t in tokens)
        assert md.quote("NIFTY").ltp == 22912.0  # seeded from REST before any tick
    finally:
        await md.stop()


async def test_stream_bridges_threads_and_reconnects():
    p = make_provider()
    await p.discover_instruments()
    await p.subscribe(["NSE:NIFTY26OCT22900CE"])
    got = []

    async def consume():
        async for t in p.stream():
            got.append(t)
            sock = FakeSocket.instances[-1]
            if len(got) == 1:
                sock.drop()  # connection lost -> base class reconnects and re-subscribes
            if len(got) == 2:
                return

    async def produce():
        while not FakeSocket.instances:
            await asyncio.sleep(0.01)
        FakeSocket.instances[-1].emit({"type": "sf", "symbol": "NSE:NIFTY26OCT22900CE", "ltp": 120,
                                       "exch_feed_time": int(NOW.timestamp()), "OI": 1100})
        while len(FakeSocket.instances) < 2:
            await asyncio.sleep(0.01)
        new = FakeSocket.instances[-1]
        while "NSE:NIFTY26OCT22900CE" not in new.subscribed:  # re-subscribed after reconnect
            await asyncio.sleep(0.01)
        new.emit({"type": "sf", "symbol": "NSE:NIFTY26OCT22900CE", "ltp": 121,
                  "exch_feed_time": int(NOW.timestamp()) + 1, "OI": 1150})

    await asyncio.wait_for(asyncio.gather(consume(), produce()), timeout=5)
    assert [t.ltp for t in got] == [120, 121]
    assert got[0].oi_change == 100  # previousOi 1000 from the master
    assert p.health.reconnects == 1
    await p.disconnect()


async def test_auth_error_stops_stream_instead_of_spinning():
    p = make_provider()
    await p.discover_instruments()
    await p.subscribe(["NSE:NIFTY50-INDEX"])

    async def consume():
        async for _ in p.stream():
            pass

    task = asyncio.create_task(consume())
    while not FakeSocket.instances:
        await asyncio.sleep(0.01)
    FakeSocket.instances[-1].kw["on_error"]({"code": -99, "message": "Token is expired"})
    with pytest.raises(auth.FyersAuthError):
        await asyncio.wait_for(task, timeout=5)


async def test_history_chunks_and_dedups():
    p = make_provider()
    insts = {i.token: i for i in await p.discover_instruments()}
    candles = await p.history(insts["NSE:NIFTY26OCTFUT"], "1m", NOW - timedelta(days=1), NOW)
    assert len(candles) == 3 and candles[0].ts < candles[-1].ts


async def test_history_clamps_to_plan_depth():
    p = make_provider()
    seen = []
    rest = FakeRest()
    orig = rest.history
    rest.history = lambda data: (seen.append(int(data["range_from"])), orig(data))[1]
    p._rest = rest
    insts = {i.token: i for i in await p.discover_instruments()}
    await p.history(insts["NSE:NIFTY50-INDEX"], "1m", NOW - timedelta(days=400), NOW)
    assert min(seen) >= int((NOW - timedelta(days=p.settings.fyers_intraday_history_days)).timestamp())
