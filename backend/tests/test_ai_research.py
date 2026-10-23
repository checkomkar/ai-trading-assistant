"""B9 (PRD §24.10): the model proposes variants, the engine judges them; the model never sees the holdout."""

from __future__ import annotations

import copy
import json
import time
from datetime import timedelta

import pytest

from tests.test_backtest_api import END, client  # noqa: F401 - module fixture: 15 simulated days
from trader.ai.providers import LLMReply
from trader.ai.research_assistant import propose
from trader.research import ai_loop
from trader.strategy.spec import MOMENTUM_V1


def variant(name_suffix: str, adx: float) -> dict:
    v = copy.deepcopy(MOMENTUM_V1)
    v["name"] = f"{MOMENTUM_V1['name']} {name_suffix}"
    for c in v["filters"]["all"]:
        if c.get("cmp", [{}])[0].get("ind") == "adx":
            c["cmp"][2]["const"] = adx
    return v


class FakeModel:
    name, model, label = "fake", "fake-1", "fake/fake-1"

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, system, messages, schema, tools=None):  # noqa: ANN001
        self.calls.append({"system": system, "messages": messages})
        stocks = copy.deepcopy(MOMENTUM_V1) | {"market": "equity"}
        body = {"variants": [
            {"hypothesis": "A stronger trend filter avoids choppy losses.", "spec_json": json.dumps(variant("(ADX 10)", 10))},
            {"hypothesis": "Broken JSON.", "spec_json": "{not json"},
            {"hypothesis": "Trade stocks instead.", "spec_json": json.dumps(stocks)},
        ]}
        return LLMReply(json.dumps(body), 1000, 200, self.label)


def test_proposals_are_validated_like_any_human_spec():
    p = propose(FakeModel(), MOMENTUM_V1, {"out_of_sample": {"trades": 10}}, 3)
    assert [x.spec is not None for x in p.items] == [True, False, False]
    assert p.items[0].spec["name"].endswith("(ADX 10)") and p.items[1].errors and p.items[2].errors


def test_judging_is_code_not_the_model():
    assert ai_loop.judge({"trades": 50, "profit_factor": 2.0}, 0.99, 1.0, 100)[0] == "rejected"  # too few trades
    assert ai_loop.judge({"trades": 200, "profit_factor": 0.9}, 0.99, 0.8, 100)[0] == "rejected"  # no edge
    assert ai_loop.judge({"trades": 200, "profit_factor": 1.1}, 0.99, 1.2, 100)[0] == "rejected"  # worse than original
    assert ai_loop.judge({"trades": 200, "profit_factor": 1.3}, 0.5, 1.1, 100)[0] == "promising"
    assert ai_loop.judge({"trades": 200, "profit_factor": 1.3}, 0.97, 1.1, 100)[0] == "accepted"


def test_ai_research_job_tests_every_valid_variant_and_never_shows_the_holdout(client, monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr("trader.ai.providers.build_provider", lambda settings, is_admin=False: fake)
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "strategy": "momentum",
            "n_variants": 3, "train_days": 10, "test_days": 5, "holdout_days": 30, "min_trades": 1}
    r = client.post("/research/ai", json=body)
    assert r.status_code == 422 and "too short" in r.text  # a 30-day holdout leaves nothing before it in a 30-day range
    body |= {"start": (END - timedelta(days=60)).isoformat()}
    r = client.post("/research/ai", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    end = time.time() + 120
    while time.time() < end:
        j = client.get(f"/research/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.2)
    assert j["status"] == "done", j.get("error")
    res = j["result"]
    assert res["trials"] == 2  # the original + the one valid proposal
    statuses = {v["hypothesis"][:12]: v["status"] for v in res["variants"]}
    assert statuses["Broken JSON."] == "invalid" and statuses["Trade stocks"] == "invalid"
    tested = next(v for v in res["variants"] if v["spec"])
    assert tested["status"] in ("accepted", "promising", "rejected") and tested["reason"]
    assert "deflated_sharpe" in tested and "out_of_sample" in tested
    # the model saw a summary of the days before the holdout, never the holdout itself
    holdout_from = res["holdout"]["from"]
    sent = fake.calls[0]["messages"][0]["content"]
    assert holdout_from not in sent and "equity_curve" not in sent and len(fake.calls) == 1
    assert res["prompt"] == sent and res["model"] == "fake/fake-1"
