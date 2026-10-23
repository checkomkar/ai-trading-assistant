"""Research jobs: sweeps, walk-forward and the holdout check run in the background (PRD §24.5-24.6).

Each job loads its data once, runs every setting over it in worker processes, and stores its result on the job row.
The plan's backtest allowance is reserved for every run the job will do before it starts, and settled at the number
of runs it really did (a cancelled sweep gives the rest back).
"""

from __future__ import annotations

import asyncio
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from trader.api.deps import CurrentUser, MeterDep
from trader.api.routes.backtests import BacktestIn, option_request, resolve
from trader.backtest.runner import load_for
from trader.db.models import ResearchJobRow
from trader.research import jobs as J
from trader.research import validation as V

router = APIRouter(prefix="/research", tags=["research"])

MAX_YEARS = 7


class ResearchBase(BacktestIn):
    """A backtest request a research job starts from; it may cover up to seven years."""

    @model_validator(mode="after")
    def _range(self) -> "ResearchBase":
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        if (self.end - self.start).days > MAX_YEARS * 366:
            raise ValueError(f"a research job can cover at most {MAX_YEARS} years")
        return self


class ParamIn(BaseModel):
    path: str = Field(..., max_length=200)
    values: list[float | int] | None = Field(None, max_length=V.MAX_VALUES_PER_PARAM)
    min: float | None = None
    max: float | None = None
    step: float | None = None


class SweepIn(ResearchBase):
    params: list[ParamIn] = Field(..., min_length=1, max_length=4)
    objective: str = "profit_factor"
    min_trades: int = Field(30, ge=1, le=10_000)
    method: str = Field("grid", pattern="^(grid|random|optuna)$")
    n_trials: int | None = Field(None, ge=2, le=V.MAX_RUNS)
    holdout_days: int = Field(0, ge=0, le=730)


class WalkForwardIn(ResearchBase):
    params: list[ParamIn] = Field(default_factory=list, max_length=4)
    train_days: int = Field(180, ge=10, le=1500)
    test_days: int = Field(60, ge=5, le=730)
    anchored: bool = False
    embargo_days: int = Field(1, ge=0, le=30)
    objective: str = "profit_factor"
    min_trades: int = Field(20, ge=1, le=10_000)
    holdout_days: int = Field(0, ge=0, le=730)


class HoldoutIn(ResearchBase):
    assign: dict[str, float | int] = Field(default_factory=dict)
    holdout_days: int = Field(90, ge=5, le=730)


def _params(ps: list[ParamIn]) -> list[V.Param]:
    try:
        return [V.Param.from_dict(p.model_dump(exclude_none=True)) for p in ps]
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from None


async def _base(request: Request, body: ResearchBase, user):  # noqa: ANN001, ANN202
    spec, version_id, strategy = await resolve(request, body, user)
    if spec and spec.get("market") == "equity":
        raise HTTPException(422, "sweeps and walk-forward are for index-option strategies for now")
    return option_request(request, body, spec, version_id, strategy)


async def _submit(request: Request, user, meter, kind: str, params: dict, runs: int, work) -> dict:  # noqa: ANN001
    if runs > V.MAX_RUNS + 50:
        raise HTTPException(422, f"this job would run {runs} backtests; the limit is {V.MAX_RUNS}")
    hold = await meter.reserve(user, "backtests", runs)

    async def job(ctx: J.JobContext) -> dict:
        used = {"n": 0}

        def progress(done: int, total: int, note: str = "") -> None:
            used["n"] = done
            ctx.progress(done, total, note)

        try:
            out = await work(ctx, progress)
        except BaseException:
            await asyncio.shield(meter.adjust(hold, used["n"]))
            raise
        await asyncio.shield(meter.adjust(hold, max(used["n"], 1) if not ctx.cancelled else used["n"]))
        return out

    job_id = await request.app.state.jobs.submit(user.id, kind, params, job, total=runs)
    return {"job_id": job_id, "runs": runs}


