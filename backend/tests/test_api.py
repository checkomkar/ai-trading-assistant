import time

import pytest
from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.config import LIVE_CONFIRM_PHRASE, Settings, TradingMode


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "api.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{db.as_posix()}",
        sim_seed=3,
        sim_tick_interval_seconds=0.05,
        option_snapshot_interval_seconds=1,
    )
    with TestClient(create_app(settings)) as c:
        deadline = time.time() + 10
        while time.time() < deadline and c.get("/market/NIFTY").status_code != 200:
            time.sleep(0.1)
        time.sleep(1.5)  # let at least one flush + snapshot happen
        yield c


def test_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["trading_mode"] == "PAPER" and h["simulated"] is True
    feed = client.get("/health/feed").json()
    assert feed["connected"] and feed["ticks_received"] > 0
    assert feed["writes"]["ticks"] > 0 and feed["writes"]["option_snapshots"] > 0
    assert "issues" in client.get("/health/data").json()


def test_market_endpoints(client):
    q = client.get("/market/nifty").json()
    assert q["symbol"] == "NIFTY" and q["ltp"] > 0
    assert client.get("/market/INDIAVIX").status_code == 200
    assert client.get("/market/FOO").status_code == 404
    ov = client.get("/market/overview").json()
    assert set(ov["quotes"]) == {"NIFTY", "BANKNIFTY", "INDIAVIX"}
    qs = client.get("/market/quotes", params={"symbols": "NIFTY,BANKNIFTY"}).json()
    assert qs["BANKNIFTY"]["ltp"] > 0


def test_candles(client):
    r = client.get("/market/candles", params={"symbol": "NIFTY", "timeframe": "1m"}).json()
    assert r["candles"] and r["candles"][-1]["high"] >= r["candles"][-1]["low"]
    assert client.get("/market/candles", params={"symbol": "NIFTY-FUT"}).status_code == 200
    assert client.get("/market/candles", params={"timeframe": "3m"}).status_code == 400
    # `day` keeps only that IST trading day's candles; a day with no data is empty, not an error
    last = r["candles"][-1]["ts"]
    from datetime import datetime, timedelta, timezone
    ist_day = (datetime.fromisoformat(last) + timedelta(hours=5, minutes=30)).date()
    one = client.get("/market/candles", params={"symbol": "NIFTY", "timeframe": "1m", "limit": 5000,
                                                "day": ist_day.isoformat()}).json()["candles"]
    assert one and all((datetime.fromisoformat(c["ts"]) + timedelta(hours=5, minutes=30)).date() == ist_day for c in one)
    assert client.get("/market/candles", params={"day": "2001-01-01"}).json()["candles"] == []


def test_option_chain_and_resolver(client):
    exps = client.get("/options/nifty/expiries").json()
    assert exps["cycle"] == "WEEKLY" and exps["listed"]
    assert client.get("/options/banknifty/expiries").json()["cycle"] == "MONTHLY"
    chain = client.get("/options/NIFTY/chain", params={"strikes": 3}).json()
    assert len(chain["rows"]) == 7 and chain["atm_strike"]
    assert chain["summary"]["pcr"] is not None
    r = client.get("/instruments/resolve", params={"underlying": "NIFTY", "option_type": "CE"}).json()
    assert r["strike"] == chain["atm_strike"]


def test_websockets(client):
    with client.websocket_connect("/ws/market") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "market" and msg["quotes"]["NIFTY"]["ltp"] > 0
    with client.websocket_connect("/ws/options") as ws:
        ws.send_json({"underlying": "BANKNIFTY", "strikes": 2})
        msg = ws.receive_json()
        assert msg["type"] == "chain"


def test_live_mode_requires_confirmation():
    with pytest.raises(ValueError):
        Settings(_env_file=None, trading_mode=TradingMode.LIVE)
    s = Settings(_env_file=None, trading_mode=TradingMode.LIVE, live_trading_confirm=LIVE_CONFIRM_PHRASE)
    assert s.trading_mode == TradingMode.LIVE


def test_env_file_with_blank_optional_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SIM_SEED=\nREDIS_URL=\nMARKET_DATA_PROVIDER=fyers\n", encoding="utf-8")
    s = Settings(_env_file=env)
    assert s.sim_seed is None and s.market_data_provider == "fyers"


def test_session_date_and_reference_close():
    from datetime import date, datetime

    from trader.instruments.calendar import IST, TradingCalendar
    from trader.market_data.service import MarketDataService

    cal = TradingCalendar(holidays={date(2026, 10, 20): "holiday"})
    md = MarketDataService.__new__(MarketDataService)
    md.calendar = cal
    md.now = lambda: datetime(2026, 10, 20, 22, 0, tzinfo=IST)
    assert md.session_date() == date(2026, 10, 19)  # holiday -> last trading day
    md.now = lambda: datetime(2026, 10, 21, 9, 0, tzinfo=IST)
    assert md.session_date() == date(2026, 10, 19)  # before the open -> previous session
    md.now = lambda: datetime(2026, 10, 21, 9, 20, tzinfo=IST)
    assert md.session_date() == date(2026, 10, 21)
