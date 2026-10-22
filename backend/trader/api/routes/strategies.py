"""Custom strategies (PRD §19): validate, translate from English, save versions, list."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from trader.api.deps import CurrentUser, MeterDep
from trader.db import folder_repo
from trader.ai.providers import TranslatorUnavailable, build_provider, describe, public_message
from trader.ai.strategy_translator import StrategyTranslator
from trader.db.models import StrategyRow
from trader.strategy.spec import MOMENTUM_V1, StrategySpec, render_english, semantic_errors

from trader.logging import get_logger

log = get_logger("strategies")
router = APIRouter(prefix="/strategies", tags=["strategies"])


class SpecIn(BaseModel):
    spec: dict


class SaveIn(BaseModel):
    spec: dict
    source_text: str | None = Field(None, max_length=4000)
    assumptions: list[str] = Field(default_factory=list)
    translator_model: str | None = None


class TranslateIn(BaseModel):
    text: str = Field(min_length=5, max_length=4000)
    base_spec: dict | None = None


def _check(spec: dict) -> tuple[StrategySpec | None, list[str]]:
    try:
        s = StrategySpec.model_validate(spec)
    except ValidationError as exc:
        return None, [f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return s, semantic_errors(s)


def _row(r: StrategyRow) -> dict:
    return {"id": r.id, "name": r.name, "version": r.version, "spec": r.spec, "spec_hash": r.spec_hash,
            "english": r.english, "source_text": r.source_text, "assumptions": r.assumptions or [],
            "translator_model": r.translator_model, "created_at": r.created_at.isoformat()}


@router.post("/validate")
async def validate(body: SpecIn) -> dict:
    s, errs = _check(body.spec)
    return {"valid": s is not None and not errs, "errors": errs,
            "english": render_english(s) if s is not None else None, "spec_hash": s.hash if s else None}


@router.get("/template")
async def template() -> dict:
    s = StrategySpec.model_validate(MOMENTUM_V1)
    return {"spec": s.model_dump(mode="json", by_alias=True, exclude_none=True), "english": render_english(s)}


@router.get("/library")
async def library(user: CurrentUser) -> dict:
    """Ready-made starting points, the same for everyone. Opening one loads it into the editor; it is saved (and counted
    against the plan) only when the user saves it."""
    from trader.strategy.library import CATEGORIES, catalogue

    return {"categories": list(CATEGORIES), "strategies": catalogue()}


@router.get("/translator")
async def translator_info(request: Request, user: CurrentUser) -> dict:
    """Which provider/model translates descriptions, and whether it has a key (the key itself is never sent).
    Customers only learn whether translation is available; the provider chain is the operator's business."""
    info = describe(request.app.state.settings)
    return info if user.is_admin else {"configured": info["configured"]}


@router.post("/translate")
async def translate(request: Request, body: TranslateIn, user: CurrentUser, meter: MeterDep) -> dict:
    """One AI generation. The unit is taken first (402 when the plan is used up) and given back if no model call
    was billed, e.g. when no provider is reachable."""
    try:
        provider = build_provider(request.app.state.settings)
        async with meter.metered(user, "generations", provider) as mp:
            out = await asyncio.to_thread(StrategyTranslator(mp).translate, body.text, body.base_spec)
    except TranslatorUnavailable as exc:
        log.warning("translate unavailable", event="translate.unavailable", error=str(exc)[:600])
        raise HTTPException(503, public_message(exc, user.is_admin)) from None
    return out.__dict__


@router.post("")
async def save(request: Request, body: SaveIn, user: CurrentUser, meter: MeterDep) -> dict:
    s, errs = _check(body.spec)
    if s is None or errs:
        raise HTTPException(422, "; ".join(errs))
    async with request.app.state.sessionmaker() as ses, ses.begin():
        latest = (await ses.execute(select(StrategyRow).where(StrategyRow.owner == user.id, StrategyRow.name == s.name)
                                    .order_by(StrategyRow.version.desc()).limit(1))).scalars().first()
        if latest is not None and latest.spec_hash == s.hash:
            return _row(latest)  # unchanged: don't create a duplicate version
        if latest is None:
            await meter.check_stock(user, "strategies")  # a new version of an existing strategy is not a new strategy
        row = StrategyRow(id=uuid.uuid4().hex[:12], owner=user.id, name=s.name, version=(latest.version + 1) if latest else 1,
                          spec=s.model_dump(mode="json", by_alias=True, exclude_none=True), spec_hash=s.hash,
                          english=render_english(s), source_text=body.source_text, assumptions=body.assumptions,
                          translator_model=body.translator_model, created_at=datetime.now(timezone.utc))
        ses.add(row)
    return _row(row)


