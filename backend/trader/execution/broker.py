"""`Broker` interface (PRD §9.1, §12.1).

`PaperBroker` implements it today. A live adapter (M5) must implement the same interface, and
nothing above this layer (strategies, runner, risk engine) may know which one it is talking to.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from trader.execution.types import Order, OrderRequest, Position


class Broker(ABC):
    name: str = "base"
    is_live: bool = False

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> Order:
        """Submit an order. Idempotent on `request.client_order_id`."""

    @abstractmethod
    async def modify_order(self, order_id: str, *, quantity: int | None = None,
                           limit_price: float | None = None) -> Order: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Order: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_orders(self) -> list[Order]: ...
