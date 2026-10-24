"""Profile page: the signed-in account's details, plan status, name and password changes, other sessions."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_billing import PW, client, signup  # noqa: F401 - fixture with accounts on
from trader.api.main import create_app
from trader.config import Settings


def test_profile_shows_the_account_and_its_trial(client):
    signup(client, "p@x.com")
    pr = client.get("/auth/profile").json()
    assert pr["local"] is False and pr["email"] == "p@x.com" and pr["name"] == "A"
    assert pr["status"] == "trial" and pr["plan"]["id"] == "trial" and pr["sign_in"] == ["password"]
    assert pr["sessions"] == 1 and pr["this_session_expires"]


def test_rename_and_password_change_end_other_sessions(client, tmp_path):
    signup(client, "q@x.com")
    other = TestClient(client.app)
    assert other.post("/auth/login", json={"email": "q@x.com", "password": PW}).status_code == 200
    assert client.get("/auth/profile").json()["sessions"] == 2

    assert client.patch("/auth/profile", json={"name": "  Quinn   Q "}).json()["name"] == "Quinn Q"
    assert client.patch("/auth/profile", json={"name": "   "}).status_code == 422

    assert client.post("/auth/password", json={"current": "wrong", "new": "another long one"}).status_code == 422
    assert client.post("/auth/password", json={"current": PW, "new": "short"}).status_code == 422
    assert client.post("/auth/password", json={"current": PW, "new": "another long one"}).status_code == 200
    assert other.get("/auth/profile").status_code == 401  # the other device was signed out
    assert client.get("/auth/profile").json()["sessions"] == 1
    assert client.post("/auth/login", json={"email": "q@x.com", "password": "another long one"}).status_code == 200


def test_signed_out_and_local_mode(client, tmp_path):
    assert client.get("/auth/profile").status_code == 401
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'l.db').as_posix()}", market_data_provider="simulated")
    with TestClient(create_app(st, start_collector=False)) as c:
        pr = c.get("/auth/profile").json()
        assert pr["local"] is True and pr["status"] == "local"
        assert c.patch("/auth/profile", json={"name": "x"}).status_code == 404
