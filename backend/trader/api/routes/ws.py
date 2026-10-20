"""WebSocket APIs (PRD §4): /ws/market, /ws/options, /ws/signals.

Pushes are throttled snapshots of the in-memory state rather than raw tick fan-out, so a
browser never receives more than a few messages per second regardless of feed rate.

Client -> server messages (JSON):
  /ws/market   {"candles": {"symbol": "NIFTY", "timeframe": "5m"}}
  /ws/options  {"underlying": "NIFTY", "expiry": "2026-10-27" | null, "strikes": 15}
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from datetime import date

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from trader.instruments.specs import UNDERLYINGS, VIX_SYMBOL
from trader.market_data.service import MarketDataService

router = APIRouter()

MARKET_PUSH_SECONDS = 0.5
OPTIONS_PUSH_SECONDS = 2.0


async def _send(ws: WebSocket, payload: dict) -> None:
    await ws.send_json(jsonable_encoder(payload))


async def _receive_into(ws: WebSocket, state: dict) -> None:
    while True:
        raw = await ws.receive_text()
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict):
            state.update(msg)


async def _run(ws: WebSocket, state: dict, push) -> None:  # noqa: ANN001
    receiver = asyncio.create_task(_receive_into(ws, state))
    try:
        while not receiver.done():
            await push()
            await asyncio.sleep(state.get("_interval", 1.0))
        receiver.result()  # re-raise disconnect
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        receiver.cancel()


@router.websocket("/ws/market")
async def ws_market(ws: WebSocket) -> None:
    await ws.accept()
    md: MarketDataService = ws.app.state.md
    state: dict = {"_interval": MARKET_PUSH_SECONDS, "candles": {"symbol": "NIFTY", "timeframe": "5m"}}

    async def push() -> None:
        now = md.now()
        h = md.provider.health
        await _send(ws, {
            "type": "market",
            "server_time": now.isoformat(),
            "session": md.calendar.session_state(now).value,
            "simulated": md.provider.is_simulated,
            "provider": md.provider.name,
            "feed": {"connected": h.connected, "error": h.last_error, "ticks_per_second": md.ticks_per_second(),
                     "reconnects": h.reconnects, "stale": len(md.quality.stale_tokens)},
            "quotes": {s: (asdict(q) if (q := md.quote(s)) else None) for s in (*UNDERLYINGS, VIX_SYMBOL)},
        })
        sub = state.get("candles") or {}
        inst = md.candle_instrument(str(sub.get("symbol", "NIFTY")).upper())
        tf = sub.get("timeframe", "5m")
        if inst and tf in md.candles.timeframes:
            c = md.candles.current(inst.token, tf)
            if c:
                await _send(ws, {"type": "candle", "symbol": sub.get("symbol"), "timeframe": tf,
                                    "candle": c.to_dict()})

    await _run(ws, state, push)


@router.websocket("/ws/options")
async def ws_options(ws: WebSocket) -> None:
    await ws.accept()
    md: MarketDataService = ws.app.state.md
    state: dict = {"_interval": OPTIONS_PUSH_SECONDS, "underlying": "NIFTY", "expiry": None, "strikes": 15}

    async def push() -> None:
        u = str(state.get("underlying", "NIFTY")).upper()
        if u not in UNDERLYINGS:
            await _send(ws, {"type": "error", "message": f"unknown underlying {u}"})
            return
        exp_raw = state.get("expiry")
        exp = date.fromisoformat(exp_raw) if exp_raw else md.registry.resolve_expiry(u, "nearest", md.now())
        if exp is None:
            return
        strikes = state.get("strikes")
        chain = md.chain(u, exp, int(strikes) if strikes else None)
        await _send(ws, {"type": "chain", **chain.to_dict()})

    await _run(ws, state, push)


@router.websocket("/ws/signals")
async def ws_signals(ws: WebSocket) -> None:
    """Reserved for Phase 7. Sends heartbeats so clients can wire up the connection now."""
    await ws.accept()
    md: MarketDataService = ws.app.state.md
    state: dict = {"_interval": 5.0}

    async def push() -> None:
        await _send(ws, {"type": "heartbeat", "server_time": md.now().isoformat(), "signals": []})

    await _run(ws, state, push)
