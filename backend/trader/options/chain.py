"""Option-chain assembly and analytics (PRD §6.5, §11.3)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Callable

from trader.domain import Instrument, Tick
from trader.instruments.strikes import OptionType
from trader.options import black_scholes as bs


@dataclass
class OptionLeg:
    token: str
    symbol: str
    ltp: float | None = None
    bid: float | None = None
    ask: float | None = None
    volume: int | None = None
    oi: int | None = None
    oi_change: int | None = None
    iv: float | None = None  # annualised, decimal (0.14 = 14%)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None


@dataclass
class ChainRow:
    strike: float
    ce: OptionLeg | None = None
    pe: OptionLeg | None = None


@dataclass
class ChainSummary:
    total_call_oi: int = 0
    total_put_oi: int = 0
    total_call_oi_change: int = 0
    total_put_oi_change: int = 0
    pcr: float | None = None
    max_call_oi_strike: float | None = None
    max_put_oi_strike: float | None = None
    max_call_oi_change_strike: float | None = None
    max_put_oi_change_strike: float | None = None
    atm_iv: float | None = None


@dataclass
class OptionChain:
    underlying: str
    expiry: date
    spot: float | None
    atm_strike: float | None
    timestamp: datetime
    rows: list[ChainRow] = field(default_factory=list)
    summary: ChainSummary = field(default_factory=ChainSummary)

    def to_dict(self) -> dict:
        return asdict(self)


def _leg(inst: Instrument, tick: Tick | None, spot: float | None, t: float, r: float) -> OptionLeg:
    leg = OptionLeg(token=inst.token, symbol=inst.symbol)
    if tick is None:
        return leg
    leg.ltp, leg.bid, leg.ask = tick.ltp, tick.bid, tick.ask
    leg.volume, leg.oi, leg.oi_change = tick.volume, tick.oi, tick.oi_change
    if spot and inst.strike and inst.option_type and t > 0:
        # Prefer mid when a sane two-sided quote exists; LTP can be stale on illiquid strikes.
        px = tick.ltp
        if tick.bid and tick.ask and tick.ask >= tick.bid > 0:
            px = 0.5 * (tick.bid + tick.ask)
        iv = bs.implied_vol(px, spot, inst.strike, t, r, inst.option_type)
        if iv is not None:
            g = bs.greeks(spot, inst.strike, t, r, iv, inst.option_type)
            leg.iv, leg.delta, leg.gamma, leg.theta, leg.vega = iv, g.delta, g.gamma, g.theta, g.vega
    return leg


def build_chain(
    underlying: str,
    expiry: date,
    options: list[Instrument],
    tick_for: Callable[[str], Tick | None],
    spot: float | None,
    now: datetime,
    risk_free_rate: float,
    strike_window: int | None = None,
) -> OptionChain:
    """Assemble a chain. `strike_window` limits rows to ATM +/- N strikes."""
    t = bs.time_to_expiry(expiry, now)
    by_strike: dict[float, ChainRow] = {}
    for inst in options:
        assert inst.strike is not None
        row = by_strike.setdefault(inst.strike, ChainRow(strike=inst.strike))
        leg = _leg(inst, tick_for(inst.token), spot, t, risk_free_rate)
        if inst.option_type == OptionType.CE:
            row.ce = leg
        else:
            row.pe = leg

    rows = sorted(by_strike.values(), key=lambda r: r.strike)
    atm = min((r.strike for r in rows), key=lambda k: abs(k - spot)) if rows and spot else None

    if strike_window is not None and atm is not None:
        idx = next(i for i, r in enumerate(rows) if r.strike == atm)
        rows = rows[max(0, idx - strike_window): idx + strike_window + 1]

    chain = OptionChain(underlying=underlying, expiry=expiry, spot=spot, atm_strike=atm, timestamp=now, rows=rows)
    chain.summary = summarize(rows, atm)
    return chain


def summarize(rows: list[ChainRow], atm: float | None) -> ChainSummary:
    s = ChainSummary()

    def argmax(get: Callable[[ChainRow], int | None]) -> float | None:
        best = max(rows, key=lambda r: get(r) or 0, default=None)
        return best.strike if best is not None and (get(best) or 0) > 0 else None

    for r in rows:
        if r.ce:
            s.total_call_oi += r.ce.oi or 0
            s.total_call_oi_change += r.ce.oi_change or 0
        if r.pe:
            s.total_put_oi += r.pe.oi or 0
            s.total_put_oi_change += r.pe.oi_change or 0
    s.pcr = round(s.total_put_oi / s.total_call_oi, 3) if s.total_call_oi else None
    s.max_call_oi_strike = argmax(lambda r: r.ce.oi if r.ce else None)
    s.max_put_oi_strike = argmax(lambda r: r.pe.oi if r.pe else None)
    s.max_call_oi_change_strike = argmax(lambda r: r.ce.oi_change if r.ce else None)
    s.max_put_oi_change_strike = argmax(lambda r: r.pe.oi_change if r.pe else None)

    atm_row = next((r for r in rows if r.strike == atm), None)
    if atm_row:
        ivs = [leg.iv for leg in (atm_row.ce, atm_row.pe) if leg and leg.iv is not None]
        s.atm_iv = sum(ivs) / len(ivs) if ivs else None
    return s
