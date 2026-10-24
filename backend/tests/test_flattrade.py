"""Flattrade: the customer signs in on Flattrade's page, we exchange the code; live data reuses the NorenAPI provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.brokers.flattrade import auth as fl
from trader.config import Settings
from trader.market_data import flattrade as ft
from trader.market_data import shoonya as sh

MASTER = """Exchange,Token,Lotsize,Symbol,Tradingsymbol,Instrument,Expiry,Strike,Optiontype
NFO,49570,30,BANKNIFTY,BANKNIFTY27OCT26P59900,OPTIDX,27-OCT-2026,59900.00,PE
NFO,48699,30,BANKNIFTY,BANKNIFTY27OCT26F,FUTIDX,27-OCT-2026,-0.01,XX
NFO,89001,65,NIFTY,NIFTY29OCT26C25000,OPTIDX,29-OCT-2026,25000.00,CE
NFO,89003,65,NIFTY,NIFTY01OCT26C25000,OPTIDX,01-OCT-2026,25000.00,CE
"""


def test_login_url_and_the_request_hash():
    assert fl.login_url("KEY1") == "https://auth.flattrade.in/?app_key=KEY1"
    assert fl.request_hash("K", "CODE", "SECRET") == hashlib.sha256(b"KCODESECRET").hexdigest()


def test_exchange_code_returns_the_session_and_never_the_secret():
    seen = {}

    def http(url, body):
        seen.update(url=url, body=body)
        return {"stat": "Ok", "token": "TOKEN", "client": "FZ123"}

    s = fl.exchange_code("K", "SECRET", "CODE", http=http)
    assert s["token"] == "TOKEN" and s["client_id"] == "FZ123" and datetime.fromisoformat(s["expires_at"]) > datetime.now(timezone.utc)
    assert seen["url"] == fl.TOKEN_URL and seen["body"] == {"api_key": "K", "request_code": "CODE", "api_secret": fl.request_hash("K", "CODE", "SECRET")}
    assert "SECRET" not in json.dumps(seen["body"])


def test_exchange_failure_is_a_clear_error():
    with pytest.raises(fl.FlattradeAuthError, match="start again"):
        fl.exchange_code("K", "S", "C", http=lambda u, b: {"token": "", "client": "", "stat": "Not_Ok", "emsg": ""})
    with pytest.raises(fl.FlattradeAuthError, match="bad code"):
        fl.exchange_code("K", "S", "C", http=lambda u, b: {"stat": "Not_Ok", "emsg": "bad code"})


def test_the_index_derivatives_master_maps_onto_the_shared_builder():
    by = {i.token: i for i in ft.build_instruments(MASTER, date(2026, 10, 22))}
    assert by["NSE|26000"].symbol == "NIFTY" and by["NFO|48699"].instrument_type.value == "FUT"
    pe = by["NFO|49570"]
    assert pe.is_option and pe.strike == 59900 and pe.option_type.value == "PE" and pe.lot_size == 30 and pe.underlying == "BANKNIFTY"
    assert "NFO|89003" not in by  # expired


async def test_the_provider_uses_the_clients_id_and_flattrades_own_addresses(monkeypatch):
    sh._MASTER_CACHE.clear()
    calls = []

    def http(path, payload, token=None):
        calls.append((path, token, payload))
        return {"stat": "Ok", "lp": "25000", "c": "24900"}

    urls = []

    class WS:
        sent = []

        async def send(self, t):
            self.sent.append(json.loads(t))

        async def recv(self):
            return json.dumps({"t": "ck", "s": "OK"})

        async def close(self):
            pass

    async def connect(url):
        urls.append(url)
        return WS()

    p = ft.FlattradeMarketDataProvider(SimpleNamespace(flattrade_client_id="FZ123"), token_provider=lambda: "TOK", http=http,
                                       ws_connect=connect, master_loader=lambda: MASTER,
                                       now_fn=lambda: datetime(2026, 10, 22, 4, tzinfo=timezone.utc))
    assert p.name == "flattrade" and p.label == "Flattrade"
    insts = await p.discover_instruments()
    assert len(insts) == 3 + 3  # indices, BANKNIFTY put and future, one live NIFTY call
    await p.connect()
    assert urls == list(fl.WS_URLS) and WS.sent[0]["uid"] == "FZ123" and WS.sent[0]["susertoken"] == "TOK"
    ticks = await p.snapshot([insts[0]])
    assert ticks[0].ltp == 25000 and calls[0][2]["uid"] == "FZ123"
    signed_out = ft.FlattradeMarketDataProvider(SimpleNamespace(flattrade_client_id="FZ123"), token_provider=lambda: None)
    with pytest.raises(sh.ShoonyaAuthError, match="Flattrade"):
        await signed_out.connect()
    sh._MASTER_CACHE.clear()


def test_the_default_http_calls_flattrades_host(monkeypatch):
    seen = {}
    monkeypatch.setattr(ft.auth.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(OSError(req.full_url)))
    with pytest.raises(ConnectionError, match="piconnect.flattrade.in/PiConnectAPI/GetQuotes"):
        ft.post("/GetQuotes", {"uid": "x"}, "t")


# ---- the customer's own sign-in --------------------------------------------------------------------------
@pytest.fixture()
def client(tmp_path, monkeypatch):
    s = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'f.db').as_posix()}", sim_seed=3, sim_backfill_minutes=0,
                 auth_enabled=True, credential_key="k" * 43 + "=", signup_open=True, signup_invite_code="", public_url="https://s.example")
    from trader.api.routes import brokers as route

    def fake_exchange(api_key, api_secret, code, http=None):
        if code != "good":
            raise fl.FlattradeAuthError("Flattrade refused the sign-in: used code")
        return {"token": "SESSION", "client_id": "FZ123", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()}

    monkeypatch.setattr(route.flattrade_auth, "exchange_code", fake_exchange)
    with TestClient(create_app(s)) as c:
        yield c


def signup(c, email="f@example.com"):
    assert c.post("/auth/signup", json={"email": email, "password": "correct horse battery", "name": "F"}).status_code == 200


def test_flattrade_sign_in_flow_stores_only_the_session(client):
    signup(client)
    cat = client.get("/brokers/catalog").json()
    b = next(x for x in cat["brokers"] if x["id"] == "flattrade")
    assert b["status"] == "available" and cat["flattrade_redirect_uri"] == "https://s.example/api/brokers/flattrade/callback"
    assert client.post("/brokers/flattrade/login-url").status_code == 404  # keys first
    client.post("/brokers/connections", json={"broker": "flattrade", "values": {"api_key": "KEY1", "api_secret": "SEC"}})
    assert client.get("/brokers/connections").json()[0]["session"]["state"] == "none"
    u = client.post("/brokers/flattrade/login-url").json()
    assert u["url"] == "https://auth.flattrade.in/?app_key=KEY1" and u["redirect_uri"].endswith("/flattrade/callback")
    bad = client.get("/brokers/flattrade/callback", params={"code": "used"}, follow_redirects=False)
    assert bad.status_code == 400 and "refused" in bad.text
    assert client.get("/brokers/flattrade/callback", follow_redirects=False).status_code == 400  # no code
    ok = client.get("/brokers/flattrade/callback", params={"code": "good"}, follow_redirects=False)
    assert ok.status_code == 303 and ok.headers["location"] == "/app/#/brokers"
    c = client.get("/brokers/connections").json()[0]
    assert c["session"]["state"] == "signed_in" and "SEC" not in json.dumps(c) and "SESSION" not in json.dumps(c)


def test_a_flattrade_customer_gets_their_own_feed_once_signed_in(tmp_path, monkeypatch):
    import time

    from trader.accounts import vault
    from trader.api.routes import brokers as route
    from trader.market_data.simulated import SimConfig, SimulatedMarketDataProvider

    monkeypatch.setattr(route.flattrade_auth, "exchange_code", lambda *a, **k: {
        "token": "SESSION", "client_id": "FZ123", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()})
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'g.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, credential_key=vault.generate_key(), sim_ignore_session=True, sim_tick_interval_seconds=0.05,
                  feed_history_days=0, signup_open=True, signup_invite_code="")
    app = create_app(st, start_collector=False)
    with TestClient(app) as c:
        seen = []

        def factory(settings, holder):
            seen.append((settings.market_data_provider, settings.flattrade_client_id, holder["token"]))
            return SimulatedMarketDataProvider(SimConfig(seed=1, tick_interval=0.05, ignore_session=True))

        app.state.feeds.factory = factory
        signup(c, "g@example.com")
        c.post("/brokers/connections", json={"broker": "flattrade", "values": {"api_key": "K", "api_secret": "S"}})
        r = c.get("/market/overview")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "signin_required" and "Flattrade" in r.json()["detail"]["message"]
        assert c.get("/brokers/flattrade/callback", params={"code": "x"}, follow_redirects=False).status_code == 303
        assert c.get("/market/overview").status_code == 200
        assert seen == [("flattrade", "FZ123", "SESSION")]
