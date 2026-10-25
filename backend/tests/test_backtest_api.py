import asyncio
from datetime import date, datetime, timedelta

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


def test_an_identical_request_on_unchanged_data_returns_the_stored_run(client):
    body = {"underlying": "NIFTY", "start": (END - timedelta(days=20)).isoformat(), "end": END.isoformat(),
            "strategy_params": {"adx_min": 12}}
    first = client.post("/backtests", json=body).json()
    assert not first.get("cached")
    again = client.post("/backtests", json=body).json()
    assert again["cached"] and again["id"] == first["id"] and again["metrics"] == first["metrics"]
    changed = client.post("/backtests", json=body | {"strategy_params": {"adx_min": 13}}).json()
    assert not changed.get("cached") and changed["id"] != first["id"]
    forced = client.post("/backtests", json=body | {"force": True}).json()
    assert not forced.get("cached") and forced["id"] != first["id"]
    assert client.post("/backtests", json=body).json()["id"] == forced["id"]  # the newest run is the one reused


def test_new_candles_in_the_range_invalidate_the_stored_run(client, tmp_path):
    from trader.backtest import cache
    from trader.db.models import CandleRow, InstrumentRow
    from sqlalchemy import select

    body = {"underlying": "NIFTY", "start": (END - timedelta(days=10)).isoformat(), "end": END.isoformat()}
    first = client.post("/backtests", json=body).json()
    sm = client.app.state.sessionmaker

    async def add_bar():
        async with sm() as s, s.begin():
            inst = (await s.execute(select(InstrumentRow).where(InstrumentRow.symbol == "NIFTY",
                                                                InstrumentRow.instrument_type == "INDEX"))).scalars().first()
            # the last stored candle inside the run's range (the live simulated feed adds today's, outside it)
            in_range = datetime.combine(END, datetime.max.time())
            last = (await s.execute(select(CandleRow).where(CandleRow.instrument_id == inst.id, CandleRow.ts <= in_range)
                                    .order_by(CandleRow.ts.desc()).limit(1))).scalars().first()
            s.add(CandleRow(instrument_id=inst.id, timeframe="1m", ts=last.ts - timedelta(minutes=1), open=1, high=1, low=1,
                            close=1, volume=0))

    asyncio.run(add_bar())
    assert client.post("/backtests", json=body).json()["id"] != first["id"]
    assert cache.code_version() == cache.code_version()


def test_trades_per_day_is_user_defined_and_zero_means_no_limit(client):
    from trader.strategy.spec import MOMENTUM_V1, StrategySpec, render_english

    def run(n):
        spec = MOMENTUM_V1 | {"risk": {"max_trades_per_day": n},
                              "exits": MOMENTUM_V1["exits"] | {"time_exit_bars": 2}}
        r = client.post("/backtests", json={"underlying": "NIFTY", "start": (END - timedelta(days=30)).isoformat(),
                                            "end": END.isoformat(), "strategy": "spec", "spec": spec})
        assert r.status_code == 200, r.text
        return r.json()["trades"]

    one, unlimited = run(1), run(0)
    per_day: dict = {}
    for t in one:
        per_day[t["signal_time"][:10]] = per_day.get(t["signal_time"][:10], 0) + 1
    assert max(per_day.values(), default=0) <= 1
    assert len(unlimited) >= len(one)
    assert "no limit on trades per day" in render_english(StrategySpec.model_validate(MOMENTUM_V1 | {"risk": {"max_trades_per_day": 0}}))
    StrategySpec.model_validate(MOMENTUM_V1 | {"risk": {"max_trades_per_day": 60}})  # above the old cap of 20
