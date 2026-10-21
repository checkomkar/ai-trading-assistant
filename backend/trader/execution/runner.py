"""Live paper-trading runner (PRD §9.2, M4).

    closed bar -> FeatureEngine -> strategy -> Signal -> RiskEngine -> PaperBroker -> position
                  (shared with the backtest)             (deterministic gate)       (virtual)

The strategy only ever returns a `Signal` or `NoTrade`. This module owns the one path to the broker,
and every entry goes through `RiskEngine.check` first. Nothing produced by an LLM reaches here: a
custom strategy is a validated `StrategySpec` interpreted by `RuleStrategy` (PRD §2.1, §19.2).

Every step is written to the audit trail under one `correlation_id`:
    signal.generated -> risk.approved | risk.rejected -> order.placed -> order.filled -> exit.triggered
    -> order.filled -> trade.closed

Time comes from `now_fn` (the market-data clock), never from the wall clock, so a replayed day runs
the same code at any speed. `tick(now)` is the single heartbeat: it fills orders, then manages stops.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import async_sessionmaker

from trader.backtest import data as bt_data
from trader.backtest.runner import BacktestRequest, build_strategy
from trader.domain import Instrument
from trader.execution.paper import PaperBroker
from trader.execution.state import build_snapshot, signal_from_dict, signal_to_dict
from trader.execution.shadow import shadow_order
from trader.execution.store import PaperStore
from trader.execution.types import Order, OrderRequest, OrderStatus, Side
from trader.features import FeatureEngine, Features
from trader.instruments.calendar import IST
from trader.instruments.specs import get_spec
from trader.instruments.strikes import OptionType, moneyness_offset
from trader.logging import get_logger
from trader.market_data.candles import TIMEFRAMES, Candle
from trader.market_data.service import TOPIC_CANDLES, MarketDataService
from trader.risk import OrderIntent, RiskContext, RiskEngine
from trader.strategy.base import Direction, MarketState, NoTrade, Signal

log = get_logger("paper")


@dataclass
class LaneConfig:
    """One strategy running on one underlying. The strategy definition is the same request object the
    backtester takes, so a paper run and a backtest of the same definition cannot drift apart."""

    underlying: str = "NIFTY"
    timeframe: str = "5m"
    strategy: str = "momentum"  # momentum | spec
    strategy_params: dict = field(default_factory=dict)
    spec: dict | None = None
    strategy_version_id: str | None = None
    engine_params: dict = field(default_factory=dict)  # exits (trail, eod, hold) and lots
    bar_grace_seconds: float = 3.0  # wait this long for the futures/VIX bar of the same bucket
    warmup_days: int = 7


@dataclass
class _Live:
    """An entry/position being managed. state: ENTRY -> OPEN -> EXIT."""

    cid: str
    signal: Signal
    inst: Instrument
    quantity: int
    state: str
    entry_order: Order
    exit_order: Order | None = None
    entry_price: float = 0.0
    entry_time: datetime | None = None
    stop: float = 0.0
    initial_stop: float = 0.0
    target: float = 0.0
    high_water: float = 0.0
    low_water: float = 0.0
    last_bid: float = 0.0
    bars: int = 0
    exit_reason: str | None = None
    exit_attempts: int = 0


class Lane:
    def __init__(self, cfg: LaneConfig) -> None:
        self.cfg = cfg
        req = BacktestRequest(underlying=cfg.underlying, timeframe=cfg.timeframe, strategy=cfg.strategy,
                              strategy_params=cfg.strategy_params, spec=cfg.spec,
                              strategy_version_id=cfg.strategy_version_id, engine_params=cfg.engine_params)
        self.strategy, self.eng, self.roll_on_expiry, self.params, _ = build_strategy(req)
        self.label = cfg.spec["name"] if cfg.strategy == "spec" and cfg.spec else self.strategy.name
        self.features = FeatureEngine(_minutes(cfg.timeframe))
        self.live: _Live | None = None
        self.last_bar: Candle | None = None
        self.last_features: Features | None = None
        self.last_decision: dict | None = None
        self.signals: deque[dict] = deque(maxlen=50)
        self.warmed_bars = 0
        # bar assembly: futures / vix candles of the same bucket arrive separately
        self.fut: dict[datetime, Candle] = {}
        self.vix: dict[datetime, float] = {}
        self.pending: deque[tuple[Candle, datetime]] = deque()  # closed bars not yet handed to the strategy
        self.enabled = True


def _minutes(tf: str) -> int:
    return int(TIMEFRAMES[tf].total_seconds() // 60)


def _live_to_dict(lane: Lane, lv: _Live) -> dict:
    """What a restart needs to keep managing an open position (PRD §22.3). Not stored: the exit order
    (a restarted runner places a fresh one) and the instrument (looked up again from the symbol master)."""
    return {
        "cid": lv.cid, "underlying": lane.cfg.underlying, "strategy": lane.label, "token": lv.inst.token,
        "symbol": lv.inst.symbol, "quantity": lv.quantity, "state": lv.state,
        "signal": signal_to_dict(lv.signal), "entry_order": {**lv.entry_order.to_dict(), "charges": lv.entry_order.charges},
        "entry_price": lv.entry_price, "entry_time": lv.entry_time.isoformat() if lv.entry_time else None,
        "stop": lv.stop, "initial_stop": lv.initial_stop, "target": lv.target, "high_water": lv.high_water,
        "low_water": lv.low_water, "last_bid": lv.last_bid, "bars": lv.bars, "exit_reason": lv.exit_reason,
        "exit_attempts": lv.exit_attempts}


def _live_from_dict(d: dict, inst: Instrument) -> _Live:
    return _Live(d["cid"], signal_from_dict(d["signal"]), inst, d["quantity"], d["state"],
                 Order.from_dict(d["entry_order"]), None, d["entry_price"],
                 datetime.fromisoformat(d["entry_time"]) if d.get("entry_time") else None, d["stop"],
                 d["initial_stop"], d["target"], d["high_water"], d["low_water"], d["last_bid"], d["bars"],
                 d.get("exit_reason"), d.get("exit_attempts", 0))


class PaperRunner:
    def __init__(self, md: MarketDataService, broker: PaperBroker, risk: RiskEngine, store: PaperStore,
                 lanes: list[LaneConfig], *, sessionmaker: async_sessionmaker | None = None,
                 now_fn: Callable[[], datetime] | None = None, tick_interval: float = 1.0) -> None:
        self.md, self.broker, self.risk, self.store = md, broker, risk, store
        self.sessionmaker = sessionmaker
        self.now = now_fn or md.now
        self.tick_interval = tick_interval
        self.lanes = [Lane(c) for c in lanes]
        self.data_source = md.provider.name
        self.shadow_slippage_pct = 1.0  # entry slippage cap used for the shadow (never-sent) live order
        self.option_pricing = getattr(md.provider, "option_pricing", "live quotes")
        self.running = False
        self.started_at: datetime | None = None
        self.errors = 0
        self.last_error: str | None = None
        self._tasks: list[asyncio.Task] = []
        self._day: date | None = risk.day  # a restored same-day engine must not be rolled (and reset) again
        self.restore_report: dict | None = None  # set by PaperService: what was restored at start-up
        self._routes: dict[str, list[tuple[Lane, str]]] = {}  # token -> (lane, "bar" | "fut" | "vix")

    # ---- lifecycle ------------------------------------------------------------------------
    async def start(self, *, warmup: bool = True, restored: list[dict] | None = None) -> None:
        """`restored` are open positions from a saved snapshot (or a previous run of this process); each is
        handed to the lane that trades it, or the start is refused (PRD §22.3)."""
        self.started_at = self.now()
        if restored:
            self.adopt(restored)
        for lane in self.lanes:
            self._bind_tokens(lane)
            if warmup:
                await self.warmup(lane)
        self.running = True
        self.store.emit("runner.started", None, {
            "lanes": [{"underlying": ln.cfg.underlying, "strategy": ln.label, "timeframe": ln.cfg.timeframe,
                       "warmed_bars": ln.warmed_bars} for ln in self.lanes],
            "data_source": self.data_source, "option_pricing": self.option_pricing, "broker": self.broker.name,
            "limits": self.risk.status()["limits"],
            "strategy_params": [ln.params for ln in self.lanes]})
        self._tasks = [asyncio.create_task(self._consume(), name="paper-candles"),
                       asyncio.create_task(self._heartbeat(), name="paper-tick")]

    async def stop(self) -> None:
        self.running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        # Nothing manages an order while the runner is down: withdraw what is in flight, so a later start
        # (which re-places the exit of a position that was leaving) cannot race a stale order.
        now = self.now()
        for lane in self.lanes:
            live = lane.live
            if live is not None and live.state == "ENTRY":
                await self._withdraw_entry(lane, live, now)
            elif live is not None and live.exit_order is not None:
                await self.broker.cancel_order(live.exit_order.id)
                live.exit_order = None
        self.store.emit("runner.stopped", None, {"open_positions": [ln.live.cid for ln in self.lanes if ln.live]})
        self._save_state(now)
        await self.store.flush()

    def _bind_tokens(self, lane: Lane) -> None:
        und = lane.cfg.underlying
        for kind, inst in (("bar", self.md.registry.index(und)), ("fut", self.md.registry.future(und)),
                           ("vix", self.md.registry.index("INDIAVIX"))):
            if inst is not None:
                self._routes.setdefault(inst.token, []).append((lane, kind))

    async def warmup(self, lane: Lane) -> None:
        """Feed stored closed bars through the FeatureEngine (no signals), so indicators start warm."""
        if not self.sessionmaker:
            return
        now = self.now()
        today = now.astimezone(IST).date()
        source = "simulated" if self.md.provider.is_simulated else "real"
        tf = TIMEFRAMES[lane.cfg.timeframe]
        try:
            async with self.sessionmaker() as s:
                data = await bt_data.load(s, lane.cfg.underlying, lane.cfg.timeframe,
                                          today - timedelta(days=lane.cfg.warmup_days), today, source)
        except Exception as exc:  # noqa: BLE001
            self.store.emit("runner.warmup_failed", None, {"underlying": lane.cfg.underlying, "error": str(exc)})
            return
        for bar in data.bars:
            if bar.ts + tf <= now:
                lane.features.update(bar, data.futures.get(bar.ts), data.vix.get(bar.ts))
                lane.warmed_bars += 1

    # ---- inputs -----------------------------------------------------------------------------
    def on_candle(self, c: Candle) -> None:
        for lane, kind in self._routes.get(c.token, ()):
            if c.timeframe != lane.cfg.timeframe:
                continue
            if kind == "bar":
                lane.pending.append((c, self.now()))
            elif kind == "fut":
                lane.fut[c.ts] = c
            else:
                lane.vix[c.ts] = c.close

    async def _consume(self) -> None:
        with self.md.bus.subscribe(TOPIC_CANDLES) as q:
            while True:
                self.on_candle(await q.get())

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.tick_interval)
            try:
                await self.tick()
                await self.store.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad tick must not stop the runner
                self._record_error("tick", exc)

    def _record_error(self, what: str, exc: Exception) -> None:
        self.errors += 1
        self.last_error = f"{type(exc).__name__}: {exc}"
        log.exception("paper step failed", event="paper.tick_failed", step=what)
        self.store.emit("runner.error", None, {"error": self.last_error, "step": what})

    @contextmanager
    def _isolated(self, what: str):
        """A failure in one step of the heartbeat (a strategy that raises on a bar, say) is recorded and must
        not skip the steps after it: order fills and stop management have to keep running."""
        try:
            yield
        except Exception as exc:  # noqa: BLE001
            self._record_error(what, exc)

    # ---- the heartbeat -----------------------------------------------------------------------
    async def tick(self, now: datetime | None = None) -> None:
        now = now or self.now()
        with self._isolated("roll_day"):
            self._roll_day(now)
        for lane in self.lanes:
            with self._isolated(f"bar:{lane.cfg.underlying}"):
                await self._drain_bar(lane, now)
        for order in self.broker.process(now):
            with self._isolated(f"fill:{order.id}"):
                self._on_order_update(order, now)
        for lane in self.lanes:
            with self._isolated(f"manage:{lane.cfg.underlying}"):
                await self._manage(lane, now)
        with self._isolated("daily_loss"):
            await self._enforce_daily_loss(now)
        with self._isolated("save_state"):
            self._save_state(now)

    def _roll_day(self, now: datetime) -> None:
        day = now.astimezone(IST).date()
        if day != self._day:
            self._day = day
            self.risk.roll_day(day)
            self.broker.reset_day()
            for lane in self.lanes:
                lane.fut.clear()
                lane.vix.clear()

    async def _drain_bar(self, lane: Lane, now: datetime) -> None:
        while lane.pending:  # oldest first; a late heartbeat must not drop a bar and leave a hole in the indicators
            bar, got_at = lane.pending[0]
            fut_inst = self.md.registry.future(lane.cfg.underlying)
            fut_ready = fut_inst is None or bar.ts in lane.fut
            if not fut_ready and (now - got_at).total_seconds() < lane.cfg.bar_grace_seconds:
                return
            lane.pending.popleft()
            for old in [k for k in lane.fut if k < bar.ts - timedelta(hours=1)]:
                lane.fut.pop(old, None)
                lane.vix.pop(old, None)
            await self.on_bar(lane, bar, lane.fut.get(bar.ts), lane.vix.get(bar.ts), now)

    # ---- bar close: features, exit rules, signal -----------------------------------------------
    async def on_bar(self, lane: Lane, bar: Candle, fut: Candle | None, vix: float | None,
                     now: datetime) -> Signal | NoTrade | None:
        feats = lane.features.update(bar, fut, vix)
        lane.last_bar, lane.last_features = bar, feats
        live = lane.live
        state = MarketState(lane.cfg.underlying, feats, has_position=live is not None,
                            trades_today=self.risk.trades_today, day_pnl=self.risk.day_pnl,
                            daily_loss_hit=self.risk.day_pnl <= -self.risk.limits.max_daily_loss)
        if live is not None and live.state == "OPEN":
            live.bars += 1
            reason = lane.strategy.exit_signal(state, live.signal.direction)
            if reason is None and lane.eng.max_hold_bars is not None and live.bars >= lane.eng.max_hold_bars:
                reason = "time exit"
            if reason:
                self._request_exit(lane, reason, now)
        if not lane.enabled:
            lane.last_decision = {"kind": "disabled", "ts": bar.ts.isoformat()}
            return None
        decision = lane.strategy.generate_signal(state)
        if isinstance(decision, NoTrade):
            lane.last_decision = {"kind": "no_trade", "reason": decision.reason, "ts": bar.ts.isoformat()}
        elif isinstance(decision, Signal):
            await self._on_signal(lane, decision, now)
        else:
            lane.last_decision = {"kind": "none", "ts": bar.ts.isoformat()}
        return decision

    # ---- signal -> risk -> order ---------------------------------------------------------------
    async def _on_signal(self, lane: Lane, sig: Signal, now: datetime) -> None:
        cid = uuid.uuid4().hex[:12]
        d = {"kind": "signal", "ts": sig.timestamp.isoformat(), "correlation_id": cid,
             "direction": sig.direction.value, "confidence": sig.confidence, "reason": sig.reason,
             "underlying": sig.underlying, "strategy": lane.label, "outcome": "pending", "detail": ""}
        lane.last_decision = d
        lane.signals.append(d)
        self.store.emit("signal.generated", cid, {
            "strategy": lane.label, "strategy_version": lane.cfg.strategy_version_id, "underlying": sig.underlying,
            "direction": sig.direction.value, "confidence": sig.confidence, "reason": sig.reason,
            "entry_ref": sig.entry_ref, "stop_loss_pct": sig.stop_loss_pct, "target_pct": sig.target_pct,
            "features": sig.features, "data_source": self.data_source, "option_pricing": self.option_pricing})

        def skip(why: str) -> None:
            d["outcome"], d["detail"] = "skipped", why
            self.store.emit("signal.skipped", cid, {"reason": why})

        if lane.live is not None:
            return skip("position already open")
        if now.astimezone(IST).time() >= lane.eng.eod_exit:
            return skip("after EOD cutoff")
        inst = self._select_option(lane, sig, now)
        if inst is None:
            return skip("no listed option contract for the selection")
        quote = self.md.latest.get(inst.token)
        if quote is None:
            return skip(f"no quote for {inst.symbol}")
        price = quote.ask if quote.ask and quote.ask > 0 else self.broker.exe.buy(quote.ltp)
        qty = lane.eng.lots * inst.lot_size
        d.update(option=inst.symbol, entry=round(price, 2),
                 stop=round(price * (1 - sig.stop_loss_pct / 100), 2),
                 target=round(price * (1 + sig.target_pct / 100), 2))

        intent = OrderIntent(lane.label, sig.underlying, inst.token, inst.symbol, qty, inst.lot_size, price,
                             sig.stop_loss_pct, cid)
        decision = self.risk.check(intent, self._risk_context(sig.underlying, inst.token, now))
        self.store.emit("risk.approved" if decision.approved else "risk.rejected", cid, decision.to_dict())
        if not decision.approved:
            d["outcome"], d["detail"] = "risk_rejected", "; ".join(decision.reasons)
            return

        order = self.broker.submit(OrderRequest(inst.token, inst.symbol, Side.BUY, qty, f"{cid}-entry", cid,
                                                tag="entry"))
        self.store.emit("order.placed", cid, order.to_dict())
        if order.status == OrderStatus.REJECTED:
            d["outcome"], d["detail"] = "order_rejected", order.reason or ""
            self.store.emit("order.rejected", cid, order.to_dict())
            return
        if self.data_source not in ("simulated", "replay"):  # real feed only; synthetic data has no live order to mirror
            ask = quote.ask if quote.ask and quote.ask > 0 else price
            self.store.emit("shadow.order", cid, shadow_order(
                symbol=inst.symbol, quantity=qty, ask=ask, tick_size=inst.tick_size,
                slippage_pct=self.shadow_slippage_pct, paper_price=price, correlation_id=cid))
        self.risk.on_entry(cid, sig.underlying, price * qty)
        lane.live = _Live(cid, sig, inst, qty, "ENTRY", order)
        d["outcome"] = "ordered"

    def _select_option(self, lane: Lane, sig: Signal, now: datetime) -> Instrument | None:
        reg, und = self.md.registry, sig.underlying
        today = now.astimezone(IST).date()
        exps = [e for e in reg.expiries(und, now) if e > today or (e == today and not lane.roll_on_expiry)]
        spot = self.md.spot(und)
        if not exps or spot is None:
            return None
        exp = exps[0]
        strikes = reg.strikes(und, exp)
        if not strikes:
            return None
        sel = sig.option_selection.upper()
        steps = 0
        if sel.startswith(("ITM", "OTM")):
            n = int(sel[3:] or 1)
            steps = moneyness_offset(sig.direction.option_type, n if sel.startswith("ITM") else -n)
        elif sel != "ATM":
            return None
        step = self.md._strike_step(und, exp)
        atm = min(strikes, key=lambda k: abs(k - spot))
        return reg.option(und, exp, atm + steps * step, sig.direction.option_type)

    def _risk_context(self, underlying: str, token: str, now: datetime) -> RiskContext:
        und_inst = self.md.registry.index(underlying)
        return RiskContext(
            now=now, market_open=self.md.calendar.is_market_open(now),
            instrument_valid=self.md.registry.by_token(token) is not None,
            underlying_age_s=self.md.quality.last_tick_age(und_inst.token, now) if und_inst else None,
            option_age_s=self._age(token, now), available_cash=self.broker.cash,
            unrealized_pnl=self.unrealized_pnl())

    def _age(self, token: str, now: datetime) -> float | None:
        t = self.md.latest.get(token)
        return None if t is None else (now - t.ts).total_seconds()

    # ---- fills ---------------------------------------------------------------------------------
    def _on_order_update(self, order: Order, now: datetime) -> None:
        cid = order.request.correlation_id
        lane, live = next(((ln, ln.live) for ln in self.lanes if ln.live and ln.live.cid == cid), (None, None))
        self.store.emit("order.filled" if order.status == OrderStatus.FILLED else "order.rejected", cid,
                        order.to_dict())
        if lane is None or live is None:
            return
        sig = live.signal
        if order is live.entry_order:
            if order.status != OrderStatus.FILLED:
                self.risk.on_entry_failed(live.cid)
                lane.live = None
                for s in lane.signals:
                    if s.get("correlation_id") == cid:
                        s["outcome"], s["detail"] = "order_rejected", order.reason or ""
                return
            px = order.fill_price or 0.0
            live.state, live.entry_price, live.entry_time = "OPEN", px, order.filled_at
            live.stop = live.initial_stop = px * (1 - sig.stop_loss_pct / 100)
            live.target = px * (1 + sig.target_pct / 100)
            live.high_water = live.low_water = live.last_bid = px
            self.risk.update_entry(live.cid, px * live.quantity)
            for s in lane.signals:
                if s.get("correlation_id") == cid:
                    s["outcome"] = "filled"
            return
        if order is live.exit_order:
            if order.status != OrderStatus.FILLED:  # should not happen; retry next heartbeat
                self.store.emit("alert.exit_rejected", cid, order.to_dict())
                live.exit_order = None  # stays in EXIT; _manage places a new exit order
                return
            self._close(lane, live, order, now)

    def _close(self, lane: Lane, live: _Live, order: Order, now: datetime) -> None:
        entry = live.entry_order
        qty = live.quantity
        exit_px = order.fill_price or 0.0
        gross = (exit_px - live.entry_price) * qty
        charges = entry.charges + order.charges
        net = gross - charges
        entry_t = live.entry_time or entry.submitted_at
        exit_t = order.filled_at or now
        spot = self.md.spot(lane.cfg.underlying)
        trade = {
            "correlation_id": live.cid, "trading_day": exit_t.astimezone(IST).date().isoformat(),
            "underlying": lane.cfg.underlying, "symbol": live.inst.symbol, "strategy": lane.label,
            "strategy_version": lane.cfg.strategy_version_id, "direction": live.signal.direction.value,
            "quantity": qty, "entry_time": entry_t, "entry_price": round(live.entry_price, 2),
            "exit_time": exit_t, "exit_price": round(exit_px, 2), "exit_reason": live.exit_reason or "exit",
            "gross_pnl": round(gross, 2), "charges": round(charges, 2), "net_pnl": round(net, 2),
            "mae_pct": round((min(live.low_water, exit_px) / live.entry_price - 1) * 100, 2),
            "mfe_pct": round((max(live.high_water, exit_px) / live.entry_price - 1) * 100, 2),
            "minutes_held": round((exit_t - entry_t).total_seconds() / 60, 2),
            "signal_reason": live.signal.reason, "confidence": live.signal.confidence,
            "data_source": self.data_source, "option_pricing": self.option_pricing,
            "features": live.signal.features,
        }
        self.store.add_trade(trade)
        self.store.emit("trade.closed", live.cid, {
            **{k: v for k, v in trade.items() if k != "features"},
            "entry_time": entry_t.isoformat(), "exit_time": exit_t.isoformat(), "underlying_exit": spot})
        self.risk.on_exit(live.cid, net)
        lane.live = None

    # ---- position management ------------------------------------------------------------------
    async def _manage(self, lane: Lane, now: datetime) -> None:
        live = lane.live
        if live is None:
            return
        if live.state == "EXIT" and live.exit_order is None:
            self._place_exit(lane, live, now)  # a previous attempt was rejected: retry
        if live.state != "OPEN":
            return
        q = self.md.latest.get(live.inst.token)
        if q is None:
            return
        bid = q.bid if q.bid and q.bid > 0 else q.ltp
        live.last_bid = bid
        live.low_water = min(live.low_water, bid)
        live.high_water = max(live.high_water, bid)
        reason = None
        if now.astimezone(IST).time() >= lane.eng.eod_exit:
            reason = "eod"
        elif bid <= live.stop:
            reason = "trailing stop" if live.stop > live.initial_stop + 1e-9 else "stop"
        elif bid >= live.target:
            reason = "target"
        else:
            eng = lane.eng
            if eng.trail_activate_pct is not None and eng.trail_pct is not None \
                    and live.high_water >= live.entry_price * (1 + eng.trail_activate_pct / 100):
                live.stop = max(live.stop, live.high_water * (1 - eng.trail_pct / 100))
        if reason:
            self._request_exit(lane, reason, now)

    def _request_exit(self, lane: Lane, reason: str, now: datetime) -> None:
        live = lane.live
        if live is None or live.state != "OPEN":
            return
        live.state, live.exit_reason = "EXIT", reason
        self.store.emit("exit.triggered", live.cid, {"reason": reason, "bid": live.last_bid,
                                                      "stop": round(live.stop, 2), "target": round(live.target, 2)})
        self._place_exit(lane, live, now)

    def _place_exit(self, lane: Lane, live: _Live, now: datetime) -> None:
        live.exit_attempts += 1
        order = self.broker.submit(OrderRequest(live.inst.token, live.inst.symbol, Side.SELL, live.quantity,
                                                f"{live.cid}-exit-{live.exit_attempts}", live.cid,
                                                tag=f"exit:{live.exit_reason}"))
        live.exit_order = order
        self.store.emit("order.placed", live.cid, order.to_dict())
        if order.status == OrderStatus.REJECTED:
            self.store.emit("alert.exit_rejected", live.cid, order.to_dict())
            live.exit_order = None  # retried by _manage on the next heartbeat

    # ---- kill switch / daily loss ---------------------------------------------------------------
    async def kill(self, reason: str = "manual STOP", *, flatten: bool = True) -> None:
        """Emergency STOP: no new entries. With `flatten`, open positions are also closed at market."""
        now = self.now()
        self.risk.trip_kill_switch(reason, now)
        self.store.emit("kill_switch.tripped", None, {"reason": reason, "flatten": flatten})
        for lane in self.lanes:
            live = lane.live
            if live is not None and live.state == "ENTRY" and await self._withdraw_entry(lane, live, now):
                continue  # not filled yet: withdrawn
            if flatten:
                self._request_exit(lane, f"kill switch: {reason}", now)
        await self.tick(now)
        await self.store.flush()

    async def _withdraw_entry(self, lane: Lane, live: _Live, now: datetime) -> bool:
        """Cancel an entry that has not filled. False if it filled in the meantime (it is then a position)."""
        order = await self.broker.cancel_order(live.entry_order.id)
        if order.status == OrderStatus.FILLED:
            self._on_order_update(order, now)
            return False
        self.risk.on_entry_failed(live.cid)
        self.store.emit("order.cancelled", live.cid, order.to_dict())
        lane.live = None
        return True

    async def _enforce_daily_loss(self, now: datetime) -> None:
        if not any(ln.live and ln.live.state == "OPEN" for ln in self.lanes):
            return
        if self.risk.day_pnl + self.unrealized_pnl() <= -self.risk.limits.max_daily_loss:
            for lane in self.lanes:
                self._request_exit(lane, "daily loss limit", now)

    def unrealized_pnl(self) -> float:
        total = 0.0
        for ln in self.lanes:
            lv = ln.live
            if lv and lv.state in ("OPEN", "EXIT") and lv.entry_price:
                total += (lv.last_bid - lv.entry_price) * lv.quantity - lv.entry_order.charges
        return total

    # ---- persistence (PRD §22.3) ---------------------------------------------------------------
    def export_lives(self) -> list[dict]:
        """Open positions (OPEN or on their way out). An entry still waiting for its fill is not one."""
        return [_live_to_dict(ln, ln.live) for ln in self.lanes if ln.live and ln.live.state in ("OPEN", "EXIT")]

    def snapshot(self) -> dict:
        unfilled = {ln.live.cid for ln in self.lanes if ln.live and ln.live.state == "ENTRY"}
        return build_snapshot(self.risk, self.broker, self.export_lives(), unfilled)

    def _save_state(self, now: datetime) -> None:
        self.store.set_state(now.astimezone(IST).date().isoformat(), self.data_source, self.snapshot())

    def adopt(self, lives: list[dict]) -> None:
        """Resume managing restored positions. All or nothing: if any cannot be matched to a lane, a contract
        in the symbol master or a position in the broker, nothing is adopted and the start is refused, since
        a position nobody manages never exits."""
        plan: list[tuple[Lane, _Live]] = []
        for d in lives:
            free = [ln for ln in self.lanes if ln.live is None and ln.cfg.underlying == d["underlying"]
                    and all(ln is not p[0] for p in plan)]
            lane = next((ln for ln in free if ln.label == d["strategy"]), free[0] if free else None)
            if lane is None:
                raise RuntimeError(f"open paper position {d['symbol']} ({d['strategy']}) has no lane to manage it: "
                                   f"start a lane for {d['underlying']}, or close the position first")
            inst = self.md.registry.by_token(d["token"])
            if inst is None:
                raise RuntimeError(f"open paper position {d['symbol']} is not in the symbol master any more")
            pos = self.broker.position(d["token"])
            if pos is None or pos.quantity < d["quantity"]:
                raise RuntimeError(f"saved position {d['symbol']} does not match the paper broker's positions")
            plan.append((lane, _live_from_dict(d, inst)))
        for lane, live in plan:
            lane.live = live
            self.store.emit("position.restored", live.cid, {
                "symbol": live.inst.symbol, "state": live.state, "quantity": live.quantity,
                "entry_price": live.entry_price, "stop": round(live.stop, 2), "target": round(live.target, 2),
                "high_water": live.high_water, "bars": live.bars, "strategy": lane.label})

    # ---- read side ---------------------------------------------------------------------------
    def positions(self) -> list[dict]:
        out = []
        now = self.now()
        for ln in self.lanes:
            lv = ln.live
            if lv is None:
                continue
            cur = lv.last_bid if lv.state != "ENTRY" else 0.0
            held = (now - lv.entry_time).total_seconds() / 60 if lv.entry_time else 0.0
            out.append({
                "correlation_id": lv.cid, "state": lv.state, "underlying": ln.cfg.underlying,
                "symbol": lv.inst.symbol, "strategy": ln.label, "direction": lv.signal.direction.value,
                "quantity": lv.quantity, "entry_price": round(lv.entry_price, 2) if lv.entry_price else None,
                "current_price": round(cur, 2) if cur else None, "stop": round(lv.stop, 2) if lv.stop else None,
                "target": round(lv.target, 2) if lv.target else None,
                "unrealized_pnl": round((cur - lv.entry_price) * lv.quantity - lv.entry_order.charges, 2)
                if lv.state != "ENTRY" and lv.entry_price else None,
                "minutes_held": round(held, 1), "exit_reason": lv.exit_reason,
                "entry_time": lv.entry_time.isoformat() if lv.entry_time else None})
        return out

    def status(self) -> dict:
        return {
            "running": self.running, "started_at": self.started_at.isoformat() if self.started_at else None,
            "now": self.now().isoformat(), "broker": self.broker.name, "data_source": self.data_source,
            "option_pricing": self.option_pricing, "errors": self.errors, "last_error": self.last_error,
            "cash": round(self.broker.cash, 2), "starting_cash": self.broker.starting_cash,
            "realized_pnl": round(self.broker.realized_pnl, 2), "unrealized_pnl": round(self.unrealized_pnl(), 2),
            "lanes": [{
                "underlying": ln.cfg.underlying, "strategy": ln.label, "timeframe": ln.cfg.timeframe,
                "enabled": ln.enabled, "warmed_bars": ln.warmed_bars,
                "last_bar": ln.last_bar.ts.isoformat() if ln.last_bar else None,
                "last_decision": ln.last_decision, "signals": list(ln.signals)[-10:][::-1],
            } for ln in self.lanes],
            "risk": self.risk.status(), "restore": self.restore_report,
        }
