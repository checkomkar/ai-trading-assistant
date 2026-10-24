"""Backtest chart indicators (PRD §24.3 B2): the series are what the strategy saw, plus searchable extras."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

from tests.test_backtest_api import END, client  # noqa: F401 - module fixture with 15 simulated days
from trader.strategy.spec import MOMENTUM_V1

START = (END - timedelta(days=30)).isoformat()


def _at(points, iso):
    t = int(datetime.fromisoformat(iso).timestamp())
    return next(v for ts, v in points if ts == t)


def test_momentum_chart_carries_its_indicators_with_the_values_the_strategy_used(client):
    res = client.post("/backtests", json={"start": START, "end": END.isoformat(), "strategy_params": {"adx_min": 5}}).json()
    assert res["trades"]
    ch = client.get(f"/backtests/{res['id']}/chart").json()
    assert ch["exact"] is True and ch["run_timeframe"] == "5m"
    by = {s["id"]: s for s in ch["series"]}
    assert {"ema|9||", "ema|21||", "rsi|14||", "adx|14||", "vwap|||", "fut_close|||"} <= set(by)
    rsi = by["rsi|14||"]
    assert rsi["pane"] == "oscillator 0-100" and rsi["thresholds"] == [45.0, 55.0] and rsi["source"] == "strategy"
    assert by["vwap|||"]["pane"] == "futures" and by["ema|9||"]["pane"] == "price"
    for t in res["trades"]:  # the chart's value at the signal bar is the one the signal was decided on
        sig_bar = (datetime.fromisoformat(t["signal_time"])).isoformat()
        assert _at(rsi["points"], sig_bar) == round(t["features"]["rsi"], 4)
        assert _at(by["ema|9||"]["points"], sig_bar) == round(t["features"]["ema_fast"], 4)


def test_spec_chart_has_its_operands_extras_and_entry_checks(client):
    spec = copy.deepcopy(MOMENTUM_V1)
    spec["filters"]["all"].append({"cmp": [{"ind": "close"}, ">", {"ind": "wma", "period": 20}]})
    body = {"strategy": "spec", "spec": spec, "start": START, "end": END.isoformat()}
    r = client.post("/backtests", json=body)
    assert r.status_code == 200, r.text
    res = r.json()
    ch = client.get(f"/backtests/{res['id']}/chart", params={"extra": "bb_upper:20,supertrend:10:3"}).json()
    ids = {s["id"]: s for s in ch["series"]}
    assert "wma|20||" in ids and ids["wma|20||"]["points"]  # a wma operand once crashed the interpreter
    assert ids["adx|14||"]["thresholds"] == [18.0]
    assert ids["bb_upper|20||"]["source"] == "user" and ids["supertrend|10|3|"]["points"]
    for t in res["trades"]:
        assert t["checks"] and all(len(c) == 2 for c in t["checks"])
        assert all(ok is True for _, ok in t["checks"])  # every condition held when the entry fired
    other = client.get(f"/backtests/{res['id']}/chart", params={"timeframe": "15m"}).json()
    assert other["exact"] is False and other["series"]


def test_extras_are_validated(client):
    res = client.post("/backtests", json={"start": START, "end": END.isoformat()}).json()
    assert client.get(f"/backtests/{res['id']}/chart", params={"extra": "nosuch:5"}).status_code == 422
    assert client.get(f"/backtests/{res['id']}/chart", params={"extra": "rsi:1000"}).status_code == 422


def test_indicator_search_and_saved_layout(client):
    found = client.get("/indicators", params={"q": "boll"}).json()
    assert {"bb_upper", "bb_lower", "bb_mid"} <= {e["name"] for e in found}
    allx = client.get("/indicators").json()
    assert len(allx) >= 99 and all(e["category"] != "Other" for e in allx)
    cpr = client.get("/indicators", params={"q": "cpr"}).json()
    assert [e["name"] for e in cpr] == ["cpr_tc", "cpr_bc"]
    assert client.get("/prefs/chart-indicators").json()["value"] is None
    layout = [{"name": "ema", "period": 50, "color": "#a78bfa"}]
    assert client.put("/prefs/chart-indicators", json={"value": layout}).status_code == 200
    assert client.get("/prefs/chart-indicators").json()["value"] == layout
    assert client.put("/prefs/BAD KEY", json={"value": []}).status_code in (400, 404)


def test_a_repeated_chart_is_served_from_memory_and_identical(client, monkeypatch):
    from trader.api.routes import backtests as route

    res = client.post("/backtests", json={"start": START, "end": END.isoformat(), "strategy_params": {"adx_min": 6}}).json()
    first = client.get(f"/backtests/{res['id']}/chart").json()
    loads = []
    real_load = route.bt_data.load

    async def counting(*a, **k):
        loads.append(a)
        return await real_load(*a, **k)

    monkeypatch.setattr(route.bt_data, "load", counting)
    again = client.get(f"/backtests/{res['id']}/chart").json()
    with_extra = client.get(f"/backtests/{res['id']}/chart", params={"extra": "sma:50"}).json()
    assert loads == [] and again == first
    assert len(with_extra["series"]) == len(first["series"]) + 1  # only the new series was computed