@router.post("/sweep")
async def sweep(request: Request, body: SweepIn, user: CurrentUser, meter: MeterDep) -> dict:
    base = await _base(request, body, user)
    spec = V.SweepSpec(_params(body.params), body.objective, body.min_trades, body.method, body.n_trials, body.holdout_days)
    if spec.objective not in V.OBJECTIVES:
        raise HTTPException(422, f"objective must be one of {V.OBJECTIVES}")
    try:
        runs = V.planned_runs(spec)
        V.combos(spec.params, spec.method, spec.n_trials)  # rejects an oversized grid before anything is reserved
        V.check(base, {p.path: p.values[0] for p in spec.params})  # rejects an unknown path or setting
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    sm = request.app.state.sessionmaker

    async def work(ctx: J.JobContext, progress) -> dict:  # noqa: ANN001
        data = await load_for(sm, base)
        return await asyncio.to_thread(V.run_sweep, base, data, spec, progress, lambda: ctx.cancelled)

    return await _submit(request, user, meter, "sweep", body.model_dump(mode="json"), runs, work)


@router.post("/walk-forward")
async def walk_forward(request: Request, body: WalkForwardIn, user: CurrentUser, meter: MeterDep) -> dict:
    base = await _base(request, body, user)
    spec = V.WalkForwardSpec(_params(body.params), body.train_days, body.test_days, body.anchored, body.embargo_days,
                             body.objective, body.min_trades, body.holdout_days)
    if spec.objective not in V.OBJECTIVES:
        raise HTTPException(422, f"objective must be one of {V.OBJECTIVES}")
    try:
        if spec.params:
            V.combos(spec.params, "grid")
            V.check(base, {p.path: p.values[0] for p in spec.params})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    folds, runs = V.planned_wf_runs(base.start, base.end, spec)
    if folds == 0:
        raise HTTPException(422, "the range is too short for one training and one test window")
    sm = request.app.state.sessionmaker

    async def work(ctx: J.JobContext, progress) -> dict:  # noqa: ANN001
        data = await load_for(sm, base)
        return await asyncio.to_thread(V.run_walk_forward, base, data, spec, progress, lambda: ctx.cancelled)

    return await _submit(request, user, meter, "walk_forward", body.model_dump(mode="json") | {"folds": folds}, runs, work)


@router.post("/holdout")
async def holdout(request: Request, body: HoldoutIn, user: CurrentUser, meter: MeterDep) -> dict:
    """The final check on the reserved most recent days: once per strategy version, setting and holdout window."""
    base = await _base(request, body, user)
    try:
        V.check(base, dict(body.assign))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    _, window = V.effective_end(base.end, body.holdout_days)
    sig = V.holdout_signature(base, dict(body.assign), window)
    async with request.app.state.sessionmaker() as s:
        prior = (await s.execute(select(ResearchJobRow).where(
            ResearchJobRow.owner == user.id, ResearchJobRow.kind == "holdout",
            ResearchJobRow.status.in_(("queued", "running", "done"))))).scalars().all()
    used = next((r for r in prior if (r.params or {}).get("signature") == sig), None)
    if used is not None:
        raise HTTPException(409, {"code": "holdout_used", "job_id": used.id,
                                  "message": "This strategy version and setting were already checked on this holdout. "
                                             "Checking again would turn the holdout into more training data."})
    sm = request.app.state.sessionmaker

    async def work(ctx: J.JobContext, progress) -> dict:  # noqa: ANN001
        data = await load_for(sm, base)
        out = await asyncio.to_thread(V.run_holdout, base, data, dict(body.assign), body.holdout_days)
        progress(1, 1, "done")
        return out

    return await _submit(request, user, meter, "holdout", body.model_dump(mode="json") | {"signature": sig}, 1, work)


