"""Custom strategies (PRD §19): validate, translate from English, save versions, list."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select

from trader.ai.strategy_translator import StrategyTranslator, TranslatorUnavailable
from trader.db.models import StrategyRow
from trader.strategy.spec import MOMENTUM_V1, StrategySpec, render_english, semantic_errors

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


@router.post("/translate")
async def translate(request: Request, body: TranslateIn) -> dict:
    st = request.app.state.md.settings
    tr = StrategyTranslator(st.strategy_translator_model, st.anthropic_api_key)
    try:
        out = await asyncio.to_thread(tr.translate, body.text, body.base_spec)
    except TranslatorUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    return out.__dict__


@router.post("")
async def save(request: Request, body: SaveIn) -> dict:
    s, errs = _check(body.spec)
    if s is None or errs:
        raise HTTPException(422, "; ".join(errs))
    async with request.app.state.sessionmaker() as ses, ses.begin():
        latest = (await ses.execute(select(StrategyRow).where(StrategyRow.name == s.name)
                                    .order_by(StrategyRow.version.desc()).limit(1))).scalars().first()
        if latest is not None and latest.spec_hash == s.hash:
            return _row(latest)  # unchanged: don't create a duplicate version
        row = StrategyRow(id=uuid.uuid4().hex[:12], name=s.name, version=(latest.version + 1) if latest else 1,
                          spec=s.model_dump(mode="json", by_alias=True, exclude_none=True), spec_hash=s.hash,
                          english=render_english(s), source_text=body.source_text, assumptions=body.assumptions,
                          translator_model=body.translator_model, created_at=datetime.now(timezone.utc))
        ses.add(row)
    return _row(row)


@router.get("")
async def list_latest(request: Request) -> list[dict]:
    async with request.app.state.sessionmaker() as ses:
        sub = select(StrategyRow.name, func.max(StrategyRow.version).label("v")).group_by(StrategyRow.name).subquery()
        rows = (await ses.execute(
            select(StrategyRow).join(sub, (StrategyRow.name == sub.c.name) & (StrategyRow.version == sub.c.v))
            .order_by(StrategyRow.created_at.desc()))).scalars().all()
    return [_row(r) for r in rows]


@router.get("/{strategy_id}/versions")
async def versions(request: Request, strategy_id: str) -> list[dict]:
    async with request.app.state.sessionmaker() as ses:
        row = await ses.get(StrategyRow, strategy_id)
        if row is None:
            raise HTTPException(404, "strategy not found")
        rows = (await ses.execute(select(StrategyRow).where(StrategyRow.name == row.name)
                                  .order_by(StrategyRow.version.desc()))).scalars().all()
    return [_row(r) for r in rows]


@router.get("/{strategy_id}")
async def get(request: Request, strategy_id: str) -> dict:
    async with request.app.state.sessionmaker() as ses:
        row = await ses.get(StrategyRow, strategy_id)
    if row is None:
        raise HTTPException(404, "strategy not found")
    return _row(row)
