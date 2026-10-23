"""AI research loop (PRD §24.10 B9): the model proposes, the engine judges.

1. Walk-forward the strategy as it is (the baseline), on the days before the held-back period.
2. Show the model a summary of that result (out-of-sample metrics, where it made and lost money) and ask for variants.
3. Walk-forward every valid variant on exactly the same folds.
4. Judge in code: a variant is **accepted** only with enough out-of-sample trades, a profit factor above 1 and above the
   baseline's, and a Deflated Sharpe of at least 0.95 counting every proposal as a trial; **promising** if it beats the
   baseline but misses the evidence bar; otherwise **rejected**. The reason is always stated.

The held-back days are never shown to the model or used here; a variant reaches paper trading only if the owner saves it
and enables it. This package does not import execution, risk or brokers.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from trader.backtest import data as bt_data
from trader.backtest.runner import BacktestRequest
from trader.research import stats
from trader.research import validation as V

DSR_BAR = 0.95


def summarize(wf: dict) -> dict:
    """What the model sees: out-of-sample results and where they came from. No curves, no trades, no holdout days."""
    oos = wf.get("out_of_sample", {})
    keep = ("trades", "total_net_pnl", "profit_factor", "win_rate", "sharpe", "max_drawdown", "expectancy")
    regimes = {k: {v: {x: g.get(x) for x in ("trades", "net_pnl", "profit_factor")} for v, g in groups.items()}
               for k, groups in (oos.get("by_regime") or {}).items()}
    return {"out_of_sample": {k: oos.get(k) for k in keep}, "exit_reasons": oos.get("exit_reasons"),
            "by_market_condition": regimes, "highlights": oos.get("regime_highlights"),
            "in_sample_average": wf.get("in_sample_average"), "verdict": wf.get("verdict"),
            "walk_forward": wf.get("layout")}


def judge(oos: dict, dsr: float, baseline_pf: float | None, min_trades: int) -> tuple[str, str]:
    trades, pf = oos.get("trades", 0), oos.get("profit_factor")
    if trades < min_trades:
        return "rejected", f"only {trades} out-of-sample trades (at least {min_trades} needed)"
    if pf is None or pf <= 1:
        return "rejected", f"out-of-sample profit factor {pf}: no edge"
    if baseline_pf is not None and pf <= baseline_pf:
        return "rejected", f"profit factor {pf} does not beat the original's {baseline_pf}"
    if dsr < DSR_BAR:
        return "promising", (f"beats the original (profit factor {pf} vs {baseline_pf}) but the deflated Sharpe "
                             f"{dsr:.2f} is below {DSR_BAR} after counting every proposal: test it further")
    return "accepted", f"profit factor {pf} vs {baseline_pf}, deflated Sharpe {dsr:.2f}"


def run(base: BacktestRequest, data: bt_data.BacktestData, wf_spec: V.WalkForwardSpec,
        ask: Callable[[dict, dict, int], object], n: int, min_trades: int = V.MIN_EVIDENCE_TRADES,
        progress: Callable[[int, int, str], None] | None = None, cancelled: Callable[[], bool] | None = None) -> dict:
    """`ask(spec, summary, n)` returns `ai.research_assistant.Proposals` (the caller meters that model call)."""
    folds = len(V.plan_folds(base.start, V.effective_end(base.end, wf_spec.holdout_days)[0], wf_spec))
    total = folds * (n + 1)
    done = [0]

    def step(note: str) -> Callable[[int, int, str], None] | None:
        if not progress:
            return None
        start = done[0]
        return lambda d, _t, _n="": progress(start + d, total, note)

    spec0 = replace(wf_spec, params=[])
    baseline = V.run_walk_forward(base, data, spec0, step("original strategy"), cancelled)
    done[0] += folds
    summary = summarize(baseline)
    proposals = ask(base.spec or {}, summary, n)
    results: list[dict] = []
    for i, p in enumerate(proposals.items):  # type: ignore[attr-defined]
        row = {"hypothesis": p.hypothesis, "name": (p.spec or {}).get("name"), "spec": p.spec, "errors": p.errors}
        if p.spec is None:
            row.update(status="invalid", reason="; ".join(p.errors[:3]))
            results.append(row)
            continue
        if cancelled and cancelled():
            break
        try:
            wf = V.run_walk_forward(replace(base, strategy="spec", spec=p.spec, strategy_version_id=None), data, spec0,
                                    step(f"variant {i + 1}/{len(proposals.items)}"), cancelled)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - one broken variant must not end the research
            row.update(status="invalid", reason=f"{type(exc).__name__}: {exc}")
            results.append(row)
            continue
        done[0] += folds
        row.update(out_of_sample={k: wf["out_of_sample"].get(k) for k in ("trades", "total_net_pnl", "profit_factor",
                                                                          "win_rate", "sharpe", "max_drawdown")},
                   highlights=wf["out_of_sample"].get("regime_highlights"), _daily=wf["daily_pnl"],
                   _capital=wf["capital"], equity_curve=wf["out_of_sample"].get("equity_curve"))
        results.append(row)
    # Every proposal counts as a trial, the original included: the more ideas tried, the higher the bar.
    tested = [r for r in results if "_daily" in r]
    trials = 1 + len([r for r in results if r.get("spec")])
    srs = [stats.sharpe([x / r["_capital"] for x in r["_daily"]]) for r in tested]
    srs.append(stats.sharpe([x / baseline["capital"] for x in baseline["daily_pnl"]]))
    var = stats.moments(srs)[1] ** 2 if len(srs) > 1 else 0.0
    base_pf = baseline["out_of_sample"].get("profit_factor")
    for r in tested:
        r["deflated_sharpe"] = stats.deflated_sharpe([x / r["_capital"] for x in r.pop("_daily")], trials, var)
        r.pop("_capital")
        r["status"], r["reason"] = judge(r["out_of_sample"], r["deflated_sharpe"]["dsr"], base_pf, min_trades)
    order = {"accepted": 0, "promising": 1, "rejected": 2, "invalid": 3}
    results.sort(key=lambda r: (order.get(r.get("status"), 4), -((r.get("out_of_sample") or {}).get("profit_factor") or 0)))
    return {
        "baseline": {**summary, "deflated_sharpe": stats.deflated_sharpe(
            [x / baseline["capital"] for x in baseline["daily_pnl"]], trials, var),
            "equity_curve": baseline["out_of_sample"].get("equity_curve")},
        "variants": results, "trials": trials, "holdout": baseline.get("holdout"),
        "model": getattr(proposals, "model", ""), "prompt": getattr(proposals, "prompt", ""),
        "reply": getattr(proposals, "reply", ""), "folds": folds,
        "verdict": _headline(results),
    }


def _headline(results: list[dict]) -> str:
    acc = [r for r in results if r.get("status") == "accepted"]
    pro = [r for r in results if r.get("status") == "promising"]
    if acc:
        return f"{len(acc)} variant(s) passed every check out-of-sample, best: {acc[0]['name']}. Confirm on the holdout."
    if pro:
        return f"No variant passed every check; {len(pro)} beat the original but need more evidence."
    return "No variant beat the original out-of-sample."

