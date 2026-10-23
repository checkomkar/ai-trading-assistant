import ast
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.config import Settings

TRADER = Path(__file__).resolve().parent.parent / "trader"


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db = tmp_path_factory.mktemp("db") / "paper_api.db"
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{db.as_posix()}", sim_seed=5,
                        sim_tick_interval_seconds=0.05)
    with TestClient(create_app(settings)) as c:
        deadline = time.time() + 10
        while time.time() < deadline and c.get("/market/NIFTY").status_code != 200:
            time.sleep(0.1)
        yield c


def test_status_before_the_runner_starts(client):
    s = client.get("/paper/status").json()
    assert s["running"] is False and s["broker"] == "paper" and s["data_source"] == "simulated"
    assert s["risk"]["kill_switch"]["active"] is False and s["risk"]["limits"]["max_daily_loss"] == 5000
    assert client.get("/paper/positions").json() == {"positions": []}


def test_kill_switch_works_without_a_runner_and_is_audited(client):
    k = client.post("/paper/kill", json={"reason": "api test"}).json()
    assert k["active"] and k["reason"] == "api test"
    assert client.get("/paper/risk").json()["kill_switch"]["active"]
    assert not client.post("/paper/kill/reset").json()["active"]
    types = [e["event_type"] for e in client.get("/paper/events").json()["events"]]
    assert "kill_switch.tripped" in types and "kill_switch.reset" in types


def test_strategy_can_be_disabled(client):
    assert client.post("/paper/strategies/momentum/enabled", params={"enabled": False}).json()["enabled"] is False
    assert client.get("/paper/risk").json()["disabled_strategies"] == ["momentum"]
    client.post("/paper/strategies/momentum/enabled", params={"enabled": True})


def test_journal_and_summary_start_empty_and_accept_filters(client):
    j = client.get("/paper/journal", params={"direction": "LONG", "result": "win", "day": "2026-10-21"}).json()
    assert j["trades"] == [] and j["summary"]["trades"] == 0
    s = client.get("/paper/summary").json()
    assert s["trades"] == 0 and s["net_pnl"] == 0
    assert client.get("/paper/trace/nope").status_code == 404


def test_runner_refuses_to_trade_a_closed_market_but_starts_cleanly(client):
    s = client.post("/paper/start", json={"underlyings": ["NIFTY"]})
    assert s.status_code == 200 and s.json()["running"]
    assert client.post("/paper/start").status_code == 400  # already running
    assert client.post("/paper/stop").json() == {"running": False}


def test_runner_starts_a_saved_strategy_on_its_own_timeframe(client):
    from trader.strategy.library import LIBRARY

    def save(raw):
        return client.post("/strategies", json={"spec": raw, "source_text": "t"}).json()["id"]

    five = next(s for s in LIBRARY if s["timeframe"] == "5m")
    fifteen = next(s for s in LIBRARY if s["timeframe"] == "15m")
    bad = client.post("/paper/start", json={"strategy_id": save(fifteen)})
    assert bad.status_code == 400 and "15m" in bad.json()["detail"]  # not built live: refused, not silently 5m
    ok = client.post("/paper/start", json={"strategy_id": save(five), "underlyings": ["NIFTY"]})
    assert ok.status_code == 200 and ok.json()["lanes"][0]["timeframe"] == "5m"
    client.post("/paper/stop")


def test_no_endpoint_accepts_an_order(client):
    """Orders come only from the runner via the risk engine; the API has nothing that takes one."""
    paths = client.get("/openapi.json").json()["paths"]
    posts = {p for p, ops in paths.items() if "post" in ops}
    assert "/paper/start" in posts  # the schema really lists the routes, so the checks below are not vacuous
    assert not [p for p in posts if "order" in p or "buy" in p or "sell" in p]
    assert {p for p in posts if p.startswith("/paper")} == {
        "/paper/start", "/paper/stop", "/paper/kill", "/paper/kill/reset", "/paper/strategies/{name}/enabled"}


def test_llm_code_cannot_reach_order_placement():
    """PRD §2: nothing under trader/ai may import the execution, risk or runner modules."""
    banned = ("trader.execution", "trader.risk", "trader.brokers")
    # trader/research runs the AI research loop (PRD §24.10): it must not reach orders either
    for f in [*(TRADER / "ai").glob("*.py"), *(TRADER / "research").glob("*.py")]:
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [node.module] if isinstance(node, ast.ImportFrom) and node.module else \
                [a.name for a in node.names] if isinstance(node, ast.Import) else []
            assert not [m for m in mods if m.startswith(banned)], f"{f.name} imports {mods}"


def test_only_the_paper_broker_exists_and_live_is_still_guarded():
    from trader.execution import Broker
    from trader.execution.paper import PaperBroker

    assert PaperBroker.is_live is False
    assert [c.__name__ for c in Broker.__subclasses__()] == ["PaperBroker"]
