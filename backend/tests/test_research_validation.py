"""B5 (PRD §24.6): sweeps, walk-forward, the holdout, and the statistics that keep them honest."""

from __future__ import annotations

import math
import random
import time
from datetime import date, datetime, timedelta

import pytest

from tests.test_backtest_api import END, client  # noqa: F401 - module fixture: 15 simulated days
from trader.backtest.runner import BacktestRequest
from trader.research import stats
from trader.research import validation as V


# ---- statistics ---------------------------------------------------------------------------------------------------
def test_expected_max_sharpe_matches_a_monte_carlo_of_lucky_strategies():
    rng = random.Random(1)
    n, sd = 50, 0.1
    sims = [max(rng.gauss(0, sd) for _ in range(n)) for _ in range(4000)]
    mc = sum(sims) / len(sims)
    assert stats.expected_max_sharpe(n, sd * sd) == pytest.approx(mc, rel=0.05)
    assert stats.expected_max_sharpe(1, sd * sd) == 0.0


def test_deflated_sharpe_is_the_probabilistic_sharpe_for_one_trial_and_falls_with_more_trials():
    rng = random.Random(2)
    rets = [rng.gauss(0.001, 0.01) for _ in range(500)]
    one = stats.deflated_sharpe(rets, 1, 0.01)
    m, sd, sk, ku = stats.moments(rets)
    assert one["dsr"] == pytest.approx(stats.probabilistic_sharpe(m / sd, 0.0, 500, sk, ku), abs=1e-4)
    many = [stats.deflated_sharpe(rets, n, 0.01)["dsr"] for n in (1, 10, 100, 1000)]
    assert many == sorted(many, reverse=True) and many[-1] < many[0]


def test_noise_chosen_from_many_trials_is_rejected_and_a_real_edge_is_not():
    rng = random.Random(3)
    trials = [[rng.gauss(0, 0.01) for _ in range(250)] for _ in range(200)]  # 200 settings of pure noise
    srs = [stats.sharpe(t) for t in trials]
    best = trials[max(range(200), key=lambda i: srs[i])]
    _, sd, _, _ = stats.moments(srs)
    assert stats.sharpe(best) > 0.15  # looks great on its own...
    assert stats.deflated_sharpe(best, 200, sd * sd)["dsr"] < 0.95  # ...but not after 200 tries
    edge = [rng.gauss(0.004, 0.01) for _ in range(250)]
    assert stats.deflated_sharpe(edge, 200, sd * sd)["dsr"] > 0.95


def test_bootstrap_spread_and_drawdown():
    b = stats.bootstrap([100.0, -50.0, 80.0, -20.0, 10.0] * 20, n=500)
    assert b["net_pnl"]["p5"] < b["net_pnl"]["p50"] < b["net_pnl"]["p95"] and 0 <= b["prob_loss"] <= 1
    assert stats.max_drawdown([10, -30, 5, -10, 40]) == 35


def test_a_profitable_point_with_a_losing_neighbour_is_fragile():
    grid = {(1,): 0.5, (2,): 3.0, (3,): -1.0, (4,): 2.0, (5,): 1.9}
    nb = stats.neighbourhood(grid, {0: [1, 2, 3, 4, 5]})
    assert nb[(2,)]["fragile"] and nb[(5,)]["fragile"] is False and nb[(4,)]["fragile"]


# ---- parameters, folds, holdout --------------------------------------------------------------------------------------
def test_parameters_apply_to_a_copy_and_typos_are_refused():
    base = BacktestRequest(spec={"exits": {"stop_loss_pct": 25}, "sides": {"long": {"entry": {"all": [{"cmp": [{"ind": "rsi", "period": 14}, ">", {"const": 55}]}]}}}})
    r = V.apply(base, {"spec.exits.stop_loss_pct": 30, "spec.sides.long.entry.all.0.cmp.2.const": 60})
    assert r.spec["exits"]["stop_loss_pct"] == 30 and r.spec["sides"]["long"]["entry"]["all"][0]["cmp"][2]["const"] == 60
    assert base.spec["exits"]["stop_loss_pct"] == 25  # untouched
    with pytest.raises(ValueError):
        V.apply(base, {"spec.exits.stopp_loss": 1})
    with pytest.raises(ValueError):
        V.apply(base, {"bogus.x": 1})
    assert V.Param.from_dict({"path": "x", "min": 10, "max": 30, "step": 10}).values == [10, 20, 30]


def test_tunables_list_exits_constants_and_periods():
    from trader.strategy.spec import MOMENTUM_V1

    t = {x["path"]: x for x in V.tunables(MOMENTUM_V1)}
    assert "spec.exits.stop_loss_pct" in t
    consts = [x for x in t.values() if x["path"].endswith(".const")]
    periods = [x for x in t.values() if x["path"].endswith(".period")]
    assert any(x["value"] == 55 and "RSI" in x["label"] for x in consts) and periods


