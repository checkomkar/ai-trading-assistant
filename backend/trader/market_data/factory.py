from __future__ import annotations

from trader.config import Settings
from trader.market_data.base import MarketDataProvider


def create_provider(settings: Settings, sessionmaker=None) -> MarketDataProvider:  # noqa: ANN001
    name = settings.market_data_provider.lower()
    if name == "simulated":
        from trader.market_data.simulated import SimConfig, SimulatedMarketDataProvider

        return SimulatedMarketDataProvider(SimConfig(
            seed=settings.sim_seed,
            tick_interval=settings.sim_tick_interval_seconds,
            disconnect_probability=settings.sim_disconnect_probability,
            ignore_session=settings.sim_ignore_session,
            risk_free_rate=settings.risk_free_rate,
        ))
    if name == "fyers":
        from trader.brokers.fyers.provider import FyersMarketDataProvider

        return FyersMarketDataProvider(settings)
    if name == "replay":
        from datetime import date

        from trader.market_data.replay import ReplayProvider

        if sessionmaker is None or not settings.replay_date:
            raise ValueError("MARKET_DATA_PROVIDER=replay needs REPLAY_DATE=YYYY-MM-DD and a database")
        unds = tuple(u.strip().upper() for u in settings.paper_underlyings.split(",") if u.strip())
        return ReplayProvider(sessionmaker, date.fromisoformat(settings.replay_date), speed=settings.replay_speed,
                              source=settings.replay_source, underlyings=unds,
                              risk_free_rate=settings.risk_free_rate)
    if name == "shoonya":
        from trader.brokers.shoonya.login import load_token, token_path
        from trader.market_data.shoonya import ShoonyaMarketDataProvider

        # The operator's own session, from `python -m trader.brokers.shoonya.login`
        return ShoonyaMarketDataProvider(settings, token_provider=lambda: load_token(token_path(settings), settings.shoonya_user_id))
    raise ValueError(f"Unknown MARKET_DATA_PROVIDER: {settings.market_data_provider}")
