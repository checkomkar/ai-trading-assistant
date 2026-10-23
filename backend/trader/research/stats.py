"""Statistics that guard against fooling ourselves (PRD §24.6 B5.4-B5.7).

* **Deflated Sharpe Ratio** (Bailey & López de Prado, "The Deflated Sharpe Ratio", 2014): the probability that the
  selected strategy's true Sharpe is above zero, after allowing for how many settings were tried, how non-normal its
  returns are and how long the record is. Implemented from the paper's formulas with the standard library's normal
  distribution; checked in tests against its defining properties and a Monte-Carlo of the expected maximum Sharpe.
* **Bootstrap** of daily P&L: the spread of outcomes the same trades could have produced in another order.
* **Neighbourhood stability** of a parameter grid: a good result whose neighbours lose money is fragile.
* **Walk-forward efficiency**: out-of-sample Sharpe divided by in-sample Sharpe.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist

N01 = NormalDist()
EULER_GAMMA = 0.5772156649015329


def moments(xs: list[float]) -> tuple[float, float, float, float]:
    """Mean, standard deviation (sample), skewness, kurtosis (non-excess: 3 for a normal distribution)."""
    n = len(xs)
    if n < 2:
        return (xs[0] if xs else 0.0), 0.0, 0.0, 3.0
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return m, 0.0, 0.0, 3.0
    pop = math.sqrt(sum((x - m) ** 2 for x in xs) / n)
    skew = sum((x - m) ** 3 for x in xs) / n / pop ** 3
    kurt = sum((x - m) ** 4 for x in xs) / n / pop ** 4
    return m, sd, skew, kurt


def sharpe(xs: list[float]) -> float:
    """Per-period Sharpe ratio (not annualised)."""
    m, sd, _, _ = moments(xs)
    return m / sd if sd > 0 else 0.0


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """SR0: the Sharpe the best of `n_trials` unskilled strategies reaches by luck (paper eq. for E[max SR])."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * N01.inv_cdf(1 - 1 / n_trials)
                                + EULER_GAMMA * N01.inv_cdf(1 - 1 / (n_trials * math.e)))


def probabilistic_sharpe(sr: float, sr_benchmark: float, t: int, skew: float, kurt: float) -> float:
    """PSR: P(true SR > benchmark) given an observed per-period SR over `t` returns with this skew and kurtosis."""
    if t < 2:
        return 0.5
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return 0.5
    return N01.cdf((sr - sr_benchmark) * math.sqrt(t - 1) / math.sqrt(denom))


def deflated_sharpe(returns: list[float], n_trials: int, var_trial_sr: float) -> dict:
    """DSR of the selected strategy's per-period `returns`, given `n_trials` tried with this variance of their Sharpes."""
    _, _, skew, kurt = moments(returns)
    sr = sharpe(returns)
    sr0 = expected_max_sharpe(n_trials, var_trial_sr)
    return {"sharpe_per_period": round(sr, 5), "sharpe_annualised": round(sr * math.sqrt(252), 3),
            "expected_max_sharpe_by_luck": round(sr0, 5), "trials": n_trials, "returns": len(returns),
            "skew": round(skew, 3), "kurtosis": round(kurt, 3),
            "dsr": round(probabilistic_sharpe(sr, sr0, len(returns), skew, kurt), 4)}


def max_drawdown(daily: list[float]) -> float:
    eq = peak = dd = 0.0
    for x in daily:
        eq += x
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return dd


def bootstrap(daily_pnl: list[float], n: int = 1000, seed: int = 7) -> dict:
    """Resample whole days with replacement (same-day trades stay together): percentiles of total P&L and drawdown."""
    if not daily_pnl:
        return {"samples": 0}
    rng = random.Random(seed)
    k = len(daily_pnl)
    totals, dds = [], []
    for _ in range(n):
        path = [daily_pnl[rng.randrange(k)] for _ in range(k)]
        totals.append(sum(path))
        dds.append(max_drawdown(path))
    totals.sort()
    dds.sort()

    def pct(xs: list[float], q: float) -> float:
        return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2)

    return {"samples": n, "days": k,
            "net_pnl": {"p5": pct(totals, 0.05), "p50": pct(totals, 0.5), "p95": pct(totals, 0.95)},
            "max_drawdown": {"p5": pct(dds, 0.05), "p50": pct(dds, 0.5), "p95": pct(dds, 0.95)},
            "prob_loss": round(sum(1 for x in totals if x < 0) / n, 3)}


def walk_forward_efficiency(train_sharpes: list[float], test_sharpes: list[float]) -> float | None:
    if not train_sharpes or not test_sharpes:
        return None
    tr = sum(train_sharpes) / len(train_sharpes)
    te = sum(test_sharpes) / len(test_sharpes)
    return round(te / tr, 3) if tr > 0 else None


def neighbourhood(grid: dict[tuple, float], steps: dict[int, list]) -> dict[tuple, dict]:
    """For each grid point: the worst neighbour score (+/-1 step in each parameter) and whether the point is fragile
    (profitable itself, but a neighbour loses money or scores less than half as much)."""
    out: dict[tuple, dict] = {}
    for point, score in grid.items():
        worst = None
        for d, values in steps.items():
            i = values.index(point[d])
            for j in (i - 1, i + 1):
                if 0 <= j < len(values):
                    nb = point[:d] + (values[j],) + point[d + 1:]
                    if nb in grid and grid[nb] is not None:
                        worst = grid[nb] if worst is None else min(worst, grid[nb])
        fragile = (score is not None and worst is not None and score > 0 and (worst < 0 or worst < score / 2))
        out[point] = {"worst_neighbour": worst, "fragile": fragile}
    return out
