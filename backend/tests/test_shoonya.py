"""Shoonya (Finvasia): sign-in, symbol master, websocket frames, the provider, and the customer's own feed."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.brokers.shoonya import auth
from trader.config import Settings
from trader.market_data import shoonya as sh
from trader.market_data.base import ProviderAuthError

MASTER = """Exchange,Token,LotSize,Symbol,TradingSymbol,Expiry,Instrument,OptionType,StrikePrice,TickSize,
NFO,48577,,,,,,,0,0,
NFO,61471,65,NIFTY,NIFTY23NOV26F,23-NOV-2026,FUTIDX,XX,-0.01,0.1,
NFO,89001,65,NIFTY,NIFTY29OCT26C25000,29-OCT-2026,OPTIDX,CE,25000,0.05,
NFO,89002,65,NIFTY,NIFTY29OCT26P25000,29-OCT-2026,OPTIDX,PE,25000,0.05,
NFO,89003,65,NIFTY,NIFTY01OCT26C25000,01-OCT-2026,OPTIDX,CE,25000,0.05,
NFO,70001,30,BANKNIFTY,BANKNIFTY23NOV26F,23-NOV-2026,FUTIDX,XX,-0.01,0.2,
NFO,70002,500,RELIANCE,RELIANCE29OCT26C1300,29-OCT-2026,OPTSTK,CE,1300,0.05,
"""
TODAY = date(2026, 10, 22)


def test_login_payload_hashes_the_password_and_app_key():
    p = auth.login_payload("FA1", "secret", "123456", "FA1_U", "KEY", "dev")
    assert p["pwd"] == auth.sha256("secret") and p["appkey"] == auth.sha256("FA1|KEY")
    assert p["uid"] == "FA1" and p["factor2"] == "123456" and p["vc"] == "FA1_U" and p["source"] == "API"
    assert "secret" not in json.dumps(p) and "KEY" not in json.dumps(p)
    assert auth.encode({"a": 1}, "tok") == b'jData={"a":1}&jKey=tok'


def test_sign_in_returns_a_session_and_never_the_password():
    seen = {}

    def http(path, payload, token=None):
        seen.update(path=path, payload=payload)
        return {"stat": "Ok", "susertoken": "TOKEN", "uid": "FA1", "uname": "Omkar"}

    s = auth.sign_in("FA1", "pw", "123456", "FA1_U", "KEY", "dev", http=http)
    assert s["token"] == "TOKEN" and s["name"] == "Omkar" and datetime.fromisoformat(s["expires_at"]) > datetime.now(timezone.utc)
    assert seen["path"] == "/QuickAuth" and "pw" not in json.dumps(s)


def test_sign_in_failure_is_a_clear_auth_error():
    with pytest.raises(auth.ShoonyaAuthError, match="6-digit code"):
        auth.sign_in("FA1", "pw", "000000", "v", "k", "d", http=lambda *a, **k: {"stat": "Not_Ok", "emsg": "Invalid Input : Check Password"})
    with pytest.raises(auth.ShoonyaAuthError):
        auth.sign_in("FA1", "pw", "000000", "v", "k", "d", http=lambda *a, **k: None)


def test_session_expiry_is_the_next_six_am_ist():
    ist = timezone(timedelta(hours=5, minutes=30))
    morning = datetime(2026, 10, 22, 4, 0, tzinfo=ist).astimezone(timezone.utc)
    evening = datetime(2026, 10, 22, 18, 0, tzinfo=ist).astimezone(timezone.utc)
    assert auth.session_expiry(morning).astimezone(ist) == datetime(2026, 10, 22, 6, 0, tzinfo=ist)
    assert auth.session_expiry(evening).astimezone(ist) == datetime(2026, 10, 23, 6, 0, tzinfo=ist)


def test_check_separates_expired_sessions_from_other_refusals():
    with pytest.raises(auth.ShoonyaAuthError, match="expired"):
        auth.check({"stat": "Not_Ok", "emsg": "Session Expired : Invalid Session Key"}, "x")
    with pytest.raises(ConnectionError):
        auth.check({"stat": "Not_Ok", "emsg": "Too many requests"}, "x")
    assert auth.check({"stat": "Ok"}, "x") == {"stat": "Ok"}


def test_master_gives_indices_futures_and_live_options_only():
    out = sh.build_instruments(MASTER, TODAY)
    by = {i.token: i for i in out}
    assert by["NSE|26000"].symbol == "NIFTY" and by["NSE|26017"].symbol == "INDIAVIX"
    assert by["NFO|61471"].instrument_type.value == "FUT" and by["NFO|61471"].lot_size == 65
    ce = by["NFO|89001"]
    assert ce.is_option and ce.strike == 25000 and ce.option_type.value == "CE" and ce.expiry == date(2026, 10, 29) and ce.underlying == "NIFTY"
    assert "NFO|89003" not in by  # expired
    assert "NFO|70002" not in by  # a stock option, not an index option
    assert "NFO|48577" not in by


def test_websocket_frames_merge_and_become_ticks():
    state: dict = {}
    now = datetime(2026, 10, 22, 9, 30, tzinfo=timezone.utc)
    key, q = sh.merge_frame(state, {"t": "tk", "e": "NSE", "tk": "26000", "lp": "25000.5", "c": "24900", "o": "24950", "h": "25010", "l": "24940", "v": "100"})
    t = sh.to_tick(key, q, now)
    assert key == "NSE|26000" and t.ltp == 25000.5 and t.prev_close == 24900 and t.volume == 100
    _, q = sh.merge_frame(state, {"t": "tf", "e": "NSE", "tk": "26000", "lp": "25001.0"})  # only the changed field
    t = sh.to_tick("NSE|26000", q, now)
    assert t.ltp == 25001.0 and t.prev_close == 24900 and t.open == 24950
    assert sh.merge_frame(state, {"t": "ck", "s": "OK"}) is None
    assert sh.to_tick("x", {"lp": "0"}, now) is None and sh.to_tick("x", {}, now) is None


def test_chart_rows_parse_newest_first_into_ordered_candles():
    rows = [{"stat": "Ok", "time": "22-10-2026 09:16:00", "into": "2", "inth": "3", "intl": "1", "intc": "2.5", "v": "10", "oi": "0"},
            {"stat": "Ok", "time": "22-10-2026 09:15:00", "into": "1", "inth": "2", "intl": "1", "intc": "2", "v": "5"},
            {"time": "bad"}]
    cs = sh.parse_series(rows, "NSE|26000", "1m")
    assert [c.close for c in cs] == [2.0, 2.5] and cs[0].ts == datetime(2026, 10, 22, 3, 45, tzinfo=timezone.utc)
    assert sh.parse_series({"stat": "Not_Ok"}, "t", "1m") == []


class FakeWS:
    def __init__(self, ack, frames):
        self.sent, self.ack, self.frames, self.closed = [], ack, frames, False

    async def send(self, text):
        self.sent.append(json.loads(text))

    async def recv(self):
        return json.dumps(self.ack)

    def __aiter__(self):
        async def gen():
            for f in self.frames:
                yield json.dumps(f)
        return gen()

    async def close(self):
        self.closed = True


def provider(ws, token="TOK"):
    async def connect(url):
        return ws

    return sh.ShoonyaMarketDataProvider(SimpleNamespace(shoonya_user_id="FA1"), token_provider=lambda: token, ws_connect=connect,
                                        master_loader=lambda: MASTER, now_fn=lambda: datetime(2026, 10, 22, 4, tzinfo=timezone.utc))


async def test_provider_connects_subscribes_and_streams_ticks():
    sh._MASTER_CACHE.clear()
    ws = FakeWS({"t": "ck", "s": "OK"}, [{"t": "tk", "e": "NSE", "tk": "26000", "lp": "25000"}, {"t": "tf", "e": "NSE", "tk": "26000", "lp": "25010"}])
    p = provider(ws)
    insts = await p.discover_instruments()
    assert len(insts) == 3 + 1 + 2 + 1  # indices, NIFTY fut, two NIFTY options, BANKNIFTY fut
    await p.connect()
    assert ws.sent[0] == {"t": "c", "uid": "FA1", "actid": "FA1", "susertoken": "TOK", "source": "API"}
    await p.subscribe(["NSE|26000", "NSE|26009"])
    assert ws.sent[-1] == {"t": "t", "k": "NSE|26000#NSE|26009"}
    got = []
    with pytest.raises(ConnectionError):  # the fake socket ends: that reads as a dropped feed, which reconnects
        async for tick in p._stream():
            got.append((tick.token, tick.ltp))
    assert got == [("NSE|26000", 25000.0), ("NSE|26000", 25010.0)]
    await p.disconnect()
    assert ws.closed


async def test_provider_rejects_a_bad_session_as_an_auth_error_and_needs_a_token():
    bad = provider(FakeWS({"t": "ck", "s": "Not_Ok"}, []))
    with pytest.raises(ProviderAuthError, match="Sign in again"):
        await bad.connect()
    with pytest.raises(ProviderAuthError):
        await provider(FakeWS({"t": "ck", "s": "OK"}, []), token=None).connect()


async def test_snapshot_and_history_use_the_session_token():
    calls = []

    def http(path, payload, token=None):
        calls.append((path, token, payload))
        if path == "/GetQuotes":
            return {"stat": "Ok", "lp": "25000", "c": "24900"}
        return [{"time": "22-10-2026 09:15:00", "into": "1", "inth": "2", "intl": "1", "intc": "2", "v": "5"}]

    p = sh.ShoonyaMarketDataProvider(SimpleNamespace(shoonya_user_id="FA1"), token_provider=lambda: "TOK", http=http)
    idx = sh.build_instruments(MASTER, TODAY)[0]
    ticks = await p.snapshot([idx])
    assert ticks[0].ltp == 25000 and calls[0][1] == "TOK" and calls[0][2] == {"uid": "FA1", "exch": "NSE", "token": "26000"}
    start = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc)
    cs = await p.history(idx, "1m", start, start + timedelta(hours=2))
    assert len(cs) == 1 and calls[-1][0] == "/TPSeries" and calls[-1][2]["intrv"] == "1"
    assert await p.history(idx, "2m", start, start + timedelta(hours=2)) == []  # an interval Shoonya does not offer


# ---- the customer's own connection ------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    s = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 's.db').as_posix()}", sim_seed=3,
                 sim_backfill_minutes=0, auth_enabled=True, credential_key="k" * 43 + "=", signup_open=True, signup_invite_code="")
    from trader.api.routes import brokers as route

    monkeypatch.setattr(route.shoonya_auth, "sign_in", lambda uid, pw, totp, vc, key, imei, http=None: (
        {"token": "SESSION", "user_id": uid, "name": "Omkar", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()}
        if (pw, totp) == ("good", "123456") else (_ for _ in ()).throw(auth.ShoonyaAuthError("Shoonya refused the sign-in: nope."))))
    with TestClient(create_app(s)) as c:
        yield c


def signup(c, email="a@example.com"):
    r = c.post("/auth/signup", json={"email": email, "password": "correct horse battery", "name": "A"})
    assert r.status_code == 200, r.text


KEYS = {"user_id": "FA1", "api_key": "APIKEY123", "vendor_code": "FA1_U", "imei": "striktek"}


def test_sign_in_stores_only_the_session_and_lists_it_as_signed_in(client):
    signup(client)
    assert client.post("/brokers/connections", json={"broker": "shoonya", "values": KEYS}).status_code == 200
    conns = client.get("/brokers/connections").json()
    assert conns[0]["session"]["state"] == "none" and "APIKEY123" not in json.dumps(conns)
    bad = client.post("/brokers/shoonya/login", json={"password": "wrong", "totp": "000000"})
    assert bad.status_code == 400 and "nope" in bad.json()["detail"]
    ok = client.post("/brokers/shoonya/login", json={"password": "good", "totp": "123456"})
    assert ok.status_code == 200 and ok.json()["name"] == "Omkar"
    assert "good" not in ok.text and "SESSION" not in ok.text  # neither the password nor the session token goes back out
    conns = client.get("/brokers/connections").json()
    assert conns[0]["session"]["state"] == "signed_in"
    # the vault holds the session token but no password
    from trader.accounts import vault
    from trader.db.models import BrokerConnectionRow

    async def blob():
        async with client.app.state.sessionmaker() as s:
            from sqlalchemy import select

            row = (await s.execute(select(BrokerConnectionRow))).scalars().first()
            return vault.unseal(row.secret_blob, client.app.state.settings.credential_key)

    keys = asyncio.run(blob())
    assert keys["access_token"] == "SESSION" and "good" not in json.dumps(keys) and "password" not in keys


def test_five_bad_sign_ins_are_a_pause_not_an_account_lock(client):
    signup(client, "b@example.com")
    client.post("/brokers/connections", json={"broker": "shoonya", "values": KEYS})
    codes = [client.post("/brokers/shoonya/login", json={"password": "wrong", "totp": "000000"}).status_code for _ in range(6)]
    assert codes == [400] * 5 + [429]


def test_catalog_lists_shoonya_as_supported_with_a_password_sign_in(client):
    signup(client, "c@example.com")
    b = next(x for x in client.get("/brokers/catalog").json()["brokers"] if x["id"] == "shoonya")
    assert b["status"] == "available" and b["login_kind"] == "password" and {f["key"] for f in b["fields"]} == set(KEYS)


async def test_the_feed_manager_runs_a_customer_on_the_broker_they_are_signed_in_to():
    from trader.market_data import feeds

    far, soon = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat(), (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert feeds._keys_expired("shoonya", {"access_token": "t", "expires_at": far}) is False
    assert feeds._keys_expired("shoonya", {"access_token": "t", "expires_at": soon}) is True
    assert feeds._keys_expired("shoonya", {"access_token": "t"}) is True and feeds._keys_expired("shoonya", {}) is True
    assert feeds._feed_expired("shoonya", {"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}) is False


def test_totp_matches_the_rfc_6238_reference_vectors():
    from trader.brokers.shoonya.login import totp_now

    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of the RFC's ASCII key "12345678901234567890"
    assert totp_now(secret, at=59, digits=8) == "94287082" and totp_now(secret, at=1111111109, digits=8) == "07081804"
    assert totp_now("gezd gnbv gy3t qojq gezd gnbv gy3t qojq", at=59) == "287082"  # spaces and case do not matter


def test_the_saved_session_is_used_only_for_the_same_user_and_before_it_expires(tmp_path):
    from trader.brokers.shoonya.login import load_token, save_token

    p = tmp_path / "t.json"
    assert load_token(p, "FA1") is None
    save_token(p, "FA1", "TOK", (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat())
    assert load_token(p, "FA1") == "TOK" and load_token(p, "FA2") is None
    save_token(p, "FA1", "TOK", (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat())
    assert load_token(p, "FA1") is None


def test_a_refusal_sent_as_http_400_is_read_as_shoonyas_message_not_a_network_error(monkeypatch):
    import io
    import urllib.error

    def refuse(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"stat":"Not_Ok","emsg":"Invalid Input :  Invalid Vendor code"}'))

    monkeypatch.setattr(auth.urllib.request, "urlopen", refuse)
    with pytest.raises(auth.ShoonyaAuthError, match="Invalid Vendor code"):
        auth.sign_in("FA1", "pw", "123456", "bad", "k", "d")
    assert auth.HOST.endswith("/NorenWClientAPI")

    def gateway(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, io.BytesIO(b"<html>502</html>"))

    monkeypatch.setattr(auth.urllib.request, "urlopen", gateway)
    with pytest.raises(ConnectionError, match="502"):
        auth.post("/QuickAuth", {})


async def test_the_socket_falls_back_to_the_next_address_when_the_first_does_not_answer():
    urls = []
    ws = FakeWS({"t": "ck", "s": "OK"}, [])

    async def connect(url):
        urls.append(url)
        if len(urls) == 1:
            raise OSError("refused")
        return ws

    p = sh.ShoonyaMarketDataProvider(SimpleNamespace(shoonya_user_id="FA1"), token_provider=lambda: "TOK", ws_connect=connect)
    await p.connect()
    assert urls == list(auth.WS_URLS) and ws.sent[0]["t"] == "c"
