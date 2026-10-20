"""In-memory index of discovered instruments + option resolver (PRD §3.4)."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Iterable, Literal

from trader.domain import Instrument, InstrumentType
from trader.instruments.calendar import IST, MARKET_CLOSE
from trader.instruments.strikes import OptionType, atm_strike

ExpirySelector = Literal["nearest", "next", "monthly"] | date


class InstrumentRegistry:
    def __init__(self, instruments: Iterable[Instrument] = ()) -> None:
        self._by_token: dict[str, Instrument] = {}
        self._by_symbol: dict[str, Instrument] = {}
        self._options: dict[tuple[str, date, float, OptionType], Instrument] = {}
        self._expiries: dict[str, set[date]] = defaultdict(set)
        self._strikes: dict[tuple[str, date], set[float]] = defaultdict(set)
        self.add_many(instruments)

    def add_many(self, instruments: Iterable[Instrument]) -> None:
        for inst in instruments:
            self._by_token[inst.token] = inst
            self._by_symbol[inst.symbol] = inst
            if inst.is_option:
                assert inst.expiry and inst.strike is not None and inst.option_type
                self._options[(inst.underlying, inst.expiry, inst.strike, inst.option_type)] = inst
                self._expiries[inst.underlying].add(inst.expiry)
                self._strikes[(inst.underlying, inst.expiry)].add(inst.strike)

    def __len__(self) -> int:
        return len(self._by_token)

    def all(self) -> list[Instrument]:
        return list(self._by_token.values())

    def by_token(self, token: str) -> Instrument | None:
        return self._by_token.get(token)

    def by_symbol(self, symbol: str) -> Instrument | None:
        return self._by_symbol.get(symbol)

    def index(self, underlying: str) -> Instrument | None:
        return next(
            (i for i in self._by_token.values()
             if i.instrument_type == InstrumentType.INDEX and i.underlying == underlying),
            None,
        )

    def future(self, underlying: str) -> Instrument | None:
        futs = [i for i in self._by_token.values()
                if i.instrument_type == InstrumentType.FUT and i.underlying == underlying]
        return min(futs, key=lambda i: i.expiry or date.max) if futs else None

    def expiries(self, underlying: str, now: datetime | None = None) -> list[date]:
        exps = sorted(self._expiries.get(underlying, ()))
        if now is None:
            return exps
        local = now.astimezone(IST)
        return [d for d in exps if d > local.date() or (d == local.date() and local.time() < MARKET_CLOSE)]

    def strikes(self, underlying: str, expiry: date) -> list[float]:
        return sorted(self._strikes.get((underlying, expiry), ()))

    def options_for_expiry(self, underlying: str, expiry: date) -> list[Instrument]:
        return [i for (u, e, _, _), i in self._options.items() if u == underlying and e == expiry]

    def resolve_expiry(self, underlying: str, selector: ExpirySelector, now: datetime) -> date | None:
        if isinstance(selector, date):
            return selector if selector in self._expiries.get(underlying, ()) else None
        live = self.expiries(underlying, now)
        if not live:
            return None
        if selector == "nearest":
            return live[0]
        if selector == "next":
            return live[1] if len(live) > 1 else None
        if selector == "monthly":
            # Monthly = last listed expiry of the nearest live month.
            month = (live[0].year, live[0].month)
            same_month = [d for d in live if (d.year, d.month) == month]
            return same_month[-1]
        raise ValueError(f"Unknown expiry selector: {selector}")

    def option(self, underlying: str, expiry: date, strike: float, option_type: OptionType) -> Instrument | None:
        return self._options.get((underlying, expiry, float(strike), option_type))

    def resolve_option(
        self,
        underlying: str,
        option_type: OptionType,
        now: datetime,
        *,
        spot: float | None = None,
        strike: float | None = None,
        offset: int = 0,
        expiry: ExpirySelector = "nearest",
    ) -> Instrument | None:
        """Resolve e.g. ("NIFTY", CE, strike=25000, expiry="nearest") or (spot=25012, offset=+1)."""
        exp = self.resolve_expiry(underlying, expiry, now)
        if exp is None:
            return None
        strikes = self.strikes(underlying, exp)
        if not strikes:
            return None
        if strike is None:
            if spot is None:
                raise ValueError("Either strike or spot is required")
            interval = int(strikes[1] - strikes[0]) if len(strikes) > 1 else None
            strike = atm_strike(underlying, spot, interval) + offset * (interval or 0)
        return self.option(underlying, exp, strike, option_type)