@router.get("")
async def list_latest(request: Request, user: CurrentUser) -> list[dict]:
    async with request.app.state.sessionmaker() as ses:
        sub = (select(StrategyRow.name, func.max(StrategyRow.version).label("v")).where(StrategyRow.owner == user.id)
               .group_by(StrategyRow.name).subquery())
        rows = (await ses.execute(
            select(StrategyRow).join(sub, (StrategyRow.name == sub.c.name) & (StrategyRow.version == sub.c.v))
            .where(StrategyRow.owner == user.id).order_by(StrategyRow.created_at.desc()))).scalars().all()
        where = await folder_repo.assignments(ses, user.id)
    return [{**_row(r), "folder_id": where.get(r.name)} for r in rows]


# ---- folders (declared before the /{strategy_id} routes so "folders" is not read as an id) ----
class FolderIn(BaseModel):
    name: str = Field(max_length=200)


class MoveIn(BaseModel):
    strategy_name: str
    folder_id: str | None = None  # None = back to ungrouped


def _folder_http(exc: folder_repo.FolderError) -> HTTPException:
    return HTTPException(404 if "not found" in str(exc) else 422, str(exc))


@router.get("/folders")
async def list_folders(request: Request, user: CurrentUser) -> list[dict]:
    async with request.app.state.sessionmaker() as ses:
        return await folder_repo.list_folders(ses, user.id)


@router.post("/folders")
async def create_folder(request: Request, body: FolderIn, user: CurrentUser) -> dict:
    try:
        async with request.app.state.sessionmaker() as ses, ses.begin():
            f = await folder_repo.create_folder(ses, body.name, user.id)
            return {"id": f.id, "name": f.name, "position": f.position, "count": 0}
    except folder_repo.FolderError as exc:
        raise _folder_http(exc) from None


@router.patch("/folders/{folder_id}")
async def rename_folder(request: Request, folder_id: str, body: FolderIn, user: CurrentUser) -> dict:
    try:
        async with request.app.state.sessionmaker() as ses, ses.begin():
            f = await folder_repo.rename_folder(ses, folder_id, body.name, user.id)
            return {"id": f.id, "name": f.name}
    except folder_repo.FolderError as exc:
        raise _folder_http(exc) from None


@router.delete("/folders/{folder_id}")
async def delete_folder(request: Request, folder_id: str, user: CurrentUser) -> dict:
    """The strategies inside are kept: they become ungrouped."""
    try:
        async with request.app.state.sessionmaker() as ses, ses.begin():
            await folder_repo.delete_folder(ses, folder_id, user.id)
    except folder_repo.FolderError as exc:
        raise _folder_http(exc) from None
    return {"deleted": folder_id}


@router.put("/folder-assignment")
async def move_strategy(request: Request, body: MoveIn, user: CurrentUser) -> dict:
    try:
        async with request.app.state.sessionmaker() as ses, ses.begin():
            await folder_repo.move_strategy(ses, body.strategy_name, body.folder_id, user.id)
    except folder_repo.FolderError as exc:
        raise _folder_http(exc) from None
    return {"strategy_name": body.strategy_name, "folder_id": body.folder_id}


@router.get("/{strategy_id}/versions")
async def versions(request: Request, strategy_id: str, user: CurrentUser) -> list[dict]:
    async with request.app.state.sessionmaker() as ses:
        row = await ses.get(StrategyRow, strategy_id)
        if row is None or row.owner != user.id:
            raise HTTPException(404, "strategy not found")
        rows = (await ses.execute(select(StrategyRow).where(StrategyRow.owner == user.id, StrategyRow.name == row.name)
                                  .order_by(StrategyRow.version.desc()))).scalars().all()
    return [_row(r) for r in rows]


@router.get("/{strategy_id}")
async def get(request: Request, strategy_id: str, user: CurrentUser) -> dict:
    async with request.app.state.sessionmaker() as ses:
        row = await ses.get(StrategyRow, strategy_id)
    if row is None or row.owner != user.id:
        raise HTTPException(404, "strategy not found")
    return _row(row)
