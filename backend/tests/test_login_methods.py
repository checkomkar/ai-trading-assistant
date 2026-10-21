"""Google, phone-OTP and email/password sign-in all land on the same accounts and the same trial."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from trader.accounts import google, otp
from trader.api.main import create_app
from trader.config import Settings

PW = "correct horse battery"


class FakeSms:
    name = "fake"

    def __init__(self):
        self.sent = []

    async def send(self, phone, text):
        self.sent.append((phone, text))

    def code(self):
        return self.sent[-1][1].split()[0]


@pytest.fixture
def env(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'l.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, google_client_id="cid", admin_emails="boss@x.com", sms_provider="console")
    app = create_app(st, start_collector=False)
    with TestClient(app) as c:
        app.state.sms = sms = FakeSms()
        ident = {"sub": "g-1", "email": "Gina@x.com", "name": "Gina"}

        async def fake_verify(credential, client_id):
            if credential == "bad":
                raise google.GoogleError("Google did not accept that sign-in.")
            return {**ident, "sub": credential}

        app.state.google_verify = fake_verify
        yield c, sms, ident


def test_phone_number_normalising():
    assert otp.normalise_phone("98765 43210") == "+919876543210"
    assert otp.normalise_phone("+1 (415) 555-2671") == "+14155552671"
    with pytest.raises(Exception):
        otp.normalise_phone("12345")


def test_me_advertises_available_methods(env):
    c, _, _ = env
    me = c.get("/auth/me").json()
    assert me["google_client_id"] == "cid" and me["phone_login"] is True


def test_otp_signs_in_creates_one_trial_account_and_code_is_single_use(env):
    c, sms, _ = env
    assert c.post("/auth/otp/send", json={"phone": "9876543210"}).json()["phone"] == "+919876543210"
    code = sms.code()
    assert len(code) == 6
    assert c.post("/auth/otp/verify", json={"phone": "9876543210", "code": "000000" if code != "000000" else "111111"}).status_code == 401
    r = c.post("/auth/otp/verify", json={"phone": "9876543210", "code": code, "name": "Ravi"})
    assert r.status_code == 200 and r.json()["plan"] == "trial" and r.json()["email"] == "+919876543210"
    assert c.get("/strategies").status_code == 200
    c.post("/auth/logout")
    assert c.post("/auth/otp/verify", json={"phone": "9876543210", "code": code}).status_code == 401  # already used
    c.post("/auth/otp/send", json={"phone": "+919876543210"})  # rate-limited resend is covered below; same number, same account
    assert c.get("/auth/me").json()["signed_in"] is False


def test_otp_locks_after_five_wrong_guesses(env):
    c, sms, _ = env
    c.post("/auth/otp/send", json={"phone": "9876543210"})
    code = sms.code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert c.post("/auth/otp/verify", json={"phone": "9876543210", "code": wrong}).status_code == 401
    r = c.post("/auth/otp/verify", json={"phone": "9876543210", "code": code})
    assert r.status_code == 401  # the right code no longer works: ask for a new one


def test_otp_resend_is_rate_limited(env):
    c, _, _ = env
    assert c.post("/auth/otp/send", json={"phone": "9876543210"}).status_code == 200
    assert c.post("/auth/otp/send", json={"phone": "9876543210"}).status_code == 429


def test_otp_off_without_a_provider(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'n.db').as_posix()}", market_data_provider="simulated", auth_enabled=True)
    with TestClient(create_app(st, start_collector=False)) as c:
        assert c.get("/auth/me").json()["phone_login"] is False
        assert c.post("/auth/otp/send", json={"phone": "9876543210"}).status_code == 404


def test_console_sms_refused_when_cookies_are_secure(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 's.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, sms_provider="console", auth_cookie_secure=True)
    with TestClient(create_app(st, start_collector=False)) as c:
        assert c.get("/auth/me").json()["phone_login"] is False


def test_google_creates_then_reuses_the_account_and_links_password_accounts(env):
    c, _, ident = env
    r = c.post("/auth/google", json={"credential": "g-1"})
    assert r.status_code == 200 and r.json()["email"] == "gina@x.com" and r.json()["plan"] == "trial"
    gid = r.json()["id"]
    c.post("/auth/logout")
    assert c.post("/auth/google", json={"credential": "g-1"}).json()["id"] == gid
    assert c.post("/auth/google", json={"credential": "bad"}).status_code == 401
    # a Google account has no password, so the password form cannot be used to enter it
    assert c.post("/auth/login", json={"email": "gina@x.com", "password": PW}).status_code == 401
    # someone who signed up with a password and later uses Google (same verified email) gets the same account
    c.post("/auth/logout")
    pid = c.post("/auth/signup", json={"email": "gina2@x.com", "password": PW}).json()["id"]
    c.post("/auth/logout")
    ident.update(email="gina2@x.com")
    assert c.post("/auth/google", json={"credential": "g-2"}).json()["id"] == pid


def test_google_admin_email_gets_owner_plan(env):
    c, _, ident = env
    ident.update(email="boss@x.com")
    assert c.post("/auth/google", json={"credential": "g-9"}).json()["plan"] == "owner"


def test_closed_signup_blocks_new_google_and_phone_accounts_only(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'c.db').as_posix()}", market_data_provider="simulated",
                  auth_enabled=True, google_client_id="cid", sms_provider="console", signup_invite_code="beta")
    app = create_app(st, start_collector=False)
    with TestClient(app) as c:
        app.state.sms = sms = FakeSms()

        async def v(credential, client_id):
            return {"sub": credential, "email": "a@x.com", "name": ""}

        app.state.google_verify = v
        assert c.post("/auth/google", json={"credential": "g-1"}).status_code == 403
        assert c.post("/auth/google", json={"credential": "g-1", "invite_code": "beta"}).status_code == 200
        c.post("/auth/logout")
        assert c.post("/auth/google", json={"credential": "g-1"}).status_code == 200  # existing: no code needed
        c.post("/auth/otp/send", json={"phone": "9876543210"})
        assert c.post("/auth/otp/verify", json={"phone": "9876543210", "code": sms.code()}).status_code == 403
