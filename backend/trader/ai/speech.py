"""Speech-to-text through Deepgram's pre-recorded API, so a strategy can be described by voice.

The transcript only fills the text box: the user reads and edits it, and the translator and the deterministic
validator still stand between the words and anything that runs (PRD §2).
"""

from __future__ import annotations

from typing import Any

import httpx

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
# Trading terms the recogniser would otherwise turn into ordinary words.
KEYTERMS = ["NIFTY", "BANKNIFTY", "VWAP", "EMA", "RSI", "ADX", "MACD", "ATR", "Bollinger", "breakout", "stop loss",
            "call", "put", "CE", "PE", "trailing", "opening range"]
MAX_AUDIO_BYTES = 10 * 1024 * 1024


class SpeechUnavailable(RuntimeError):
    """No key, a rejected key, or Deepgram could not be reached."""


def transcribe(audio: bytes, content_type: str, settings: Any, client: httpx.Client | None = None) -> str:
    if not settings.deepgram_api_key:
        raise SpeechUnavailable("No Deepgram key. Set DEEPGRAM_API_KEY in .env.")
    params: list[tuple[str, str]] = [("model", settings.deepgram_model), ("language", settings.deepgram_language),
                                     ("smart_format", "true")]
    if settings.deepgram_model.startswith("nova-3"):  # keyterm prompting exists on Nova-3; older models use keywords
        params += [("keyterm", k) for k in KEYTERMS]
    headers = {"Authorization": f"Token {settings.deepgram_api_key}", "Content-Type": content_type or "audio/webm"}
    try:
        resp = (client or httpx).post(DEEPGRAM_URL, params=params, content=audio, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise SpeechUnavailable("Could not reach Deepgram.") from exc
    if resp.status_code in (401, 403):
        raise SpeechUnavailable("Deepgram rejected the API key.")
    if resp.status_code >= 400:
        raise SpeechUnavailable(f"Deepgram error {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()["results"]["channels"][0]["alternatives"][0]["transcript"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise SpeechUnavailable("Unexpected response from Deepgram.") from exc
