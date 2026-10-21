"""Razorpay Subscriptions: create a subscription for Checkout, cancel at period end, verify and decode webhooks.

Plans are created once in the Razorpay dashboard (monthly, INR); their ids go in RAZORPAY_PLAN_PRO / RAZORPAY_PLAN_MAX.
The browser only ever receives the public key id and a subscription id. Access is granted by the signed webhook,
never by the browser saying "payment succeeded".
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

API = "https://api.razorpay.com/v1"
GRACE = timedelta(days=2)  # a renewal webhook can land a little after the cycle ends


class PaymentsUnavailable(RuntimeError):
    pass


class Razorpay:
    def __init__(self, settings: Any, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.s = settings
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.s.razorpay_key_id and self.s.razorpay_key_secret and (self.s.razorpay_plan_pro or self.s.razorpay_plan_max))

    def plan_id(self, plan: str) -> str:
        return {"pro": self.s.razorpay_plan_pro, "max": self.s.razorpay_plan_max}.get(plan, "")

    def plan_for(self, provider_plan_id: str) -> str | None:
        return {self.s.razorpay_plan_pro: "pro", self.s.razorpay_plan_max: "max"}.get(provider_plan_id) if provider_plan_id else None

    async def _call(self, method: str, path: str, json: dict | None = None) -> dict:
        if not self.configured:
            raise PaymentsUnavailable("Payments are not set up on this server yet.")
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=20.0,
                                         auth=(self.s.razorpay_key_id, self.s.razorpay_key_secret)) as c:
                resp = await c.request(method, f"{API}{path}", json=json)
        except httpx.HTTPError as exc:
            raise PaymentsUnavailable("Could not reach the payment provider. Try again shortly.") from exc
        if resp.status_code >= 400:
            raise PaymentsUnavailable(f"The payment provider refused the request ({resp.status_code}).")
        return resp.json()

    async def create_subscription(self, plan: str, account_id: str) -> dict:
        pid = self.plan_id(plan)
        if not pid:
            raise PaymentsUnavailable(f"The {plan} plan is not set up for payment yet.")
        return await self._call("POST", "/subscriptions", {
            "plan_id": pid, "total_count": 120, "customer_notify": 1, "notes": {"account_id": account_id, "plan": plan}})

    async def cancel_at_cycle_end(self, subscription_id: str) -> dict:
        return await self._call("POST", f"/subscriptions/{subscription_id}/cancel", {"cancel_at_cycle_end": 1})

    def verify_webhook(self, raw: bytes, signature: str) -> bool:
        secret = self.s.razorpay_webhook_secret
        if not secret or not signature:
            return False
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def cycle_bounds(entity: dict) -> tuple[datetime | None, datetime | None]:
    def ts(v: Any) -> datetime | None:
        return datetime.fromtimestamp(int(v), tz=timezone.utc) if v else None

    start, end = ts(entity.get("current_start")), ts(entity.get("current_end"))
    return start, (end + GRACE if end else None)
