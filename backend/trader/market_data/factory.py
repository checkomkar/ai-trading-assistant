from __future__ import annotations

from trader.config import Settings
from trader.market_data.base import MarketDataProvider


def create_provider(settings: Settings) -> MarketDataProvider:
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
    if name == "shoonya":
        from trader.market_data.shoonya import ShoonyaMarketDataProvider

        return ShoonyaMarketDataProvider(settings)
    raise ValueError(f"Unknown MARKET_DATA_PROVIDER: {settings.market_data_provider}")
