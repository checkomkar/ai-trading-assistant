"""Centralised configuration (PRD §0.3). Loaded from environment / `.env`."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
CONFIG_DIR = BACKEND_DIR / "config"

LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THE_RISK"


class TradingMode(str, Enum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    LIVE = "LIVE"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    trading_mode: TradingMode = TradingMode.PAPER
    live_trading_confirm: str = ""
    market_timezone: str = "Asia/Kolkata"

    market_data_provider: str = "simulated"
    sim_ignore_session: bool = True
    sim_seed: int | None = None
    sim_tick_interval_seconds: float = 1.0
    sim_disconnect_probability: float = 0.0
    sim_backfill_minutes: int = 375  # synthetic history for SIM:* instruments on an empty DB

    database_url: str = "sqlite+aiosqlite:///./data/trader.db"
    tick_store_strike_window: int = 3
    option_snapshot_interval_seconds: int = 60
    redis_url: str = ""

    history_backfill_days: int = 5  # startup gap-fill for index/futures candles (broker providers)

    fyers_client_id: str = ""  # App ID, e.g. "XXXXXXXX-100"
    fyers_secret_key: str = Field(default="", repr=False)
    fyers_redirect_uri: str = "http://127.0.0.1:8765/fyers/callback"
    fyers_max_symbols: int = 100  # Standard plan socket cap; Prime allows 5000
    fyers_strikes_each_side: int = 10
    fyers_expiries_per_underlying: int = 1
    fyers_expired_intraday_daily_limit: int = 10  # Standard plan quota for expired F&O intraday calls
    # Intraday history depth. Measured 2026-10-20 on Standard: requests older than ~365 days are rejected
    # with "Invalid input". Prime serves 5+ years; raise this if you subscribe.
    fyers_intraday_history_days: int = 364

    # Natural-language strategy translator (PRD §19). The Anthropic SDK also finds credentials
    # from `ant auth login` profiles, so an empty key here does not necessarily disable it.
    anthropic_api_key: str = Field(default="", repr=False)
    strategy_translator_model: str = "claude-opus-5-5"

    shoonya_user_id: str = ""
    shoonya_password: str = Field(default="", repr=False)
    shoonya_totp_secret: str = Field(default="", repr=False)
    shoonya_vendor_code: str = ""
    shoonya_api_key: str = Field(default="", repr=False)
    shoonya_imei: str = ""

    risk_max_daily_loss: float = 5000
    risk_max_trades_per_day: int = 5
    risk_max_open_positions: int = 1
    risk_max_lots_per_trade: int = 1
    risk_max_consecutive_losses: int = 3
    risk_max_risk_per_trade: float = 4000  # premium x quantity x stop-loss %, in rupees
    risk_max_premium_exposure: float = 60000
    risk_max_underlying_exposure: float = 40000
    risk_max_data_age_seconds: float = 10

    # Paper trading (PRD M4). Nothing here can place a live order: the only broker is PaperBroker.
    paper_autostart: bool = False  # start the strategy runner with the API
    paper_underlyings: str = "NIFTY"  # comma separated
    paper_timeframe: str = "5m"  # must be a timeframe the collector builds live (1m, 5m)
    paper_strategy: str = "momentum"  # momentum | spec
    paper_latency_ms: int = 500
    paper_capital: float = 100000

    # MARKET_DATA_PROVIDER=replay: rehearse a stored day through the live pipeline (PRD §14.1)
    replay_date: str = ""  # YYYY-MM-DD, a stored trading day
    replay_speed: float = 60  # 1 = real time; 0 = as fast as possible
    replay_source: str = "real"  # real | simulated

    risk_free_rate: float = 0.065

    api_cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    @field_validator("sim_seed", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        # `SIM_SEED=` in .env means "not set", not an invalid integer.
        return None if isinstance(v, str) and not v.strip() else v

    @model_validator(mode="after")
    def _guard_live(self) -> "Settings":
        # PRD §14.10: LIVE needs a second, explicit confirmation.
        if self.trading_mode == TradingMode.LIVE and self.live_trading_confirm != LIVE_CONFIRM_PHRASE:
            raise ValueError(
                f"TRADING_MODE=LIVE requires LIVE_TRADING_CONFIRM={LIVE_CONFIRM_PHRASE}"
            )
        return self

    @property
    def data_dir(self) -> Path:
        path = BACKEND_DIR / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.market_timezone)

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.api_cors_origins.split(",") if o.strip()]

    @property
    def resolved_database_url(self) -> str:
        """SQLite relative paths are resolved against the backend dir, and the folder is created."""
        url = self.database_url
        prefix = "sqlite+aiosqlite:///"
        if url.startswith(prefix) and not url.startswith(prefix + "/"):
            rel = url[len(prefix):]
            if rel != ":memory:":
                path = (BACKEND_DIR / rel).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                return prefix + path.as_posix()
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