class AIResearchIn(ResearchBase):
    n_variants: int = Field(5, ge=1, le=8)
    train_days: int = Field(180, ge=10, le=1500)
    test_days: int = Field(60, ge=5, le=730)
    anchored: bool = False
    embargo_days: int = Field(1, ge=0, le=30)
    min_trades: int = Field(V.MIN_EVIDENCE_TRADES, ge=1, le=10_000)
    holdout_days: int = Field(90, ge=30, le=730)  # the AI never sees these days


@router.post("/ai")
async def ai_research(request: Request, body: AIResearchIn, user: CurrentUser, meter: MeterDep) -> dict:
    """The model proposes variants of a strategy; each is walk-forwarded on the same folds and judged by code (PRD §24.10).
    One AI generation (metered) plus the backtests of the baseline and every variant."""
    from trader.ai.providers import TranslatorUnavailable, build_provider, public_message
    from trader.ai.research_assistant import propose
    from trader.research import ai_loop
    from trader.strategy.spec import MOMENTUM_V1

    if not body.strategy_id and not body.spec and body.strategy == "momentum":
        body = body.model_copy(update={"strategy": "spec", "spec": MOMENTUM_V1})
    base = await _base(request, body, user)
    if not base.spec:
        raise HTTPException(422, "AI research needs a rule-based strategy (a saved strategy or a spec)")
    wf = V.WalkForwardSpec([], body.train_days, body.test_days, body.anchored, body.embargo_days, "profit_factor",
                           1, body.holdout_days)
    folds, _ = V.planned_wf_runs(base.start, base.end, wf)
    if folds == 0:
        raise HTTPException(422, "the range is too short for one training and one test window before the holdout")
    runs = folds * (body.n_variants + 1)
    sm, settings = request.app.state.sessionmaker, request.app.state.settings

    async def work(ctx: J.JobContext, progress) -> dict:  # noqa: ANN001
        data = await load_for(sm, base)
        loop = asyncio.get_running_loop()

        def ask(spec: dict, summary: dict, n: int):  # noqa: ANN202 - runs in the worker thread
            async def call():  # noqa: ANN202
                provider = build_provider(settings, user.is_admin)
                async with meter.metered(user, "generations", provider) as mp:
                    return await asyncio.to_thread(propose, mp, spec, summary, n)

            try:
                return asyncio.run_coroutine_threadsafe(call(), loop).result()
            except TranslatorUnavailable as exc:
                raise RuntimeError(public_message(exc, user.is_admin)) from None

        return await asyncio.to_thread(ai_loop.run, base, data, wf, ask, body.n_variants, body.min_trades, progress,
                                       lambda: ctx.cancelled)

    return await _submit(request, user, meter, "ai_research", body.model_dump(mode="json"), runs, work)


class TunablesIn(BaseModel):
    strategy_id: str | None = None
    spec: dict | None = None


@router.post("/tunables")
async def tunables(request: Request, body: TunablesIn, user: CurrentUser) -> list[dict]:
    """Numbers a sweep can vary for this strategy (Momentum #1's settings when neither is given)."""
    spec = body.spec
    if body.strategy_id:
        from trader.db.models import StrategyRow

        async with request.app.state.sessionmaker() as s:
            row = await s.get(StrategyRow, body.strategy_id)
        if row is None or row.owner != user.id:
            raise HTTPException(404, "strategy not found")
        spec = row.spec
    return V.tunables(spec)


@router.get("/jobs")
async def list_jobs(request: Request, user: CurrentUser, limit: int = Query(30, ge=1, le=100)) -> list[dict]:
    return await J.list_jobs(request.app.state.sessionmaker, user.id, limit)


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: str, user: CurrentUser) -> dict:
    async with request.app.state.sessionmaker() as s:
        row = await s.get(ResearchJobRow, job_id)
    if row is None or row.owner != user.id:
        raise HTTPException(404, "job not found")
    return J.summary(row, with_result=True)


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(request: Request, job_id: str, user: CurrentUser) -> dict:
    if not request.app.state.jobs.cancel(job_id, user.id):
        raise HTTPException(409, "job is not queued or running")
    return {"id": job_id, "cancelling": True}
