import asyncio
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.config import Settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.domain import InstrumentType
from trader.market_data.simulated import SimConfig, SimulatedMarketDataProvider
from trader.scripts.generate_sim_history import generate, trading_days

END = date(2026, 10, 16)


async def _seed(url: str) -> None:
    sim = SimulatedMarketDataProvider(SimConfig(seed=1))
    insts = [i for i in await sim.discover_instruments() if i.instrument_type != InstrumentType.OPT]
    tokens = {i.symbol: i.token for i in insts if i.instrument_type == InstrumentType.INDEX}
    tokens |= {f"{i.underlying}-FUT": i.token for i in insts if i.instrument_type == InstrumentType.FUT}
    candles = generate(trading_days(15, END), tokens, seed=5, timeframes=("5m",))
    engine = create_engine(url)
    async with create_sessionmaker(engine)() as s, s.begin():
        ids = await repo.upsert_instruments(s, insts)
        await repo.upsert_candles(s, candles, ids)
    await engine.dispose()


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    url = f"sqlite+aiosqlite:///{(tmp_path_factory.mktemp('bt') / 'bt.db').as_posix()}"
    upgrade_db(url)
    asyncio.run(_seed(url))
    settings = Settings(_env_file=None, database_url=url, sim_seed=2, sim_tick_interval_seconds=0.2,
                        sim_backfill_minutes=0)
    with TestClient(create_app(settings)) as c:
        yield c


def test_coverage_reports_simulated_source(client):
    cov = client.get("/backtests/coverage", params={"underlying": "NIFTY"}).json()
    assert cov["real"]["candles"] == 0 and cov["simulated"]["candles"] > 1000
    assert cov["simulated"]["to"] == END.isoformat()


def test_run_list_and_fetch(client):
    body = {"underlying": "NIFTY", "start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(),
            "strategy_params": {"adx_min": 10, "stop_loss_pct": 30, "target_pct": 60}}
    r = client.post("/backtests", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["data_source"] == "simulated" and res["pricing_mode"] == "synthetic"
    assert res["days"] == 15 and res["bars"] == 15 * 75
    assert any("SIMULATED" in w for w in res["warnings"])
    m = res["metrics"]
    assert m["trades"] == len(res["trades"]) and len(m["daily_pnl"]) == 15
    assert res["params"]["strategy"]["stop_loss_pct"] == 30
    for t in res["trades"]:
        assert t["entry_time"] > t["signal_time"]  # never same-bar
    runs = client.get("/backtests").json()
    assert runs[0]["id"] == res["id"] and runs[0]["data_source"] == "simulated"
    again = client.get(f"/backtests/{res['id']}").json()
    assert again["metrics"]["total_net_pnl"] == m["total_net_pnl"]


def test_validation_and_missing_data(client):
    assert client.post("/backtests", json={"start": "2026-10-10", "end": "2026-10-01"}).status_code == 422
    r = client.post("/backtests", json={"start": "2020-01-01", "end": "2020-02-01", "source": "real"})
    assert r.status_code == 404 and "Backfill" in r.json()["detail"]
    assert client.get("/backtests/nope").status_code == 404


def test_higher_timeframe_is_built_from_stored_candles(client):
    cov = client.get("/backtests/coverage", params={"underlying": "NIFTY", "timeframe": "15m"}).json()
    assert cov["simulated"]["built_from"] == "5m" and "15m" not in cov["simulated"]["stored_timeframes"]
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "timeframe": "15m",
            "strategy_params": {"adx_min": 5}}
    res = client.post("/backtests", json=body).json()
    assert res["built_from"] == "5m" and res["bars"] == 15 * 25  # 25 x 15m bars per session
    assert any("built from stored 5m" in w for w in res["warnings"])


def test_chart_endpoint_matches_trades(client):
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "strategy_params": {"adx_min": 5}}
    res = client.post("/backtests", json=body).json()
    ch = client.get(f"/backtests/{res['id']}/chart").json()
    assert len(ch["bars"]) == res["bars"] and ch["ema_fast"]["period"] == 9
    times = {b[0] for b in ch["bars"]}
    from datetime import datetime as _dt
    for t in res["trades"]:
        assert int(_dt.fromisoformat(t["entry_time"]).timestamp()) in times  # markers land on real bars
        assert int(_dt.fromisoformat(t["exit_bar_time"]).timestamp()) in times
    assert client.get("/backtests/nope/chart").status_code == 404
    c15 = client.get(f"/backtests/{res['id']}/chart", params={"timeframe": "15m"}).json()
    assert c15["timeframe"] == "15m" and len(c15["bars"]) == len(ch["bars"]) // 3
    last = client.get(f"/backtests/{res['id']}/chart", params={"timeframe": "15m", "last_days": 2}).json()
    assert len(last["bars"]) == 2 * 25
    assert client.get(f"/backtests/{res['id']}/chart", params={"timeframe": "7m"}).status_code == 400


def test_prefers_timeframe_with_more_coverage(tmp_path):
    """Two days of native 15m must not beat 15 days of 5m when building 15m bars."""
    from trader.backtest import data as bt
    from trader.db import repository as repo
    from trader.db.session import create_engine as ce, create_sessionmaker as cs
    from trader.market_data.candles import Candle

    url = f"sqlite+aiosqlite:///{(tmp_path / 'cov.db').as_posix()}"
    upgrade_db(url)
    asyncio.run(_seed(url))

    async def go():
        eng = ce(url)
        sm = cs(eng)
        async with sm() as s:
            five = await bt.load(s, "NIFTY", "5m", END - timedelta(days=30), END, "simulated")
        native = [Candle("SIM:NIFTY", "15m", c.ts, c.open, c.high, c.low, c.close, 0, True)
                  for c in five.bars[-50:] if c.ts.minute % 15 == 0]
        async with sm() as s, s.begin():
            ids = await repo.upsert_instruments(s, [])
            await repo.upsert_candles(s, native, ids)
        async with sm() as s:
            d = await bt.load(s, "NIFTY", "15m", END - timedelta(days=30), END, "simulated")
        await eng.dispose()
        return d

    d = asyncio.run(go())
    assert d.built_from == "5m" and len(d.bars) == 15 * 25
