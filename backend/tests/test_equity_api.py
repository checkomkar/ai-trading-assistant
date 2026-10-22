"""Stocks end to end: daily candle store, strategies, backtest API, chart, paper portfolios."""

from __future__ import annotations

import asyncio
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.config import Settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.instruments import equities as eq
from trader.market_data import equity_daily
from trader.market_data.candles import Candle

SYMS = ["TCS", "INFY", "WIPRO"]
LAST = date(2026, 10, 16)
DAYS = 520


def weekdays(n: int, end: date) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def walk(sym: str, seed: int) -> list[Candle]:
    rnd, px, out = random.Random(seed), 1000.0, []
    for d in weekdays(DAYS, LAST):
        o = px * (1 + rnd.uniform(-0.01, 0.01))
        c = o * (1 + rnd.uniform(-0.02, 0.022))
        out.append(Candle(eq.token(sym), "1d", eq.day_stamp(d), o, max(o, c) * 1.005, min(o, c) * 0.995, c,
                          rnd.randint(1000, 5000), closed=True))
        px = c
    return out


async def seed(url: str) -> None:
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    async with sm() as s, s.begin():
        ids = await repo.upsert_instruments(s, [eq.instrument(x) for x in SYMS])
        for n, x in enumerate(SYMS):
            await repo.upsert_candles(s, walk(x, n + 1), ids)
    await engine.dispose()


