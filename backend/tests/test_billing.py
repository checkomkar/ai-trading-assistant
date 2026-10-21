"""SaaS layer: accounts, plans, the 5-generation trial, metering, per-account isolation, payment webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trader.ai.providers import LLMReply
from trader.api.main import create_app
from trader.billing import plans as plans_mod
from trader.billing.pricing import cost_usd
from trader.config import Settings

SECRET = "whsec_test"
PW = "correct horse battery"


class FakeProvider:
    name, model = "fake", "m"
    label = "fake/m"

    def complete(self, system, messages, schema, tools=None):
        spec = {"spec_json": "", "assumptions": [], "questions": ["which index?"], "unsupported": []}
        return LLMReply(json.dumps(spec), 1000, 500, "groq/openai/gpt-oss-120b")

    def describe_image(self, data, mime, prompt):
        return "a chart"


@pytest.fixture
def client(tmp_path, monkeypatch):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 't.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, admin_emails="boss@x.com", razorpay_key_id="k", razorpay_key_secret="s",
                  razorpay_webhook_secret=SECRET, razorpay_plan_pro="plan_pro", razorpay_plan_max="plan_max")
    monkeypatch.setattr("trader.api.routes.strategies.build_provider", lambda _s: FakeProvider())
    with TestClient(create_app(st, start_collector=False)) as c:
        yield c


def signup(c, email="a@x.com"):
    r = c.post("/auth/signup", json={"email": email, "password": PW, "name": "A"})
    assert r.status_code == 200, r.text
    return r.json()


def test_plan_margins_hold():
    """No paid plan may let the AI budget exceed 40% of its revenue after GST."""
    for p in plans_mod.public_plans():
        if p.price_inr:
            assert plans_mod.margin_report(p)["ai_share"] <= plans_mod.MAX_AI_COST_SHARE, p.id
    assert plans_mod.PLANS["trial"].generations == 5


def test_cost_from_tokens_and_free_models():
    assert cost_usd("openrouter/google/gemma-4-31b-it:free", 10_000, 10_000) == 0
    assert cost_usd("groq/openai/gpt-oss-120b", 1_000_000, 0) == pytest.approx(0.15)
    assert cost_usd("some/unknown-model", 1_000_000, 0) == pytest.approx(5.0)  # unknown = priced high


def test_login_required_and_cookie_session(client):
    assert client.get("/strategies").status_code == 401
    assert client.get("/market/quotes").status_code == 401
    assert client.get("/auth/me").json()["signed_in"] is False
    signup(client)
    assert client.get("/strategies").json() == []
    assert client.get("/auth/me").json()["plan"] == "trial"
    client.post("/auth/logout")
    assert client.get("/strategies").status_code == 401
    assert client.post("/auth/login", json={"email": "a@x.com", "password": "wrong password!"}).status_code == 401
    assert client.post("/auth/login", json={"email": "A@x.com", "password": PW}).status_code == 200


def test_weak_password_and_duplicate_email_rejected(client):
    assert client.post("/auth/signup", json={"email": "b@x.com", "password": "short"}).status_code == 422
    signup(client, "b@x.com")
    assert client.post("/auth/signup", json={"email": "B@x.com", "password": PW}).status_code == 422


def test_trial_allows_exactly_five_generations_then_402(client):
    signup(client)
    for _ in range(5):
        assert client.post("/strategies/translate", json={"text": "buy calls on breakout"}).status_code == 200
    r = client.post("/strategies/translate", json={"text": "buy calls on breakout"})
    assert r.status_code == 402
    body = r.json()["detail"]
    assert body["code"] == "quota_exceeded" and body["resource"] == "generations" and body["plan"] == "trial"
    u = client.get("/billing/usage").json()
    assert u["usage"]["generations"] == {"used": 5, "limit": 5, "remaining": 0}
    assert "ai_cost_usd" not in u  # customers never see our cost
    assert u["ai_allowance_pct"] > 0


def test_failed_model_call_gives_the_unit_back(client, monkeypatch):
    from trader.ai.providers import TranslatorUnavailable

    class Down(FakeProvider):
        def complete(self, *a, **k):
            raise TranslatorUnavailable("no key")

    monkeypatch.setattr("trader.api.routes.strategies.build_provider", lambda _s: Down())
    signup(client)
    assert client.post("/strategies/translate", json={"text": "buy calls on breakout"}).status_code == 503
    assert client.get("/billing/usage").json()["usage"]["generations"]["used"] == 0


def test_backtests_are_counted_but_cost_no_ai_allowance(client):
    signup(client)
    client.post("/backtests", json={"start": "2026-01-01", "end": "2026-01-10"})  # no data: 404, refunded
    u = client.get("/billing/usage").json()
    assert u["usage"]["backtests"]["used"] == 0 and u["usage"]["generations"]["used"] == 0
    assert u["ai_allowance_pct"] == 0


def test_accounts_cannot_see_each_others_strategies(client):
    spec = client.get("/strategies/template") if False else None
    signup(client, "one@x.com")
    tpl = client.get("/strategies/template").json()["spec"]
    saved = client.post("/strategies", json={"spec": tpl}).json()
    client.post("/auth/logout")
    signup(client, "two@x.com")
    assert client.get("/strategies").json() == []
    assert client.get(f"/strategies/{saved['id']}").status_code == 404
    assert client.get(f"/strategies/{saved['id']}/versions").status_code == 404
    # the same name is free for the second account
    assert client.post("/strategies", json={"spec": tpl}).json()["version"] == 1


def test_paper_and_admin_are_operator_only(client):
    signup(client)
    assert client.get("/paper/status").status_code == 403
    assert client.post("/admin/grant", json={"email": "a@x.com", "plan": "pro"}).status_code == 403
    client.post("/auth/logout")
    signup(client, "boss@x.com")
    assert client.get("/auth/me").json()["plan"] == "owner"
    assert client.get("/paper/status").status_code == 200


def test_operator_can_grant_a_plan_and_limits_follow(client):
    signup(client)
    client.post("/auth/logout")
    signup(client, "boss@x.com")
    assert client.post("/admin/grant", json={"email": "a@x.com", "plan": "pro", "days": 30}).status_code == 200
    client.post("/auth/logout")
    client.post("/auth/login", json={"email": "a@x.com", "password": PW})
    u = client.get("/billing/usage").json()
    assert u["plan"]["id"] == "pro" and u["usage"]["generations"]["limit"] == 60


def test_strategy_count_limit(client, monkeypatch):
    monkeypatch.setitem(plans_mod.PLANS, "trial", plans_mod.Plan(**{**plans_mod.PLANS["trial"].__dict__, "strategies": 1}))
    signup(client)
    tpl = client.get("/strategies/template").json()["spec"]
    assert client.post("/strategies", json={"spec": tpl}).status_code == 200
    other = {**tpl, "name": "Second one"}
    r = client.post("/strategies", json={"spec": other})
    assert r.status_code == 402 and r.json()["detail"]["resource"] == "strategies"
    assert client.post("/strategies", json={"spec": {**tpl, "description": "tweak"}}).status_code in (200, 422)  # new version of existing: allowed


def _hook(client, event, entity, event_id="evt_1", secret=SECRET):
    raw = json.dumps({"event": event, "payload": {"subscription": {"entity": entity}}}).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/billing/webhook", content=raw, headers={"x-razorpay-signature": sig, "x-razorpay-event-id": event_id})


def test_webhook_rejects_bad_signature_and_upgrades_on_valid_one(client):
    me = signup(client)
    end = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    ent = {"id": "sub_1", "plan_id": "plan_pro", "current_start": int(datetime.now(timezone.utc).timestamp()),
           "current_end": end, "notes": {"account_id": me["id"]}}
    assert _hook(client, "subscription.activated", ent, secret="wrong").status_code == 400
    assert client.get("/billing/usage").json()["plan"]["id"] == "trial"
    assert _hook(client, "subscription.activated", ent).status_code == 200
    assert client.get("/billing/usage").json()["plan"]["id"] == "pro"
    assert _hook(client, "subscription.activated", ent).json() == {"duplicate": True}  # retries are harmless
    assert _hook(client, "subscription.halted", ent, event_id="evt_2").status_code == 200  # payment failed
    assert client.get("/billing/usage").json()["plan"]["id"] == "trial"


def test_plans_endpoint_is_public(client):
    r = client.get("/billing/plans").json()
    assert [p["id"] for p in r["plans"]] == ["trial", "pro", "max"]
    assert r["plans"][0]["limits"]["generations"] == 5 and r["checkout_enabled"] is True


def test_local_mode_is_unlimited_and_needs_no_login(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'l.db').as_posix()}", market_data_provider="simulated")
    with TestClient(create_app(st, start_collector=False)) as c:
        assert c.get("/auth/me").json()["plan"] == "owner"
        u = c.get("/billing/usage").json()
        assert u["usage"]["generations"]["limit"] is None


def test_broker_keys_are_encrypted_write_only_and_per_account(tmp_path):
    import sqlite3

    from trader.accounts import vault

    key = vault.generate_key()
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'b.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, credential_key=key)
    with TestClient(create_app(st, start_collector=False)) as c:
        signup(c, "one@x.com")
        assert any(b["id"] == "fyers" and b["status"] == "available" for b in c.get("/brokers/catalog").json()["brokers"])
        assert c.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": "AB-100"}}).status_code == 422
        r = c.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": "AB-100", "secret_key": "TOPSECRET"}})
        assert r.status_code == 200 and "TOPSECRET" not in r.text and r.json()["public"] == {"client_id": "AB-100"}
        assert "TOPSECRET" not in c.get("/brokers/connections").text
        db = sqlite3.connect(tmp_path / "b.db")
        blob = db.execute("select secret_blob from broker_connections").fetchone()[0]
        db.close()
        assert "TOPSECRET" not in blob and vault.unseal(blob, key)["secret_key"] == "TOPSECRET"
        conn_id = r.json()["id"]
        c.post("/auth/logout")
        signup(c, "two@x.com")
        assert c.get("/brokers/connections").json() == []
        assert c.delete(f"/brokers/connections/{conn_id}").status_code == 404


def test_broker_keys_need_a_server_key(client):
    signup(client)
    r = client.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": "A", "secret_key": "B"}})
    assert r.status_code == 503


def test_fyers_sign_in_flow_stores_a_token_per_account_and_tests_it(tmp_path, monkeypatch):
    import base64
    import time
    import urllib.parse

    from trader.accounts import vault
    from trader.api.routes import brokers as br
    from trader.brokers.fyers.auth import FyersAuthError

    def jwt(exp):
        body = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
        return f"h.{body}.s"

    key = vault.generate_key()
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'f.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, credential_key=key, public_url="https://striktek.example")
    seen = {}

    def fake_exchange(settings, code, post=None):
        seen["ns"], seen["code"] = settings, code
        if code == "bad":
            raise FyersAuthError("invalid code")
        return jwt(int(time.time()) + 3600)

    async def fake_profile(client_id, token, quote_symbol="x"):
        return {"name": "Test User", "fy_id": "XA12345", "email": "t@x.com", "nifty": 24990.5}

    monkeypatch.setattr(br, "exchange_auth_code", fake_exchange)
    monkeypatch.setattr(br, "fetch_profile", fake_profile)
    with TestClient(create_app(st, start_collector=False)) as c:
        signup(c, "one@x.com")
        assert c.get("/brokers/catalog").json()["fyers_redirect_uri"] == "https://striktek.example/api/brokers/fyers/callback"
        assert c.post("/brokers/fyers/login-url").status_code == 404  # no keys saved yet
        c.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": "AB-100", "secret_key": "SECRET1"}})
        assert c.get("/brokers/connections").json()[0]["session"]["state"] == "none"
        assert c.post("/brokers/fyers/test").json()["needs_login"] is True

        out = c.post("/brokers/fyers/login-url").json()
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(out["url"]).query))
        assert q["client_id"] == "AB-100" and q["redirect_uri"] == "https://striktek.example/api/brokers/fyers/callback"
        assert "SECRET1" not in out["url"]

        assert c.get("/brokers/fyers/callback", params={"auth_code": "x", "state": "forged.1.abc"}).status_code == 400
        ok = c.get("/brokers/fyers/callback", params={"auth_code": "good", "state": q["state"]}, follow_redirects=False)
        assert ok.status_code == 303 and seen["code"] == "good" and seen["ns"].fyers_secret_key == "SECRET1"
        conn = c.get("/brokers/connections").json()[0]
        assert conn["session"]["state"] == "signed_in" and "access_token" not in json.dumps(conn)
        assert c.post("/brokers/fyers/test").json() == {"ok": True, "name": "Test User", "fy_id": "XA12345", "email": "t@x.com", "nifty": 24990.5}

        # a state minted for one customer is useless to another
        c.post("/auth/logout")
        signup(c, "two@x.com")
        c.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": "ZZ-100", "secret_key": "S2"}})
        assert c.get("/brokers/fyers/callback", params={"auth_code": "good", "state": q["state"]}).status_code == 400
        assert c.get("/brokers/connections").json()[0]["session"]["state"] == "none"

        # saving new keys drops the old token
        c.post("/auth/logout")
        c.post("/auth/login", json={"email": "one@x.com", "password": PW})
        c.post("/brokers/connections", json={"broker": "fyers", "values": {"client_id": "AB-100", "secret_key": "NEW"}})
        assert c.get("/brokers/connections").json()[0]["session"]["state"] == "none"
