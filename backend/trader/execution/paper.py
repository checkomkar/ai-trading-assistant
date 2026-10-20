"""PaperBroker: virtual execution against live quotes (PRD §9, M4).

* BUY fills at the live ask, SELL at the live bid. Quotes without bid/ask fall back to
  last price +/- the modelled half-spread (`ExecutionModel`), exactly as in the backtest.
* An order waits out `latency` before it fills, and then takes the quote *at fill time*, not the
  quote it was submitted against. Time only moves via `process(now)`, so replays are deterministic.
* Long-only: selling more than the open quantity is rejected. Option buying never shorts.
* Entries are refused on a stale or missing quote; exits take the last known quote (flagged),
  because refusing to exit would be worse than a stale price.
* Charges come from the same versioned `costs.yaml` model the backtester uses.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from trader.backtest.costs import CostModel, default_cost_model
from trader.backtest.pricing import ExecutionModel
from trader.domain import Instrument, Tick
from trader.execution.broker import Broker
from trader.execution.types import Order, OrderRequest, OrderStatus, OrderType, Position, Side
from trader.instruments.calendar import IST


class PaperBroker(Broker):
    name = "paper"
    is_live = False

    def __init__(
        self,
        quote_fn: Callable[[str], Tick | None],
        now_fn: Callable[[], datetime] | None = None,
        *,
        instrument_fn: Callable[[str], Instrument | None] | None = None,
        costs: CostModel | None = None,
        execution: ExecutionModel | None = None,
        latency: timedelta = timedelta(milliseconds=500),
        max_quote_age: timedelta = timedelta(seconds=15),
        capital: float = 100_000.0,
        on_fill: Callable[[Order], None] | None = None,
    ) -> None:
        self.quote_fn = quote_fn
        self.now = now_fn or (lambda: datetime.now(timezone.utc))
        self.instrument_fn = instrument_fn
        self.costs = costs or default_cost_model()
        self.exe = execution or ExecutionModel()
        self.latency, self.max_quote_age = latency, max_quote_age
        self.starting_cash = self.cash = capital
        self.on_fill = on_fill
        self._orders: dict[str, Order] = {}
        self._by_client_id: dict[str, str] = {}
        self._positions: dict[str, Position] = {}
        self._seq = 0
        self.realized_pnl = 0.0  # net of charges, since start

    # ---- Broker interface ---------------------------------------------------------------
    async def place_order(self, request: OrderRequest) -> Order:
        return self.submit(request)

    async def modify_order(self, order_id: str, *, quantity: int | None = None,
                           limit_price: float | None = None) -> Order:
        o = self._orders[order_id]
        if o.status != OrderStatus.PENDING:
            return o
        r = o.request
        o.request = OrderRequest(r.token, r.symbol, r.side, quantity or r.quantity, r.client_order_id,
                                 r.correlation_id, r.order_type,
                                 limit_price if limit_price is not None else r.limit_price, r.tag)
        return o

    async def cancel_order(self, order_id: str) -> Order:
        o = self._orders[order_id]
        if o.status == OrderStatus.PENDING:
            o.status, o.reason = OrderStatus.CANCELLED, "cancelled by user"
        return o

    async def get_positions(self) -> list[Position]:
        return self.positions()

    async def get_orders(self) -> list[Order]:
        return self.orders()

    # ---- synchronous core (the runner and tests drive this directly) ----------------------
    def submit(self, request: OrderRequest) -> Order:
        existing = self._by_client_id.get(request.client_order_id)
        if existing:
            return self._orders[existing]
        self._seq += 1
        order = Order(f"P{self._seq:06d}", request, OrderStatus.PENDING, self.now())
        self._orders[order.id] = order
        self._by_client_id[request.client_order_id] = order.id

        inst = self.instrument_fn(request.token) if self.instrument_fn else None
        if request.quantity <= 0:
            return self._reject(order, "quantity must be positive")
        if inst is not None and inst.lot_size > 1 and request.quantity % inst.lot_size:
            return self._reject(order, f"quantity {request.quantity} is not a multiple of lot size {inst.lot_size}")
        if request.order_type == OrderType.LIMIT and not request.limit_price:
            return self._reject(order, "limit order needs a limit_price")
        if request.side == Side.SELL:
            pos = self._positions.get(request.token)
            if pos is None or pos.quantity < request.quantity:
                return self._reject(order, "no open long position to sell (short selling is disabled)")
        if self.latency <= timedelta(0):
            self._try_fill(order, self.now())
        return order

    def process(self, now: datetime | None = None) -> list[Order]:
        """Fill pending orders whose latency has elapsed. Returns the orders that changed state."""
        now = now or self.now()
        changed: list[Order] = []
        for o in list(self._orders.values()):
            if o.status == OrderStatus.PENDING and now >= o.submitted_at + self.latency:
                self._try_fill(o, now)
                if o.status != OrderStatus.PENDING:
                    changed.append(o)
        return changed

    def positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.quantity > 0]

    def position(self, token: str) -> Position | None:
        p = self._positions.get(token)
        return p if p and p.quantity > 0 else None

    def orders(self) -> list[Order]:
        return list(self._orders.values())

    def order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def reset_day(self) -> None:
        """Forget filled/rejected history at the start of a session. Open positions are kept."""
        self._orders = {k: v for k, v in self._orders.items() if v.status == OrderStatus.PENDING}
        self._by_client_id = {v.request.client_order_id: k for k, v in self._orders.items()}
        self.realized_pnl = 0.0

    # ---- internals ---------------------------------------------------------------------
    def _reject(self, order: Order, reason: str) -> Order:
        order.status, order.reason = OrderStatus.REJECTED, reason
        return order

    def _quote_price(self, order: Order, tick: Tick) -> float:
        if order.request.side == Side.BUY:
            return tick.ask if tick.ask and tick.ask > 0 else self.exe.buy(tick.ltp)
        return tick.bid if tick.bid and tick.bid > 0 else self.exe.sell(tick.ltp)

    def _try_fill(self, order: Order, now: datetime) -> None:
        r = order.request
        tick = self.quote_fn(r.token)
        if tick is None:
            self._reject(order, "no quote for instrument")
            return
        age = now - tick.ts
        buy = r.side == Side.BUY
        if buy and age > self.max_quote_age:
            self._reject(order, f"stale quote ({age.total_seconds():.0f}s old)")
            return
        price = self._quote_price(order, tick)
        if r.order_type == OrderType.LIMIT:
            marketable = price <= r.limit_price if buy else price >= r.limit_price  # type: ignore[operator]
            if not marketable:
                return  # stays pending
        if not buy:
            pos = self._positions.get(r.token)
            if pos is None or pos.quantity < r.quantity:
                self._reject(order, "position no longer open")
                return
        day = now.astimezone(IST).date()
        ch = self.costs.order_charges(day, price, r.quantity, r.side.value).total
        if buy and price * r.quantity + ch > self.cash:
            self._reject(order, f"insufficient funds (need {price * r.quantity + ch:,.0f}, have {self.cash:,.0f})")
            return

        order.status, order.filled_at, order.fill_price = OrderStatus.FILLED, now, price
        order.filled_quantity, order.charges = r.quantity, ch
        if age > self.max_quote_age:
            order.reason = f"filled on a stale quote ({age.total_seconds():.0f}s old)"
        self._apply_fill(order)
        if self.on_fill:
            self.on_fill(order)

    def _apply_fill(self, o: Order) -> None:
        r, price, qty = o.request, o.fill_price, o.filled_quantity
        assert price is not None
        if r.side == Side.BUY:
            self.cash -= price * qty + o.charges
            p = self._positions.get(r.token)
            if p is None or p.quantity == 0:
                self._positions[r.token] = Position(r.token, r.symbol, qty, price, o.filled_at or self.now(),
                                                    entry_charges=o.charges)
            else:
                total = p.quantity + qty
                p.avg_price = (p.avg_price * p.quantity + price * qty) / total
                p.quantity, p.entry_charges = total, p.entry_charges + o.charges
            return
        p = self._positions[r.token]
        self.cash += price * qty - o.charges
        share = qty / p.quantity  # entry charges are released pro rata
        entry_ch = p.entry_charges * share
        net = (price - p.avg_price) * qty - o.charges - entry_ch
        p.quantity -= qty
        p.entry_charges -= entry_ch
        p.realized_pnl += net
        self.realized_pnl += net
