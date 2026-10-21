"""Voice input for the strategy box. Takes raw audio, returns text; nothing else happens with it."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request

from trader.ai.speech import MAX_AUDIO_BYTES, SpeechUnavailable, transcribe
from trader.api.deps import CurrentUser, MeterDep
from trader.billing.meter import SPEECH_COST_USD

router = APIRouter(prefix="/speech", tags=["speech"])


@router.get("/status")
async def status(request: Request) -> dict:
    return {"configured": bool(request.app.state.settings.deepgram_api_key), "provider": "deepgram"}


@router.post("/transcribe")
async def transcribe_audio(request: Request, user: CurrentUser, meter: MeterDep) -> dict:
    """Body = the recorded audio as-is (browser MediaRecorder output, e.g. audio/webm)."""
    settings = request.app.state.settings
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "no audio received")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "recording too long; keep it under a couple of minutes")
    try:
        async with meter.counted(user, "speech_requests", cost=SPEECH_COST_USD):
            text = await asyncio.to_thread(transcribe, audio, request.headers.get("content-type", ""), settings)
    except SpeechUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    return {"text": text}
