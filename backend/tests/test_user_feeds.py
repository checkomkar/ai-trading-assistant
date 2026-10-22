"""Each customer's live feed comes from their own saved broker sign-in; nobody sees another customer's (or the operator's) data."""

from __future__ import annotations

import base64
import json
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from trader.accounts import vault
from trader.api.main import create_app
from trader.api.routes import brokers as br
from trader.config import Settings
from trader.market_data.simulated import SimConfig, SimulatedMarketDataProvider

PW = "correct horse battery"


def jwt(ttl: int) -> str:
    body = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) + ttl}).encode()).decode().rstrip("=")
    return f"h.{body}.s"


class Env:
    """An app whose customer feeds run on the simulated provider (the stand-in for each customer's own broker feed)."""

    def __init__(self, tmp_path, monkeypatch, **over):
        self.token_ttl = 3600
        self.created: list[dict] = []
        key = vault.generate_key()
        self.st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'u.db').as_posix()}", market_data_provider="simulated",
                           auth_enabled=True, credential_key=key, admin_emails="boss@x.com", public_url="https://s.example",
                           sim_ignore_session=True, sim_tick_interval_seconds=0.05, feed_history_days=0, **over)
        monkeypatch.setattr(br, "exchange_auth_code", lambda s, c, post=None: jwt(self.token_ttl))

        def factory(settings, holder):
            self.created.append({"client_id": settings.fyers_client_id, "holder": holder})
            return SimulatedMarketDataProvider(SimConfig(seed=len(self.created), tick_interval=0.05, ignore_session=True))

        self.factory = factory

    def app(self):
        return create_app(self.st, start_collector=False)


def sign_up(c, email):
    r = c.post("/auth/signup", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text


def save_keys(c, client_id="AB-100"):
    c.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": client_id, "secret_key": "S"}})


def sign_in_to_fyers(c):
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(c.post("/brokers/fyers/login-url").json()["url"]).query))
    r = c.get("/brokers/fyers/callback", params={"auth_code": "ok", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 303


def connect_and_sign_in(c, client_id="AB-100"):
    save_keys(c, client_id)
    sign_in_to_fyers(c)


@pytest.fixture
def env(tmp_path, monkeypatch):
    return Env(tmp_path, monkeypatch)


def test_customer_without_a_broker_gets_a_clear_409_not_the_operators_data(env):
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "cust@x.com")
        for path in ("/market/overview", "/market/candles?symbol=NIFTY", "/options/NIFTY/expiries", "/health/feed"):
            r = c.get(path)
            assert r.status_code == 409 and r.json()["detail"]["code"] == "broker_required", path


def test_saved_keys_without_todays_sign_in_ask_for_the_sign_in(env):
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "cust@x.com")
        save_keys(c)
        r = c.get("/market/overview")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "signin_required"


def test_each_customer_gets_their_own_feed(env):
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "one@x.com")
        connect_and_sign_in(c, "ONE-100")
        assert c.get("/market/overview").status_code == 200
        assert env.created[-1]["client_id"] == "ONE-100"
        c.post("/auth/logout")
        sign_up(c, "two@x.com")
        connect_and_sign_in(c, "TWO-100")
        assert c.get("/market/overview").status_code == 200
        assert [x["client_id"] for x in env.created] == ["ONE-100", "TWO-100"]
        assert app.state.feeds.active() == 2
        mds = [f.md for f in app.state.feeds._feeds.values()]
        assert mds[0] is not mds[1] and app.state.md not in mds  # separate from each other and from the shared feed


def test_an_expired_sign_in_stops_the_feed_and_asks_for_a_new_one(env):
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "cust@x.com")
        connect_and_sign_in(c)
        assert c.get("/market/overview").status_code == 200
        owner = next(iter(app.state.feeds._feeds))
        app.state.feeds._feeds[owner].holder["token"] = jwt(-10)  # the 06:00 IST expiry arrives
        r = c.get("/market/overview")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "signin_required"
        assert app.state.feeds.active() == 0


def test_signing_in_again_swaps_the_token_and_reconnects(env):
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "cust@x.com")
        connect_and_sign_in(c)
        c.get("/market/overview")
        holder = env.created[-1]["holder"]
        old = holder["token"]
        env.token_ttl = 7200  # a different expiry makes a different token
        sign_in_to_fyers(c)
        deadline = time.time() + 10
        while holder["token"] == old and time.time() < deadline:
            time.sleep(0.1)
        assert holder["token"] != old and len(env.created) == 1  # same feed, new token (no second feed)


def test_replacing_or_removing_keys_stops_the_feed(env):
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "cust@x.com")
        connect_and_sign_in(c)
        c.get("/market/overview")
        assert app.state.feeds.active() == 1
        save_keys(c, "NEW-100")
        assert app.state.feeds.active() == 0


def test_idle_feeds_are_reaped_and_the_cap_is_enforced(tmp_path, monkeypatch):
    env = Env(tmp_path, monkeypatch, max_user_feeds=1, feed_idle_seconds=1)
    app = env.app()
    with TestClient(app) as c:
        app.state.feeds.factory = env.factory
        sign_up(c, "one@x.com")
        connect_and_sign_in(c)
        assert c.get("/market/overview").status_code == 200
        c.post("/auth/logout")
        sign_up(c, "two@x.com")
        connect_and_sign_in(c, "TWO-100")
        r = c.get("/market/overview")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "feed_busy"  # one machine, one feed
        time.sleep(1.2)
        assert c.portal.call(app.state.feeds.reap) == 1
        assert c.get("/market/overview").status_code == 200  # a place is free again


def test_operator_without_a_broker_still_sees_the_shared_feed_and_local_mode_is_unchanged(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'a.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, admin_emails="boss@x.com")
    with TestClient(create_app(st, start_collector=False)) as c:
        sign_up(c, "boss@x.com")
        assert c.get("/market/overview").status_code == 200
    st2 = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'l.db').as_posix()}", market_data_provider="simulated")
    with TestClient(create_app(st2, start_collector=False)) as c:
        assert c.get("/market/overview").status_code == 200


def test_a_shoonya_customer_gets_their_own_feed_after_signing_in(env, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(br.shoonya_auth, "sign_in", lambda *a, **k: {
        "token": "SESSION", "user_id": "FA1", "name": "O", "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat()})
    app = env.app()
    with TestClient(app) as c:
        seen = []

        def factory(settings, holder):
            seen.append((settings.market_data_provider, settings.shoonya_user_id, holder["token"]))
            return env.factory(settings, holder)

        app.state.feeds.factory = factory
        sign_up(c, "sh@x.com")
        assert c.get("/market/overview").status_code == 409  # no broker yet
        c.post("/brokers/connections", json={"broker": "shoonya", "values": {"user_id": "FA1", "api_key": "K", "vendor_code": "FA1_U", "imei": "x"}})
        r = c.get("/market/overview")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "signin_required" and "Shoonya" in r.json()["detail"]["message"]
        assert c.post("/brokers/shoonya/login", json={"password": "p", "totp": "123456"}).status_code == 200
        assert c.get("/market/overview").status_code == 200
        assert seen == [("shoonya", "FA1", "SESSION")]
