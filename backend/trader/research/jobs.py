"""Background research jobs (PRD §24.5 B4.4): long work off the request path, with progress, cancel and a stored result.

One `JobManager` per API process (on `app.state`). A job is a coroutine function given a `JobContext`; heavy CPU work
inside it goes to threads / worker processes (`research.parallel`). Rows in `research_jobs` hold status, progress
and the result, so a page can poll, and a restart marks unfinished jobs as failed instead of leaving them "running".
Limits: at most `per_owner` running jobs per account and `max_running` in the process; extra jobs wait in the queue.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from sqlalchemy import select, update

from trader.db.models import ResearchJobRow
from trader.logging import get_logger

log = get_logger("research")

TERMINAL = ("done", "failed", "cancelled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class JobContext:
    id: str
    owner: str
    params: dict
    _manager: "JobManager"
    _cancel: asyncio.Event = field(default_factory=asyncio.Event)
    _last_write: float = 0.0
    _progress: tuple[int, int, str] = (0, 0, "")

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def progress(self, done: int, total: int, note: str = "") -> None:
        """Safe to call from worker threads; written to the database at most twice a second."""
        self._progress = (done, total, note[:200])
        now = time.monotonic()
        if now - self._last_write >= 0.5 or done >= total:
            self._last_write = now
            self._manager._schedule_progress(self)


Work = Callable[[JobContext], Awaitable[dict]]


class JobManager:
    def __init__(self, sessionmaker, max_running: int = 2, per_owner: int = 1) -> None:  # noqa: ANN001
        self.sm = sessionmaker
        self.max_running, self.per_owner = max_running, per_owner
        self._running: dict[str, JobContext] = {}
        self._queue: list[tuple[JobContext, Work]] = []
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def recover(self) -> int:
        """At start-up: jobs left queued or running by a previous process can no longer finish."""
        async with self.sm() as s, s.begin():
            r = await s.execute(update(ResearchJobRow).where(ResearchJobRow.status.in_(("queued", "running")))
                                .values(status="failed", error="interrupted by a server restart", finished_at=_now()))
        return r.rowcount or 0

    async def submit(self, owner: str, kind: str, params: dict, work: Work, total: int = 0) -> str:
        self._loop = asyncio.get_running_loop()
        job_id = uuid.uuid4().hex[:12]
        async with self.sm() as s, s.begin():
            s.add(ResearchJobRow(id=job_id, owner=owner, kind=kind, status="queued", done=0, total=total, note="",
                                 params=params, created_at=_now()))
        self._queue.append((JobContext(job_id, owner, params, self), work))
        self._pump()
        return job_id

    def cancel(self, job_id: str, owner: str) -> bool:
        ctx = self._running.get(job_id)
        if ctx is not None and ctx.owner == owner:
            ctx._cancel.set()
            return True
        for i, (q, _) in enumerate(self._queue):
            if q.id == job_id and q.owner == owner:
                self._queue.pop(i)
                self._spawn(self._finish(q, "cancelled", None, None))
                return True
        return False

    async def wait(self, job_id: str, timeout: float = 60) -> None:
        """Tests and scripts: until the job reaches a terminal state."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            async with self.sm() as s:
                row = await s.get(ResearchJobRow, job_id)
            if row is not None and row.status in TERMINAL:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(job_id)

    async def shutdown(self) -> None:
        for ctx in self._running.values():
            ctx._cancel.set()
        for t in list(self._tasks):
            t.cancel()

    # ---- internals ---------------------------------------------------------------------------------------------
    def _spawn(self, coro) -> None:  # noqa: ANN001
        t = asyncio.ensure_future(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    def _pump(self) -> None:
        i = 0
        while i < len(self._queue) and len(self._running) < self.max_running:
            ctx, work = self._queue[i]
            if sum(1 for c in self._running.values() if c.owner == ctx.owner) >= self.per_owner:
                i += 1
                continue
            self._queue.pop(i)
            self._running[ctx.id] = ctx
            self._spawn(self._run(ctx, work))

    async def _run(self, ctx: JobContext, work: Work) -> None:
        async with self.sm() as s, s.begin():
            await s.execute(update(ResearchJobRow).where(ResearchJobRow.id == ctx.id)
                            .values(status="running", started_at=_now()))
        try:
            result = await work(ctx)
            await self._finish(ctx, "cancelled" if ctx.cancelled else "done", result, None)
        except asyncio.CancelledError:
            await asyncio.shield(self._finish(ctx, "cancelled", None, None))
            raise
        except Exception as exc:  # noqa: BLE001 - a failed job is reported on its row, never crashes the server
            log.warning("research job failed", event="research.failed", job=ctx.id, error=str(exc)[:300])
            await self._finish(ctx, "failed", None, f"{type(exc).__name__}: {exc}")
        finally:
            self._running.pop(ctx.id, None)
            self._pump()

    async def _finish(self, ctx: JobContext, status: str, result: dict | None, error: str | None) -> None:
        done, total, note = ctx._progress
        async with self.sm() as s, s.begin():
            await s.execute(update(ResearchJobRow).where(ResearchJobRow.id == ctx.id).values(
                status=status, result=result, error=error, finished_at=_now(), done=done, total=total, note=note))

    def _schedule_progress(self, ctx: JobContext) -> None:
        done, total, note = ctx._progress

        async def write() -> None:
            async with self.sm() as s, s.begin():
                await s.execute(update(ResearchJobRow).where(ResearchJobRow.id == ctx.id, ResearchJobRow.status == "running")
                                .values(done=done, total=total, note=note))

        loop = self._loop
        if loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._spawn(write())
        else:  # called from a worker thread
            asyncio.run_coroutine_threadsafe(write(), loop)


async def list_jobs(sm, owner: str, limit: int = 30) -> list[dict]:  # noqa: ANN001
    async with sm() as s:
        rows = (await s.execute(select(ResearchJobRow).where(ResearchJobRow.owner == owner)
                                .order_by(ResearchJobRow.created_at.desc()).limit(limit))).scalars().all()
    return [summary(r) for r in rows]


def summary(r: ResearchJobRow, with_result: bool = False) -> dict:
    d = {"id": r.id, "kind": r.kind, "status": r.status, "done": r.done, "total": r.total, "note": r.note,
         "params": r.params, "error": r.error, "created_at": r.created_at.isoformat() if r.created_at else None,
         "finished_at": r.finished_at.isoformat() if r.finished_at else None}
    if with_result:
        d["result"] = r.result
    return d