SPEC = {"market": "equity", "name": "Dip buyer", "underlyings": SYMS, "timeframe": "1d",
        "sides": {"long": {"entry": {"all": [{"cmp": [{"ind": "rsi", "period": 14}, "<", {"const": 40}]},
                                             {"cmp": [{"ind": "close"}, ">", {"ind": "sma", "period": 50}]}]}}},
        "exits": {"stop_loss_pct": 6, "target_pct": 12, "time_exit_bars": 15,
                  "conditions": {"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 65}]}},
        "risk": {"max_positions": 3, "risk_per_trade_pct": 1}}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    url = f"sqlite+aiosqlite:///{(tmp_path_factory.mktemp('eq') / 'eq.db').as_posix()}"
    upgrade_db(url)
    asyncio.run(seed(url))
    settings = Settings(_env_file=None, database_url=url, sim_seed=2, sim_tick_interval_seconds=0.2, sim_backfill_minutes=0)
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def saved(client):
    r = client.post("/strategies", json={"spec": SPEC})
    assert r.status_code == 200, r.text
    return r.json()


def test_validate_and_save_a_stock_strategy(client, saved):
    v = client.post("/strategies/validate", json={"spec": SPEC}).json()
    assert v["valid"] and "daily candles" in v["english"]
    bad = client.post("/strategies/validate", json={"spec": {**SPEC, "timeframe": "5m"}}).json()
    assert not bad["valid"] and any("daily" in e for e in bad["errors"])
    assert saved["spec"]["market"] == "equity"


def test_coverage_and_universe(client):
    cov = client.get("/equity/coverage").json()
    assert cov["symbols"] == 3 and cov["candles"] == 3 * DAYS and cov["to"] == LAST.isoformat()
    u = client.get("/equity/universe").json()
    assert len(u["universes"]["NIFTY50"]) == 50 and u["latest_day"] == LAST.isoformat()


def test_backtest_runs_on_daily_candles(client, saved):
    body = {"strategy_id": saved["id"], "start": "2025-11-03", "end": LAST.isoformat(), "timeframe": "1d",
            "engine_params": {"capital": 200000}}
    r = client.post("/backtests", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["timeframe"] == "1d" and res["pricing_mode"] == "equity delivery"
    m = res["metrics"]
    assert m["trades"] == len(res["trades"]) > 0
    assert m["equity_curve"][0]["equity"] == 200000 and len(m["daily_pnl"]) == res["days"]
    assert m["by_symbol"] and {s["symbol"] for s in m["by_symbol"]} <= set(SYMS)
    for t in res["trades"]:
        assert t["entry_time"] > t["signal_time"] and t["exit_time"] >= t["entry_time"] and t["charges"] > 0
        assert t["quantity"] > 0
    assert sum(t["net_pnl"] for t in res["trades"]) == pytest.approx(m["total_net_pnl"], abs=0.5)
    again = client.get(f"/backtests/{res['id']}").json()
    assert again["metrics"]["total_net_pnl"] == m["total_net_pnl"]
    assert client.get("/backtests").json()[0]["timeframe"] == "1d"
    ch = client.get(f"/backtests/{res['id']}/chart", params={"symbol": res["trades"][0]["contract"], "last_days": 120}).json()
    assert ch["timeframe"] == "1d" and len(ch["bars"]) > 50 and ch["symbols"]
    assert client.get(f"/backtests/{res['id']}/chart", params={"symbol": "HDFCBANK"}).status_code == 404


def test_backtest_without_data_says_how_to_load_it(client, saved):
    r = client.post("/backtests", json={"strategy_id": saved["id"], "start": "2030-01-01", "end": "2030-06-01", "timeframe": "1d"})
    assert r.status_code == 404 and "backfill_equities" in r.json()["detail"]


def test_intraday_runner_refuses_a_stock_strategy(client, saved):
    r = client.post("/paper/start", json={"strategy_id": saved["id"]})
    assert r.status_code == 400 and "Stocks" in r.json()["detail"]


def test_paper_portfolio_lifecycle(client, saved):
    r = client.post("/equity/paper", json={"strategy_id": saved["id"], "capital": 150000})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["start_day"] == LAST.isoformat() and p["equity"] == 150000 and p["positions"] == [] and p["trades"] == []
    listed = client.get("/equity/paper").json()
    assert [x["id"] for x in listed] == [p["id"]]
    assert client.get(f"/equity/paper/{p['id']}").json()["capital"] == 150000
    assert client.delete(f"/equity/paper/{p['id']}").status_code == 200
    assert client.get("/equity/paper").json() == []
    assert client.get(f"/equity/paper/{p['id']}").status_code == 404


def test_paper_portfolio_started_in_the_past_shows_positions_and_orders(client, saved):
    """A portfolio is a pure function of the candles: starting it earlier must show real positions and tomorrow's orders."""
    from trader.backtest import equity_paper
    from trader.db.models import EquityPaperRow

    sm = client.app.state.sessionmaker
    row = EquityPaperRow(id="past1", owner="local", name="past", strategy_id=saved["id"], spec=saved["spec"], capital=300000,
                         start_day=date(2025, 11, 3), active=True, created_at=datetime.now(timezone.utc))
    out = asyncio.run(equity_paper.evaluate(sm, row, {}))
    assert out["days_running"] > 200 and out["metrics"]["trades"] + len(out["positions"]) > 0
    assert out["equity"] == pytest.approx(out["cash"] + sum(p["value"] for p in out["positions"]), abs=0.5)
    for p in out["positions"]:
        assert p["stop"] < p["entry_price"] < p["target"]


def test_refresh_is_operator_only_and_needs_the_broker(client):
    assert client.post("/equity/refresh").status_code == 409  # simulated provider in tests


class FakeProvider:
    is_simulated = False

    def __init__(self):
        self.calls = []

    async def history(self, inst, tf, start, end):
        self.calls.append((inst.symbol, tf, start, end))
        out, d = [], start.astimezone(eq.IST).date()
        while d <= end.astimezone(eq.IST).date():
            if d.weekday() < 5:  # Fyers stamps a daily candle at IST midnight
                ts = datetime.combine(d, datetime.min.time(), tzinfo=eq.IST).astimezone(timezone.utc)
                out.append(Candle(inst.token, tf, ts, 100, 105, 95, 102, 10, closed=True))
            d += timedelta(days=1)
        return [c for c in out if start <= c.ts < end]


def test_refresh_stores_daily_candles_resumes_and_skips_today(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'r.db').as_posix()}"
    upgrade_db(url)

    async def go():
        engine = create_engine(url)
        sm = create_sessionmaker(engine)
        prov = FakeProvider()
        today = date(2026, 10, 14)
        first = await equity_daily.refresh(prov, sm, ["TCS", "BAJAJ-AUTO"], years=1, today=today)
        assert first["failed"] == {} and first["stored"]["TCS"] > 200
        async with sm() as s:
            got = await equity_daily.load_daily(s, ["TCS"], date(2025, 1, 1), date(2030, 1, 1))
        assert got["TCS"][0].ts.astimezone(eq.IST).time().isoformat() == "09:15:00"  # canonical stamp, not midnight
        n = len(got["TCS"])
        again = await equity_daily.refresh(prov, sm, ["TCS"], years=1, today=today + timedelta(days=3))
        assert again["stored"]["TCS"] >= 1
        async with sm() as s:
            assert len((await equity_daily.load_daily(s, ["TCS"], date(2025, 1, 1), date(2030, 1, 1)))["TCS"]) > n
        class Broken(FakeProvider):
            async def history(self, *a):
                raise RuntimeError("Invalid input")
        bad = await equity_daily.refresh(Broken(), sm, ["INFY"], years=1, today=today)
        assert "INFY" in bad["failed"] and "INFY" not in bad["stored"]
        await engine.dispose()

    asyncio.run(go())


def test_equities_only_merge_adds_stock_history_without_touching_the_rest(tmp_path):
    from trader.scripts.merge_market_data import create_target, merge

    src, dst = tmp_path / "src.db", tmp_path / "dst.db"
    url = f"sqlite+aiosqlite:///{src.as_posix()}"
    upgrade_db(url)
    asyncio.run(seed(url))
    create_target(dst)
    first = merge(src, dst, equities_only=True)
    assert first == {"instruments": 3, "candles": 3 * DAYS, "option_snapshots": 0}
    assert merge(src, dst, equities_only=True) == {"instruments": 0, "candles": 0, "option_snapshots": 0}  # safe to repeat
