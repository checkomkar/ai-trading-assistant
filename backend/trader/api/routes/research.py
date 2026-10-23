"""Research jobs: sweeps, walk-forward and AI research run in the background (PRD §24.5-24.10)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from trader.api.deps import CurrentUser
from trader.db.models import ResearchJobRow
from trader.research import jobs as J

router = APIRouter(prefix="/research", tags=["research"])


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
