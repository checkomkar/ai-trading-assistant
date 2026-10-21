"""Usage metering and quota enforcement.

Flow for anything that uses a model: `reserve` a unit (the quota check and the ledger row are one transaction, under a
per-account lock so two parallel requests cannot both take the last unit), do the work, then `settle` with the real
token cost. If the work fails before any model call was billed, `refund` removes the unit. Backtests and stock limits
(saved strategies, uploaded files) use the same checks without a model.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.accounts.repo import Principal
from trader.ai.providers import LLMProvider, LLMReply
from trader.billing.plans import AI_RESOURCES, EVENT_RESOURCES, STOCK_RESOURCES, Plan, get_plan
from trader.billing.pricing import cost_usd, estimate_tokens
from trader.db.models import KnowledgeDocRow, StrategyRow, UsageEventRow

LABELS = {"generations": "AI generations", "chat_messages": "assistant messages", "speech_requests": "voice requests",
          "backtests": "backtests", "strategies": "saved strategies", "docs": "uploaded files",
          "ai_budget": "AI allowance"}
SPEECH_COST_USD = 0.01  # flat estimate per transcription request (Deepgram bills by audio minute)
IMAGE_TOKENS_IN = 1500  # a described image is billed as roughly this many input tokens


class QuotaExceeded(Exception):
    """The plan does not allow this. Rendered as HTTP 402 with a body the UI turns into an upgrade prompt."""

    def __init__(self, resource: str, plan: Plan, limit: float | None, used: float, message: str | None = None) -> None:
        self.resource, self.plan_id, self.limit, self.used = resource, plan.id, limit, used
        what = LABELS.get(resource, resource)
        if message is None:
            message = (f"You have used all of the {what} on the {plan.name} plan."
                       if resource != "ai_budget" else f"The {plan.name} plan's monthly AI allowance is used up.")
            if plan.id in ("trial", "pro"):
                message += " Upgrade your plan to continue."
        super().__init__(message)
        self.message = message

    def body(self) -> dict:
        return {"code": "quota_exceeded", "resource": self.resource, "plan": self.plan_id,
                "limit": self.limit, "used": self.used, "message": self.message}


@dataclass
class Hold:
    event_id: int
    kind: str


class MeteredProvider:
    """Wraps a provider and records what each call used (tokens and model), without changing what it returns."""

    def __init__(self, inner: LLMProvider) -> None:
        self.inner = inner
        self.calls: list[tuple[str, int, int]] = []  # (model label, tokens in, tokens out)

    @property
    def name(self) -> str:
        return self.inner.name

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def label(self) -> str:
        return self.inner.label

    def complete(self, system: str, messages: list[dict], schema: dict, tools: list[dict] | None = None) -> LLMReply:
        reply = self.inner.complete(system, messages, schema, tools)
        tin, tout = reply.tokens_in, reply.tokens_out
        if not (tin or tout):  # the provider did not say: estimate rather than record a free call
            tin = estimate_tokens(system + "".join(str(m.get("content", "")) for m in messages))
            tout = estimate_tokens(reply.text)
        self.calls.append((reply.model or self.inner.label, tin, tout))
        return reply

    def describe_image(self, data: bytes, mime: str, prompt: str) -> str:
        text = self.inner.describe_image(data, mime, prompt)
        self.calls.append((self.inner.label, IMAGE_TOKENS_IN + estimate_tokens(prompt), estimate_tokens(text)))
        return text

    def totals(self) -> tuple[float, int, int, str | None]:
        cost = sum(cost_usd(m, i, o) for m, i, o in self.calls)
        model = self.calls[-1][0] if self.calls else None
        return cost, sum(c[1] for c in self.calls), sum(c[2] for c in self.calls), model


class Meter:
    def __init__(self, sessionmaker: async_sessionmaker) -> None:
        self._sm = sessionmaker
        self._locks: dict[str, asyncio.Lock] = {}  # per account, owned by this app instance

    def _lock(self, owner: str) -> asyncio.Lock:
        return self._locks.setdefault(owner, asyncio.Lock())

    # ---- reading ----
    @staticmethod
    def _window(p: Principal):
        return (UsageEventRow.created_at >= p.period_start,) if p.period_start is not None else ()

    async def _counts(self, s, p: Principal) -> tuple[dict[str, int], float]:
        rows = (await s.execute(
            select(UsageEventRow.kind, func.coalesce(func.sum(UsageEventRow.units), 0), func.coalesce(func.sum(UsageEventRow.cost_usd), 0.0))
            .where(UsageEventRow.owner == p.id, *self._window(p)).group_by(UsageEventRow.kind))).all()
        used = {k: int(u) for k, u, _ in rows}
        return used, float(sum(c for _, _, c in rows))

    async def _stock(self, s, p: Principal, resource: str) -> int:
        if resource == "strategies":
            return int((await s.execute(select(func.count(func.distinct(StrategyRow.name))).where(StrategyRow.owner == p.id))).scalar_one())
        return int((await s.execute(select(func.count()).select_from(KnowledgeDocRow).where(KnowledgeDocRow.owner == p.id))).scalar_one())

    async def summary(self, p: Principal) -> dict:
        plan = get_plan(p.plan)
        async with self._sm() as s:
            used, cost = await self._counts(s, p)
            usage = {}
            for r in EVENT_RESOURCES:
                lim = plan.limit(r)
                usage[r] = {"used": used.get(r, 0), "limit": lim, "remaining": None if lim is None else max(lim - used.get(r, 0), 0)}
            for r in STOCK_RESOURCES:
                n, lim = await self._stock(s, p, r), plan.limit(r)
                usage[r] = {"used": n, "limit": lim, "remaining": None if lim is None else max(lim - n, 0)}
        budget = plan.ai_budget_usd
        return {
            "plan": {"id": plan.id, "name": plan.name, "price_inr": plan.price_inr, "lifetime": plan.lifetime},
            "period": {"start": p.period_start.isoformat() if p.period_start else None,
                       "end": p.period_end.isoformat() if p.period_end else None},
            "usage": usage,
            "ai_allowance_pct": None if not budget else min(round(100 * cost / budget), 100),
            "ai_cost_usd": round(cost, 4),  # shown to admins only by the route
        }

    # ---- enforcing ----
    async def reserve(self, p: Principal, resource: str) -> Hold:
        """Check the plan's limit for `resource` (and the AI cost ceiling for model calls) and take one unit."""
        plan = get_plan(p.plan)
        async with self._lock(p.id):
            async with self._sm() as s, s.begin():
                used, cost = await self._counts(s, p)
                lim = plan.limit(resource) if resource in EVENT_RESOURCES else None
                if lim is not None and used.get(resource, 0) >= lim:
                    raise QuotaExceeded(resource, plan, lim, used.get(resource, 0))
                if resource in AI_RESOURCES or resource == "ai_other":
                    if plan.ai_budget_usd is not None and cost >= plan.ai_budget_usd:
                        raise QuotaExceeded("ai_budget", plan, plan.ai_budget_usd, cost)
                row = UsageEventRow(owner=p.id, kind=resource, units=0 if resource == "ai_other" else 1, status="held",
                                    created_at=datetime.now(timezone.utc))
                s.add(row)
                await s.flush()
                return Hold(row.id, resource)

    async def check_stock(self, p: Principal, resource: str, adding: int = 1) -> None:
        plan = get_plan(p.plan)
        lim = plan.limit(resource)
        if lim is None:
            return
        async with self._sm() as s:
            n = await self._stock(s, p, resource)
        if n + adding > lim:
            raise QuotaExceeded(resource, plan, lim, n, f"Your {plan.name} plan allows {lim} {LABELS[resource]}. "
                                "Delete some or upgrade your plan.")

    async def settle(self, hold: Hold, *, cost: float = 0.0, tokens_in: int = 0, tokens_out: int = 0, model: str | None = None) -> None:
        async with self._sm() as s, s.begin():
            row = await s.get(UsageEventRow, hold.event_id)
            if row is not None:
                row.status, row.cost_usd, row.tokens_in, row.tokens_out, row.model = "done", cost, tokens_in, tokens_out, model

    async def refund(self, hold: Hold) -> None:
        async with self._sm() as s, s.begin():
            await s.execute(delete(UsageEventRow).where(UsageEventRow.id == hold.event_id))

    @asynccontextmanager
    async def metered(self, p: Principal, resource: str, provider: LLMProvider):
        """`async with meter.metered(p, "generations", provider) as mp:` — use `mp` as the provider. Settles the real
        token cost on exit; if the work failed before any model call was billed, the unit is given back."""
        hold = await self.reserve(p, resource)
        mp = MeteredProvider(provider)
        try:
            yield mp
        except BaseException:
            await self._finish(hold, mp, failed=True)
            raise
        await self._finish(hold, mp)

    async def _finish(self, hold: Hold, mp: MeteredProvider, failed: bool = False) -> None:
        if failed and not mp.calls:
            await asyncio.shield(self.refund(hold))
            return
        cost, tin, tout, model = mp.totals()
        await asyncio.shield(self.settle(hold, cost=cost, tokens_in=tin, tokens_out=tout, model=model))

    @asynccontextmanager
    async def counted(self, p: Principal, resource: str, cost: float = 0.0):
        """For work with no model of ours in it (a backtest, a transcription): one unit, refunded if it raises."""
        hold = await self.reserve(p, resource)
        try:
            yield
        except BaseException:
            await asyncio.shield(self.refund(hold))
            raise
        await asyncio.shield(self.settle(hold, cost=cost))
