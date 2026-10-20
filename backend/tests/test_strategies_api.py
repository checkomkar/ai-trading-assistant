import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from trader.ai import strategy_translator as st
from trader.ai.strategy_translator import StrategyTranslator, TranslatorUnavailable
from trader.api.main import create_app
from trader.config import Settings
from trader.db.migrate import upgrade_db
from trader.strategy.spec import MOMENTUM_V1
from tests.test_backtest_api import END, _seed


# ---- translator with a fake Claude client ------------------------------------------------------
class FakeClaude:
    """Returns queued envelopes; records the requests it was sent."""

    def __init__(self, *envelopes, stop_reason="end_turn"):
        self.envelopes = list(envelopes)
        self.calls = []
        self.stop_reason = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        env = self.envelopes.pop(0)
        block = SimpleNamespace(type="text", text=json.dumps(env))
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason)


def env(spec=None, assumptions=(), questions=(), unsupported=()):
    return {"spec_json": json.dumps(spec) if spec else "", "assumptions": list(assumptions),
            "questions": list(questions), "unsupported": list(unsupported)}


def test_translate_valid_spec():
    fake = FakeClaude(env(MOMENTUM_V1, assumptions=["RSI period 14"]))
    out = StrategyTranslator("claude-opus-5-5", client=fake).translate("momentum on nifty ...")
    assert out.spec["name"] == MOMENTUM_V1["name"] and out.assumptions == ["RSI period 14"]
    assert "Buy the ATM call" in out.english and not out.repaired and out.errors == []
    req = fake.calls[0]
    assert req["model"] == "claude-opus-5-5" and req["fallbacks"] == "default"
    assert req["output_config"]["format"]["type"] == "json_schema"


def test_translate_repairs_once_with_validation_errors():
    bad = dict(MOMENTUM_V1, sides={"long": {"option": "CE", "entry": {"cmp": [{"ind": "close"}, ">", {"ind": "vwap"}]}}})
    fake = FakeClaude(env(bad), env(MOMENTUM_V1))
    out = StrategyTranslator("m", client=fake).translate("price above vwap ...")
    assert out.repaired and out.spec is not None and out.errors == []
    second = fake.calls[1]["messages"]
    assert second[0]["role"] == "user" and second[1]["role"] == "assistant"  # append-only history
    assert "futures basis" in second[2]["content"]  # the validation error was fed back


def test_translate_returns_questions_without_guessing():
    fake = FakeClaude(env(questions=["Breakout of what: previous day's high or opening range?"]))
    out = StrategyTranslator("m", client=fake).translate("buy on breakout")
    assert out.spec is None and out.questions and len(fake.calls) == 1


def test_translate_still_invalid_after_repair_reports_errors():
    bad = dict(MOMENTUM_V1, exits={"stop_loss_pct": 150, "target_pct": 50})
    out = StrategyTranslator("m", client=FakeClaude(env(bad), env(bad))).translate("x" * 10)
    assert out.spec is None and out.errors


def test_translate_refusal_raises():
    with pytest.raises(TranslatorUnavailable):
        StrategyTranslator("m", client=FakeClaude(env(MOMENTUM_V1), stop_reason="refusal")).translate("hello there")


def test_system_prompt_mentions_rules():
    for phrase in ("fut_close", "mirror_of", "unsupported", "questions"):
        assert phrase in st.SYSTEM_PROMPT


# ---- API ------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(tmp_path_factory):
    url = f"sqlite+aiosqlite:///{(tmp_path_factory.mktemp('st') / 'st.db').as_posix()}"
    upgrade_db(url)
    asyncio.run(_seed(url))
    settings = Settings(_env_file=None, database_url=url, sim_seed=2, sim_tick_interval_seconds=0.2,
                        sim_backfill_minutes=0, anthropic_api_key="")
    with TestClient(create_app(settings)) as c:
        yield c


def test_validate_and_template(client):
    t = client.get("/strategies/template").json()
    assert t["spec"]["name"] and "Buy the ATM call" in t["english"]
    v = client.post("/strategies/validate", json={"spec": t["spec"]}).json()
    assert v["valid"] and v["spec_hash"]
    bad = client.post("/strategies/validate", json={"spec": {**t["spec"], "timeframe": "7m"}}).json()
    assert not bad["valid"] and any("timeframe" in e for e in bad["errors"])


def test_save_versions_and_dedupe(client):
    spec = dict(MOMENTUM_V1, name="API test strategy")
    v1 = client.post("/strategies", json={"spec": spec, "source_text": "describe"}).json()
    assert v1["version"] == 1
    same = client.post("/strategies", json={"spec": spec}).json()
    assert same["id"] == v1["id"]  # unchanged spec -> no new version
    v2 = client.post("/strategies", json={"spec": {**spec, "risk": {"max_trades_per_day": 1}}}).json()
    assert v2["version"] == 2 and v2["spec_hash"] != v1["spec_hash"]
    names = [s["name"] for s in client.get("/strategies").json()]
    assert names.count("API test strategy") == 1  # latest version only
    hist = client.get(f"/strategies/{v2['id']}/versions").json()
    assert [h["version"] for h in hist] == [2, 1]
    assert client.post("/strategies", json={"spec": {**spec, "exits": {"stop_loss_pct": 0, "target_pct": 1}}}).status_code == 422


def test_backtest_saved_strategy(client):
    spec = dict(MOMENTUM_V1, name="Backtest me", filters={"time_window": ["09:30", "14:45"], "all": [
        {"cmp": [{"ind": "adx", "period": 14}, ">=", {"const": 5}]}]})
    saved = client.post("/strategies", json={"spec": spec}).json()
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "strategy_id": saved["id"]}
    r = client.post("/backtests", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["params"]["strategy"]["spec_hash"] == saved["spec_hash"]
    assert res["params"]["strategy"]["strategy_version_id"] == saved["id"]
    assert client.get("/backtests").json()[0]["strategy"].startswith("spec:")
    assert client.post("/backtests", json={**body, "strategy_id": "nope"}).status_code == 404


def test_translate_endpoint_without_credentials(client, monkeypatch):
    def boom(self, text, base_spec=None):
        raise TranslatorUnavailable("No Anthropic credentials. Set ANTHROPIC_API_KEY in .env")
    monkeypatch.setattr(StrategyTranslator, "translate", boom)
    r = client.post("/strategies/translate", json={"text": "buy calls on breakout"})
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_missing_credentials_become_translator_unavailable():
    class NoAuth(FakeClaude):
        def _create(self, **kw):
            raise TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token..."')

    with pytest.raises(TranslatorUnavailable, match="ANTHROPIC_API_KEY"):
        StrategyTranslator("m", client=NoAuth()).translate("buy calls on a breakout")
