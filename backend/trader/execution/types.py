"""Order, fill and position types shared by every `Broker` implementation (PRD §9, §12.1)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    PENDING = "PENDING"  # accepted, waiting out the latency / for a marketable limit price
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True)
class OrderRequest:
    token: str
    symbol: str
    side: Side
    quantity: int  # units (lots x lot size)
    client_order_id: str  # idempotency key: resubmitting the same key returns the same order
    correlation_id: str | None = None
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    tag: str = ""  # free text, e.g. "entry" / "exit:stop"


@dataclass
class Order:
    id: str
    request: OrderRequest
    status: OrderStatus
    submitted_at: datetime
    filled_at: datetime | None = None
    fill_price: float | None = None
    filled_quantity: int = 0
    charges: float = 0.0
    reason: str | None = None  # why it was rejected / cancelled

    def to_dict(self) -> dict:
        r = self.request
        return {
            "id": self.id, "client_order_id": r.client_order_id, "correlation_id": r.correlation_id,
            "token": r.token, "symbol": r.symbol, "side": r.side.value, "quantity": r.quantity,
            "order_type": r.order_type.value, "limit_price": r.limit_price, "tag": r.tag,
            "status": self.status.value, "submitted_at": self.submitted_at.isoformat(),
            "filled_at": self.filled_at.isoformat() if self.filled_at else None,
            "fill_price": self.fill_price, "filled_quantity": self.filled_quantity,
            "charges": round(self.charges, 2), "reason": self.reason,
        }


    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        req = OrderRequest(d["token"], d["symbol"], Side(d["side"]), d["quantity"], d["client_order_id"],
                           d.get("correlation_id"), OrderType(d["order_type"]), d.get("limit_price"), d.get("tag", ""))
        at = lambda k: datetime.fromisoformat(d[k]) if d.get(k) else None  # noqa: E731
        return cls(d["id"], req, OrderStatus(d["status"]), at("submitted_at"), at("filled_at"),  # type: ignore[arg-type]
                   d.get("fill_price"), d.get("filled_quantity", 0), d.get("charges", 0.0), d.get("reason"))


@dataclass
class Position:
    token: str
    symbol: str
    quantity: int  # long-only: option buying never goes short
    avg_price: float
    opened_at: datetime
    entry_charges: float = 0.0
    realized_pnl: float = 0.0  # net of charges, accumulated across closes of this token today
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["opened_at"] = self.opened_at.isoformat()
        return d
