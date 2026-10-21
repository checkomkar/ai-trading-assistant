"""Plans, usage, checkout and the payment webhook, plus a small operator API (grant a plan, list accounts)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from trader.accounts import repo as accounts
from trader.api.deps import AdminUser, CurrentUser, MeterDep
from trader.billing import razorpay as rzp
from trader.billing.plans import EVENT_RESOURCES, PAID, PLANS, STOCK_RESOURCES, margin_report, public_plans
from trader.db.models import AccountRow, BillingEventRow
from trader.logging import get_logger

log = get_logger("billing")
router = APIRouter(tags=["billing"])


def _client(request: Request) -> rzp.Razorpay:
    return rzp.Razorpay(request.app.state.settings, getattr(request.app.state, "razorpay_transport", None))


@router.get("/billing/plans")
async def plans(request: Request) -> dict:
    """Public: the pricing page reads this."""
    c = _client(request)
    out = []
    for p in public_plans():
        out.append({"id": p.id, "name": p.name, "price_inr": p.price_inr, "tagline": p.tagline, "lifetime": p.lifetime,
                    "features": list(p.features), "buyable": p.id in PAID and bool(c.plan_id(p.id)),
                    "limits": {r: p.limit(r) for r in (*EVENT_RESOURCES, *STOCK_RESOURCES)}})
    return {"plans": out, "currency": "INR", "gst_included": True, "checkout_enabled": c.configured,
            "note": "Backtests, charts and paper trading never use AI allowance."}


@router.get("/billing/usage")
async def usage(user: CurrentUser, meter: MeterDep) -> dict:
    out = await meter.summary(user)
    if not user.is_admin:
        out.pop("ai_cost_usd", None)
    out["renews"] = user.period_end.isoformat() if user.period_end else None
    out["subscription"] = bool(user.subscription)
    return out


class CheckoutIn(BaseModel):
    plan: str


@router.post("/billing/checkout")
async def checkout(request: Request, user: CurrentUser, body: CheckoutIn) -> dict:
    if user.local or not request.app.state.settings.auth_enabled:
        raise HTTPException(404, "Billing is not enabled on this server.")
    if body.plan not in PAID:
        raise HTTPException(422, "Choose the Pro or Max plan.")
    c = _client(request)
    try:
        sub = await c.create_subscription(body.plan, user.id)
    except rzp.PaymentsUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await s.get(AccountRow, user.id)
        row.billing_subscription = sub["id"]  # remembered so a webhook can be matched to the account
    return {"subscription_id": sub["id"], "key_id": request.app.state.settings.razorpay_key_id, "plan": body.plan,
            "name": PLANS[body.plan].name, "email": user.email}


@router.post("/billing/cancel")
async def cancel(request: Request, user: CurrentUser) -> dict:
    """Stops renewal. The plan keeps working until the period already paid for ends."""
    if not user.subscription:
        raise HTTPException(404, "There is no active subscription to cancel.")
    try:
        await _client(request).cancel_at_cycle_end(user.subscription)
    except rzp.PaymentsUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    return {"ok": True, "access_until": user.period_end.isoformat() if user.period_end else None}


@router.post("/billing/webhook")
async def webhook(request: Request) -> dict:
    raw = await request.body()
    c = _client(request)
    if not c.verify_webhook(raw, request.headers.get("x-razorpay-signature", "")):
        raise HTTPException(400, "bad signature")
    try:
        event = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "bad payload") from None
    etype = str(event.get("event", ""))
    event_id = request.headers.get("x-razorpay-event-id") or f"{etype}:{hashlib.sha256(raw).hexdigest()[:40]}"
    entity = ((event.get("payload") or {}).get("subscription") or {}).get("entity") or {}
    if not etype.startswith("subscription.") or not entity.get("id"):
        return {"ignored": True}  # payment.* and others: nothing to do, acknowledge so they stop retrying
    now = datetime.now(timezone.utc)
    async with request.app.state.sessionmaker() as s, s.begin():
        if await s.get(BillingEventRow, event_id) is not None:
            return {"duplicate": True}
        row = None
        acct = (entity.get("notes") or {}).get("account_id")
        if acct:
            row = await s.get(AccountRow, acct)
        if row is None:
            row = (await s.execute(select(AccountRow).where(AccountRow.billing_subscription == entity["id"]))).scalars().first()
        s.add(BillingEventRow(id=event_id, event_type=etype, account_id=row.id if row else None, received_at=now))
        if row is None or row.is_admin:
            return {"ignored": True}
        plan = c.plan_for(str(entity.get("plan_id", "")))
        start, end = rzp.cycle_bounds(entity)
        if etype in ("subscription.activated", "subscription.charged", "subscription.resumed") and plan:
            row.plan, row.billing_subscription = plan, entity["id"]
            row.period_start, row.period_end = start or now, end or (now + rzp.GRACE)
        elif etype in ("subscription.halted", "subscription.paused"):
            row.period_end = now  # payment failed: access ends now
        # cancelled / completed: leave period_end alone, the customer keeps what they paid for
        log.info("subscription event", event="billing.event", type=etype, account=row.id, plan=row.plan)
    return {"ok": True}


# ---- operator ----
class GrantIn(BaseModel):
    email: str
    plan: str
    days: int = Field(30, ge=1, le=366)


@router.post("/admin/grant")
async def grant(request: Request, _: AdminUser, body: GrantIn) -> dict:
    """Give an account a plan without a payment (a friend, a refund, a trial extension)."""
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await accounts.get_by_email(s, body.email)
        if row is None:
            raise HTTPException(404, "no such account")
        try:
            await accounts.set_plan(s, row, body.plan, body.days)
        except accounts.AccountError as exc:
            raise HTTPException(422, str(exc)) from None
    return {"email": body.email, "plan": body.plan, "days": body.days}


@router.get("/admin/accounts")
async def list_accounts(request: Request, _: AdminUser, meter: MeterDep) -> dict:
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(select(AccountRow).order_by(AccountRow.created_at.desc()).limit(200))).scalars().all()
    out = []
    for r in rows:
        p = accounts.principal_from_row(r)
        u = await meter.summary(p)
        out.append({"id": r.id, "email": r.email, "plan": p.plan, "created_at": r.created_at.isoformat(), "disabled": r.disabled,
                    "generations_used": u["usage"]["generations"]["used"], "ai_cost_usd": u["ai_cost_usd"]})
    return {"accounts": out, "margins": [margin_report(p) for p in public_plans() if p.price_inr]}
