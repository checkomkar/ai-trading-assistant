"""Shoonya (Finvasia) market-data adapter — interface stub (PRD §1.1, §12.2).

Implementation plan once credentials are available:

1. Auth: `QuickAuth` with user id, SHA-256 password, TOTP (pyotp) from SHOONYA_TOTP_SECRET,
   vendor code, API key and IMEI. Persist the session token; refresh daily before 09:00 IST.
2. Instrument master: download NFO symbol master (zip/csv), filter NIFTY / BANKNIFTY
   OPTIDX + FUTIDX, map to `trader.domain.Instrument` (lot size, tick size, expiry from master
   — never from `specs.py` fallbacks).
3. WebSocket: subscribe with `NSE|26000` (NIFTY), `NSE|26009` (BANKNIFTY), `NSE|26017` (INDIA VIX)
   and `NFO|<token>` for contracts; translate touchline / depth frames to `Tick`.
4. Heartbeat: the base class reconnects on `ConnectionError`; raise it from `_stream` on socket
   close or when no frame has arrived for 15 seconds during market hours.
"""

from __future__ import annotations

from typing import AsyncIterator

from trader.config import Settings
from trader.domain import Instrument, Tick
from trader.market_data.base import MarketDataProvider


class ShoonyaMarketDataProvider(MarketDataProvider):
    name = "shoonya"
    is_simulated = False

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

    def _require_credentials(self) -> None:
        s = self._settings
        missing = [k for k, v in {
            "SHOONYA_USER_ID": s.shoonya_user_id,
            "SHOONYA_PASSWORD": s.shoonya_password,
            "SHOONYA_TOTP_SECRET": s.shoonya_totp_secret,
            "SHOONYA_VENDOR_CODE": s.shoonya_vendor_code,
            "SHOONYA_API_KEY": s.shoonya_api_key,
        }.items() if not v]
        if missing:
            raise RuntimeError(f"Shoonya provider selected but missing: {', '.join(missing)}")

    async def discover_instruments(self) -> list[Instrument]:
        self._require_credentials()
        raise NotImplementedError("Shoonya instrument master download not implemented yet")

    async def _connect(self) -> None:
        self._require_credentials()
        raise NotImplementedError("Shoonya authentication not implemented yet")

    async def _disconnect(self) -> None:
        return None

    async def _stream(self) -> AsyncIterator[Tick]:
        raise NotImplementedError("Shoonya websocket not implemented yet")
        yield  # pragma: no cover - makes this an async generator