def test_walk_forward_test_windows_follow_each_other_and_never_overlap_training():
    spec = V.WalkForwardSpec([], train_days=100, test_days=30, embargo_days=1)
    folds = V.plan_folds(date(2024, 1, 1), date(2024, 12, 31), spec)
    assert len(folds) >= 7
    for (a, b, c, d), nxt in zip(folds, folds[1:] + [None]):
        assert a <= b < c <= d and (c - b).days == 2  # one embargo day between training and test
        if nxt:
            assert nxt[2] == d + timedelta(days=1)  # test windows back to back
    anchored = V.plan_folds(date(2024, 1, 1), date(2024, 12, 31), V.WalkForwardSpec([], 100, 30, anchored=True))
    assert all(f[0] == date(2024, 1, 1) for f in anchored) and anchored[-1][1] > anchored[0][1]


# ---- end to end through the API -------------------------------------------------------------------------------------
def _wait(client, job_id, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/research/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.2)
    raise TimeoutError(job_id)


def test_sweep_job_runs_every_setting_and_never_touches_the_holdout(client):
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(),
            "strategy_params": {"adx_min": 5},
            "params": [{"path": "strategy_params.stop_loss_pct", "values": [15, 30]},
                       {"path": "strategy_params.target_pct", "values": [40, 80]}],
            "min_trades": 1, "holdout_days": 4}
    r = client.post("/research/sweep", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["runs"] == 5
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    res = j["result"]
    assert res["trials"] == 4 and len(res["heatmap"]["z"]) == 2 and res["best"]["params"]
    cut = date.fromisoformat(res["holdout"]["from"])
    assert date.fromisoformat(res["window"]["to"]) < cut
    assert "deflated_sharpe" in res and "bootstrap" in res and res["verdict"]
    assert any(x["id"] == j["id"] for x in client.get("/research/jobs").json())


def test_walk_forward_job_reports_out_of_sample_results(client):
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "strategy_params": {"adx_min": 5},
            "params": [{"path": "strategy_params.stop_loss_pct", "values": [15, 30]}],
            "train_days": 10, "test_days": 5, "min_trades": 1}
    r = client.post("/research/walk-forward", json=body)
    assert r.status_code == 200, r.text
    j = _wait(client, r.json()["job_id"])
    assert j["status"] == "done", j.get("error")
    res = j["result"]
    assert res["folds"] and all("test" in f for f in res["folds"])
    assert "trades" in res["out_of_sample"] and res["verdict"]


def test_holdout_can_be_checked_once_per_setting(client):
    body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "strategy_params": {"adx_min": 5},
            "assign": {"strategy_params.stop_loss_pct": 20}, "holdout_days": 6}
    first = client.post("/research/holdout", json=body)
    assert first.status_code == 200, first.text
    j = _wait(client, first.json()["job_id"])
    assert j["status"] == "done" and j["result"]["holdout"]["days"] == 6
    again = client.post("/research/holdout", json=body)
    assert again.status_code == 409 and again.json()["detail"]["code"] == "holdout_used"
    other = client.post("/research/holdout", json=body | {"assign": {"strategy_params.stop_loss_pct": 25}})
    assert other.status_code == 200


def test_bad_requests_are_refused_before_anything_runs(client):
    base = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat()}
    assert client.post("/research/sweep", json=base | {"params": [{"path": "strategy_params.nope", "values": [1]}]}).status_code == 422
    assert client.post("/research/sweep", json=base | {"params": [{"path": "bogus.x", "values": [1]}]}).status_code == 422
    big = [{"path": f"strategy_params.{k}", "values": list(range(1, 11))} for k in ("rsi_long", "rsi_short", "adx_min")]
    assert client.post("/research/sweep", json=base | {"params": big}).status_code == 422  # 1000 combinations
    assert client.post("/research/tunables", json={}).json()[0]["path"].startswith("strategy_params.")


async def test_a_job_reserves_its_runs_against_the_plan(tmp_path):
    import asyncio

    from trader.billing.meter import Meter, QuotaExceeded
    from trader.db.migrate import upgrade_db
    from trader.db.session import create_engine, create_sessionmaker
    from trader.accounts.repo import Principal

    url = f"sqlite+aiosqlite:///{(tmp_path / 'm.db').as_posix()}"
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    meter = Meter(create_sessionmaker(engine))
    p = Principal(id="u1", email="", name="", is_admin=False, plan="trial", period_start=None, period_end=None)
    with pytest.raises(QuotaExceeded):
        await meter.reserve(p, "backtests", 10_000)
    hold = await meter.reserve(p, "backtests", 5)
    await meter.adjust(hold, 2)
    s = await meter.summary(p)
    assert s["usage"]["backtests"]["used"] == 2
    await engine.dispose()
