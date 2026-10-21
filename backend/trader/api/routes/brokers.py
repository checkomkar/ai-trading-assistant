"""Bring-your-own broker keys: the catalogue, and a customer's saved connections (secrets are write-only)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from trader.accounts import vault
from trader.api.deps import CurrentUser
from trader.brokers.catalog import CATALOG
from trader.db.models import BrokerConnectionRow

router = APIRouter(prefix="/brokers", tags=["brokers"])


class ConnectIn(BaseModel):
    broker: str
    values: dict[str, str] = Field(default_factory=dict)


def _view(r: BrokerConnectionRow) -> dict:
    spec = CATALOG.get(r.broker)
    return {"id": r.id, "broker": r.broker, "name": spec.name if spec else r.broker, "public": r.public,
            "status": spec.status if spec else "unknown", "created_at": r.created_at.isoformat(),
            "secrets_saved": [f.key for f in spec.fields if f.secret] if spec else []}


@router.get("/catalog")
async def catalog(request: Request, user: CurrentUser) -> dict:
    return {"vault_ready": bool(request.app.state.settings.credential_key),
            "brokers": [{"id": b.id, "name": b.name, "status": b.status, "data_note": b.data_note, "docs_url": b.docs_url,
                         "daily_login": b.daily_login,
                         "fields": [{"key": f.key, "label": f.label, "secret": f.secret} for f in b.fields]}
                        for b in CATALOG.values()]}


@router.get("/connections")
async def connections(request: Request, user: CurrentUser) -> list[dict]:
    async with request.app.state.sessionmaker() as s:
        rows = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == user.id)
                                .order_by(BrokerConnectionRow.created_at))).scalars().all()
    return [_view(r) for r in rows]


@router.post("/connections")
async def connect(request: Request, user: CurrentUser, body: ConnectIn) -> dict:
    """Save (or replace) the keys for one broker. Nothing is sent to the broker here; `status` says whether an adapter
    exists yet that will use them."""
    spec = CATALOG.get(body.broker)
    if spec is None:
        raise HTTPException(404, "Unknown broker.")
    values = {f.key: body.values.get(f.key, "").strip() for f in spec.fields}
    missing = [f.label for f in spec.fields if not values[f.key]]
    if missing:
        raise HTTPException(422, "Fill in: " + ", ".join(missing))
    if any(len(v) > 512 for v in values.values()):
        raise HTTPException(422, "A value is too long.")
    try:
        blob = vault.seal(values, request.app.state.settings.credential_key)
    except vault.VaultUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    public = {f.key: values[f.key] for f in spec.fields if not f.secret}
    async with request.app.state.sessionmaker() as s, s.begin():
        row = (await s.execute(select(BrokerConnectionRow).where(BrokerConnectionRow.owner == user.id,
                                                                  BrokerConnectionRow.broker == spec.id))).scalars().first()
        if row is None:
            row = BrokerConnectionRow(id=uuid.uuid4().hex[:12], owner=user.id, broker=spec.id, public=public, secret_blob=blob,
                                      created_at=datetime.now(timezone.utc))
            s.add(row)
        else:
            row.public, row.secret_blob = public, blob
        await s.flush()
        return _view(row)


@router.delete("/connections/{conn_id}")
async def disconnect(request: Request, user: CurrentUser, conn_id: str) -> dict:
    async with request.app.state.sessionmaker() as s, s.begin():
        row = await s.get(BrokerConnectionRow, conn_id)
        if row is None or row.owner != user.id:
            raise HTTPException(404, "Connection not found.")
        await s.delete(row)
    return {"deleted": conn_id}
