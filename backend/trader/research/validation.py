"""Parameter sweeps and walk-forward validation (PRD §24.6 B5).

Both work on one dataset loaded once (`research.parallel`), and both are honest about selection:

* a **sweep** tries every setting in a grid (or a random / Optuna sample of it), shows the stability of each result
  next to its score, and deflates the best one's Sharpe by the number of settings tried;
* a **walk-forward** chooses the setting on each training window only and runs it unchanged on the following test
  window; the headline is the stitched test-period result, never the in-sample one.

The most recent `holdout_days` can be reserved: nothing here reads them, and `holdout_check` runs a chosen setting on
them once per strategy version and setting.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import random
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Callable

from trader.backtest import data as bt_data
from trader.backtest.engine import Trade
from trader.backtest.metrics import compute_metrics
from trader.backtest.runner import WARMUP_DAYS, BacktestRequest, execute
from trader.instruments.calendar import IST
from trader.research import stats
from trader.research.parallel import run_many

OBJECTIVES = ("profit_factor", "total_net_pnl", "sharpe", "expectancy")
MAX_VALUES_PER_PARAM = 25
MAX_RUNS = 500
MIN_EVIDENCE_TRADES = 100

Progress = Callable[[int, int, str], None]


# ---- parameters ---------------------------------------------------------------------------------------------------
@dataclass
class Param:
    path: str  # "strategy_params.rsi_long", "engine_params.trail_pct", "spec.exits.stop_loss_pct", "spec.sides.long.entry.all.2.cmp.2.const"
    values: list

    @classmethod
    def from_dict(cls, d: dict) -> "Param":
        if "values" in d and d["values"]:
            vals = list(d["values"])
        else:
            lo, hi, step = float(d["min"]), float(d["max"]), float(d["step"])
            if step <= 0 or hi < lo:
                raise ValueError(f"{d['path']}: need min <= max and step > 0")
            n = int(math.floor((hi - lo) / step + 1e-9)) + 1
            vals = [round(lo + i * step, 6) for i in range(n)]
            if all(float(v).is_integer() for v in vals) and float(step).is_integer():
                vals = [int(v) for v in vals]
        if not vals or len(vals) > MAX_VALUES_PER_PARAM:
            raise ValueError(f"{d['path']}: between 1 and {MAX_VALUES_PER_PARAM} values")
        return cls(str(d["path"]), vals)


def _set(obj: Any, parts: list[str], value: Any) -> None:
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        key: Any = int(p) if isinstance(obj, list) else p
        if last:
            if isinstance(obj, list):
                obj[key] = value
            elif key in obj:
                obj[key] = value
            else:
                raise KeyError(".".join(parts))  # only existing numbers can be varied
        else:
            if isinstance(obj, dict) and key not in obj:
                raise KeyError(".".join(parts[: i + 1]))
            obj = obj[key]


def apply(req: BacktestRequest, assign: dict[str, Any]) -> BacktestRequest:
    """A copy of `req` with each path set. Unknown paths raise (a typo must not silently test nothing)."""
    r = replace(req, strategy_params=copy.deepcopy(req.strategy_params), engine_params=copy.deepcopy(req.engine_params),
                execution_params=copy.deepcopy(req.execution_params), spec=copy.deepcopy(req.spec))
    for path, v in assign.items():
        head, _, rest = path.partition(".")
        if head not in ("strategy_params", "engine_params", "execution_params", "spec") or not rest:
            raise ValueError(f"cannot set {path}")
        target = getattr(r, head)
        if target is None:
            raise ValueError(f"{path}: this run has no {head}")
        if head != "spec":
            target[rest] = v  # params dicts accept new keys (validated when the run is built)
        else:
            try:
                _set(target, rest.split("."), v)
            except (KeyError, IndexError, ValueError, TypeError):
                raise ValueError(f"{path} does not exist in this strategy") from None
    return r


def check(req: BacktestRequest, assign: dict[str, Any]) -> None:
    """Raise ValueError if `assign` cannot be applied or the strategy cannot be built with it (checked before a job starts)."""
    from trader.backtest.runner import build_strategy

    try:
        build_strategy(apply(req, assign))
    except (TypeError, KeyError) as exc:
        raise ValueError(f"invalid setting: {exc}") from None


MOMENTUM_TUNABLES = [("strategy_params.rsi_long", "RSI for longs above", 55), ("strategy_params.rsi_short", "RSI for shorts below", 45),
                     ("strategy_params.adx_min", "ADX at least", 18), ("strategy_params.atr_pct_min", "ATR % at least", 0.04),
                     ("strategy_params.stop_loss_pct", "stop loss % of premium", 25), ("strategy_params.target_pct", "target % of premium", 50),
                     ("strategy_params.max_trades_per_day", "max trades per day", 3),
                     ("engine_params.trail_activate_pct", "trail starts at +%", 30), ("engine_params.trail_pct", "trail distance %", 15)]


def tunables(spec: dict | None) -> list[dict]:
    """Every number a sweep may vary: exits and risk settings, each constant in a condition and each indicator period."""
    if spec is None:
        return [{"path": p, "label": lbl, "value": v} for p, lbl, v in MOMENTUM_TUNABLES]
    from pydantic import TypeAdapter

    from trader.strategy.spec import Condition, render_condition

    as_condition = TypeAdapter(Condition)

    out: list[dict] = []
    ex = spec.get("exits", {})
    for k, lbl in (("stop_loss_pct", "stop loss % of premium"), ("target_pct", "target % of premium"),
                   ("time_exit_bars", "time exit after bars")):
        if ex.get(k) is not None:
            out.append({"path": f"spec.exits.{k}", "label": lbl, "value": ex[k]})
    for k, lbl in (("activate_pct", "trail starts at +%"), ("trail_pct", "trail distance %")):
        if (ex.get("trail") or {}).get(k) is not None:
            out.append({"path": f"spec.exits.trail.{k}", "label": lbl, "value": ex["trail"][k]})
    if (spec.get("risk") or {}).get("max_trades_per_day") is not None:
        out.append({"path": "spec.risk.max_trades_per_day", "label": "max trades per day", "value": spec["risk"]["max_trades_per_day"]})

    def walk(node: Any, path: str, ctx: str) -> None:
        if isinstance(node, dict):
            if "cmp" in node or "cross" in node or "between" in node:
                try:
                    ctx = " ".join(render_condition(as_condition.validate_python(node)).replace("- ", "", 1).split())
                except Exception:  # noqa: BLE001 - labels are a convenience
                    pass
            if "const" in node and isinstance(node["const"], (int, float)):
                out.append({"path": f"{path}.const", "label": f"number in: {ctx}", "value": node["const"]})
            if "ind" in node and isinstance(node.get("period"), int):
                out.append({"path": f"{path}.period", "label": f"{node['ind']} period in: {ctx}", "value": node["period"]})
            for k, v in node.items():
                walk(v, f"{path}.{k}", ctx)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}.{i}", ctx)

    for side in ("long", "short"):
        s = (spec.get("sides") or {}).get(side)
        if s and s.get("entry") is not None:
            walk(s["entry"], f"spec.sides.{side}.entry", "")
    walk((spec.get("filters") or {}).get("all", []), "spec.filters.all", "")
    if ex.get("conditions") is not None:
        walk(ex["conditions"], "spec.exits.conditions", "")
    return out


def combos(params: list[Param], method: str = "grid", n_trials: int | None = None, seed: int = 7) -> list[dict]:
    keys = [p.path for p in params]
    grid = [dict(zip(keys, vals)) for vals in itertools.product(*(p.values for p in params))]
    if method == "grid":
        if len(grid) > MAX_RUNS:
            raise ValueError(f"{len(grid)} combinations; at most {MAX_RUNS}: use fewer values or method 'random'")
        return grid
    n = min(n_trials or 50, len(grid), MAX_RUNS)
    return random.Random(seed).sample(grid, n)


def score_of(metrics: dict, objective: str, min_trades: int) -> float | None:
    if not metrics or metrics.get("trades", 0) < min_trades:
        return None
    v = metrics.get(objective)
    return None if v is None else float(v)


def _subset(m: dict) -> dict:
    keys = ("trades", "total_net_pnl", "profit_factor", "win_rate", "sharpe", "max_drawdown", "expectancy", "return_pct")
    return {k: m.get(k) for k in keys}


# ---- data windows ---------------------------------------------------------------------------------------------------
def slice_data(data: bt_data.BacktestData, start: date, end: date) -> bt_data.BacktestData:
    """The part of a loaded dataset a run of [start, end] reads (its warm-up days included)."""
    lo = start - timedelta(days=WARMUP_DAYS)
    bars = [b for b in data.bars if lo <= b.ts.astimezone(IST).date() <= end]
    keep = {b.ts for b in bars}
    return bt_data.BacktestData(data.source, bars, {t: c for t, c in data.futures.items() if t in keep},
                                {t: v for t, v in data.vix.items() if t in keep}, data.options, data.option_lot_sizes,
                                data.built_from)


def effective_end(end: date, holdout_days: int) -> tuple[date, dict | None]:
    if holdout_days <= 0:
        return end, None
    cut = end - timedelta(days=holdout_days)
    return cut, {"from": (cut + timedelta(days=1)).isoformat(), "to": end.isoformat(), "days": holdout_days}


# ---- sweep ------------------------------------------------------------------------------------------------------------
@dataclass
class SweepSpec:
    params: list[Param]
    objective: str = "profit_factor"
    min_trades: int = 30
    method: str = "grid"  # grid | random | optuna
    n_trials: int | None = None
    holdout_days: int = 0
    workers: int | None = None


def planned_runs(spec: SweepSpec) -> int:
    n = math.prod(len(p.values) for p in spec.params)
    return (n if spec.method == "grid" else min(spec.n_trials or 50, n, MAX_RUNS)) + 1  # +1: the best one re-run in full


def run_sweep(base: BacktestRequest, data: bt_data.BacktestData, spec: SweepSpec, progress: Progress | None = None,
              cancelled: Callable[[], bool] | None = None) -> dict:
    if spec.objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")
    end, holdout = effective_end(base.end, spec.holdout_days)
    if end <= base.start:
        raise ValueError("the holdout leaves no days to sweep")
    base = replace(base, end=end)
    window = slice_data(data, base.start, end)
    if spec.method == "optuna":
        cs, outs = _optuna(base, window, spec, progress, cancelled)
    else:
        cs = combos(spec.params, spec.method, spec.n_trials)
        reqs = [apply(base, c) for c in cs]
        outs = run_many(reqs, window, workers=spec.workers, progress=(lambda d, t: progress(d, t + 1, "settings")) if progress else None,
                        cancelled=cancelled)
    runs = []
    for c, o in zip(cs, outs):
        if o is None:
            continue
        if "error" in o:
            runs.append({"params": c, "error": o["error"], "score": None})
            continue
        runs.append({"params": c, "metrics": _subset(o["metrics"]), "score": score_of(o["metrics"], spec.objective, spec.min_trades)})
    scored = [r for r in runs if r.get("score") is not None]
    res: dict = {"objective": spec.objective, "min_trades": spec.min_trades, "method": spec.method, "trials": len(runs),
                 "window": {"from": base.start.isoformat(), "to": end.isoformat()}, "holdout": holdout, "runs": runs,
                 "params": [{"path": p.path, "values": p.values} for p in spec.params]}
    # Stability on the grid: each point next to its neighbours (only meaningful for a full grid).
    if spec.method == "grid" and spec.params:
        grid = {tuple(r["params"][p.path] for p in spec.params): r.get("score") for r in runs}
        nb = stats.neighbourhood(grid, {i: p.values for i, p in enumerate(spec.params)})
        for r in runs:
            r.update(nb.get(tuple(r["params"][p.path] for p in spec.params), {}))
        if len(spec.params) >= 2:
            # the first two parameters; with more, each cell shows the best score over the others
            px, py = spec.params[0], spec.params[1]
            z: list[list[float | None]] = [[None] * len(px.values) for _ in py.values]
            for r in runs:
                sc = r.get("score")
                if sc is None:
                    continue
                yi, xi = py.values.index(r["params"][py.path]), px.values.index(r["params"][px.path])
                z[yi][xi] = sc if z[yi][xi] is None else max(z[yi][xi], sc)
            res["heatmap"] = {"x": px.path, "y": py.path, "xs": px.values, "ys": py.values, "z": z,
                              "note": None if len(spec.params) == 2 else "best score over the other parameters"}
    if not scored:
        res["verdict"] = f"No setting had at least {spec.min_trades} trades: not enough evidence to choose one."
        return res
    best = max(scored, key=lambda r: r["score"])
    res["best"] = best
    full = execute(apply(base, best["params"]), window)
    daily = [d["pnl"] or 0.0 for d in full["metrics"]["daily_pnl"]]
    capital = float(base.engine_params.get("capital", 100_000.0))
    trial_srs = [((r.get("metrics") or {}).get("sharpe") or 0.0) / math.sqrt(252) for r in runs if "metrics" in r]
    _, sd, _, _ = stats.moments(trial_srs)
    res["deflated_sharpe"] = stats.deflated_sharpe([x / capital for x in daily], len(runs), sd * sd)
    res["bootstrap"] = stats.bootstrap(daily)
    res["best_equity_curve"] = full["metrics"]["equity_curve"]
    res["verdict"] = verdict(full["metrics"]["trades"], best.get("fragile"), res["deflated_sharpe"]["dsr"],
                             full["metrics"].get("profit_factor"), len(runs), in_sample=True)
    if progress:
        progress(len(runs) + 1, len(runs) + 1, "done")
    return res


def _optuna(base: BacktestRequest, window: bt_data.BacktestData, spec: SweepSpec, progress: Progress | None,
            cancelled: Callable[[], bool] | None) -> tuple[list[dict], list]:
    try:
        import optuna
    except ImportError:
        raise ValueError("method 'optuna' needs the optional package: pip install -e \".[research]\"") from None
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=7))
    total = min(spec.n_trials or 50, MAX_RUNS)
    cs: list[dict] = []
    outs: list = []
    batch = max(1, spec.workers or 4)
    while len(cs) < total and not (cancelled and cancelled()):
        trials = [study.ask() for _ in range(min(batch, total - len(cs)))]
        assigns = [{p.path: t.suggest_categorical(p.path, p.values) for p in spec.params} for t in trials]
        res = run_many([apply(base, a) for a in assigns], window, workers=spec.workers)
        for t, a, o in zip(trials, assigns, res):
            sc = score_of((o or {}).get("metrics") or {}, spec.objective, spec.min_trades) if o and "error" not in o else None
            study.tell(t, sc if sc is not None and math.isfinite(sc) else -1e9)
            cs.append(a)
            outs.append(o)
        if progress:
            progress(len(cs), total + 1, "settings")
    return cs, outs


def verdict(trades: int, fragile: bool | None, dsr: float, pf: float | None, trials: int, in_sample: bool) -> str:
    if trades < MIN_EVIDENCE_TRADES:
        return f"Not enough trades to judge ({trades}; at least {MIN_EVIDENCE_TRADES} needed)."
    where = "in-sample (the setting was chosen on these same days)" if in_sample else "out-of-sample"
    if pf is None or pf <= 1:
        return f"No edge {where}: profit factor {pf}."
    msg = f"Profitable {where}, profit factor {pf}"
    if fragile:
        msg += "; FRAGILE: a neighbouring setting does much worse"
    if dsr < 0.95:
        msg += f"; not significant after {trials} settings tried (deflated Sharpe {dsr:.2f} < 0.95)"
    else:
        msg += f"; deflated Sharpe {dsr:.2f}"
    return msg + ("." if not in_sample else ". Confirm with a walk-forward before trusting it.")


# ---- walk-forward -----------------------------------------------------------------------------------------------------
@dataclass
class WalkForwardSpec:
    params: list[Param]
    train_days: int = 180
    test_days: int = 60
    anchored: bool = False
    embargo_days: int = 1
    objective: str = "profit_factor"
    min_trades: int = 20  # for choosing a setting on a training window
    holdout_days: int = 0
    workers: int | None = None
    folds: list[tuple[date, date, date, date]] = field(default_factory=list)


def plan_folds(start: date, end: date, spec: WalkForwardSpec) -> list[tuple[date, date, date, date]]:
    """(train_start, train_end, test_start, test_end) windows, calendar days, test windows back to back."""
    out = []
    t = start
    k = 0
    while True:
        tr_start = start if spec.anchored else t
        tr_end = (start + timedelta(days=spec.train_days + k * spec.test_days - 1)) if spec.anchored \
            else t + timedelta(days=spec.train_days - 1)
        te_start = tr_end + timedelta(days=spec.embargo_days + 1)
        te_end = te_start + timedelta(days=spec.test_days - 1)
        if te_end > end:
            break
        out.append((tr_start, tr_end, te_start, te_end))
        t += timedelta(days=spec.test_days)
        k += 1
    return out


def planned_wf_runs(start: date, end: date, spec: WalkForwardSpec) -> tuple[int, int]:
    folds = len(plan_folds(start, effective_end(end, spec.holdout_days)[0], spec))
    n = math.prod(len(p.values) for p in spec.params)
    return folds, folds * (n + 1)


def run_walk_forward(base: BacktestRequest, data: bt_data.BacktestData, spec: WalkForwardSpec,
                     progress: Progress | None = None, cancelled: Callable[[], bool] | None = None) -> dict:
    if spec.objective not in OBJECTIVES:
        raise ValueError(f"objective must be one of {OBJECTIVES}")
    end, holdout = effective_end(base.end, spec.holdout_days)
    folds = plan_folds(base.start, end, spec)
    if not folds:
        raise ValueError("the range is too short for one training and one test window")
    cs = combos(spec.params, "grid") if spec.params else [{}]
    total = len(folds) * (len(cs) + 1)
    done = 0
    rows, test_trades, test_days = [], [], []
    train_srs, test_srs, trial_srs = [], [], []
    capital = float(base.engine_params.get("capital", 100_000.0))
    for i, (a, b, c, d) in enumerate(folds):
        if cancelled and cancelled():
            break
        train = replace(base, start=a, end=b)
        outs = run_many([apply(train, x) for x in cs], slice_data(data, a, b), workers=spec.workers,
                        progress=(lambda dn, _t, base_=done: progress(base_ + dn, total, f"fold {i + 1}/{len(folds)}: training"))
                        if progress else None, cancelled=cancelled)
        done += len(cs)
        scored = [(score_of(o["metrics"], spec.objective, spec.min_trades), x, o) for x, o in zip(cs, outs)
                  if o and "error" not in o]
        trial_srs += [(o["metrics"].get("sharpe") or 0.0) / math.sqrt(252) for _, _, o in scored]
        scored = [s for s in scored if s[0] is not None]
        fold: dict = {"train": [a.isoformat(), b.isoformat()], "test": [c.isoformat(), d.isoformat()]}
        if not scored:
            fold["note"] = f"no setting had {spec.min_trades} trades in training: fold skipped"
            rows.append(fold)
            done += 1
            continue
        sc, chosen, train_out = max(scored, key=lambda s: s[0])
        test = execute(apply(replace(base, start=c, end=d), chosen), slice_data(data, c, d))
        done += 1
        if progress:
            progress(done, total, f"fold {i + 1}/{len(folds)}: testing")
        fold.update(params=chosen, train_metrics=_subset(train_out["metrics"]), test_metrics=_subset(test["metrics"]))
        rows.append(fold)
        train_srs.append(train_out["metrics"].get("sharpe") or 0.0)
        test_srs.append(test["metrics"].get("sharpe") or 0.0)
        test_trades += test["trades"]
        test_days += [x["date"] for x in test["metrics"]["daily_pnl"]]
    trades = [Trade(**t) for t in test_trades]
    stitched = compute_metrics(trades, capital, sorted(set(test_days)))
    daily = [x["pnl"] or 0.0 for x in stitched["daily_pnl"]]
    dsr = stats.deflated_sharpe([x / capital for x in daily], len(cs), stats.moments(trial_srs or [0.0])[1] ** 2)
    in_sample = {k: round(sum((f.get("train_metrics") or {}).get(k) or 0 for f in rows if "train_metrics" in f)
                          / max(1, sum(1 for f in rows if "train_metrics" in f)), 3)
                 for k in ("profit_factor", "sharpe", "win_rate")}
    return {
        "objective": spec.objective, "settings_per_fold": len(cs), "folds": rows, "holdout": holdout,
        "layout": {"train_days": spec.train_days, "test_days": spec.test_days, "anchored": spec.anchored,
                   "embargo_days": spec.embargo_days},
        "out_of_sample": {k: v for k, v in stitched.items() if k not in ("daily_pnl",)},
        "in_sample_average": in_sample,
        "walk_forward_efficiency": stats.walk_forward_efficiency(train_srs, test_srs),
        "deflated_sharpe": dsr, "bootstrap": stats.bootstrap(daily),
        "verdict": verdict(stitched["trades"], None, dsr["dsr"], stitched.get("profit_factor"), len(cs), in_sample=False),
    }


# ---- holdout ----------------------------------------------------------------------------------------------------------
def holdout_signature(base: BacktestRequest, assign: dict, holdout: dict) -> str:
    """One holdout check per strategy version, setting and holdout window."""
    who = base.strategy_version_id or json.dumps(base.spec or base.strategy_params, sort_keys=True)
    raw = json.dumps([base.underlying, base.timeframe, base.pricing, who, assign, holdout["from"], holdout["to"]], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def run_holdout(base: BacktestRequest, data: bt_data.BacktestData, assign: dict, holdout_days: int) -> dict:
    _, holdout = effective_end(base.end, holdout_days)
    if holdout is None:
        raise ValueError("holdout_days must be positive")
    req = apply(replace(base, start=date.fromisoformat(holdout["from"]), end=base.end), assign)
    out = execute(req, slice_data(data, req.start, req.end))
    return {"holdout": holdout, "params": assign, "metrics": _subset(out["metrics"]),
            "equity_curve": out["metrics"]["equity_curve"], "warnings": out["warnings"]}
