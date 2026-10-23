"""Calibrate synthetic option prices against stored real ones (PRD §24.9 B8).

Synthetic pricing uses Black-Scholes with IV = INDIA VIX x a fixed multiplier. Real option prices carry a smile
(out-of-the-money options are dearer), a term structure (expiry-day options behave differently) and an intraday
pattern. With years of real option candles stored, we back out each candle's implied volatility, divide it by VIX,
and store the median ratio per bucket:

    moneyness (how far out of the money, % of spot) x days to expiry x time of day

A bucket with too few samples falls back to a coarser one (moneyness x days to expiry, then days to expiry, then all).
The result is a small JSON file in the data folder (it is derived from your broker's licensed data, so it is not
committed). `SyntheticPricer` uses it when present, and every report names the calibration and the period it was
fitted on. `validate` measures the error on days the fit did not use.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median

from trader.instruments.calendar import IST
from trader.instruments.strikes import OptionType
from trader.options import black_scholes as bs

MIN_SAMPLES = 30
MONEYNESS_EDGES = (-1.0, -0.25, 0.25, 1.0, 2.5)  # OTM % of spot (negative = in the money)
DTE_EDGES = (1, 2, 4, 8)  # calendar days: 0 = expiry day, 1, 2-3, 4-7, 8+
TOD_EDGES = (time(10, 0), time(12, 0), time(14, 0))


def moneyness_bucket(otm_pct: float) -> str:
    labels = ("ITM >1%", "ITM 0.25-1%", "ATM", "OTM 0.25-1%", "OTM 1-2.5%", "OTM >2.5%")
    for edge, lbl in zip(MONEYNESS_EDGES, labels):
        if otm_pct < edge:
            return lbl
    return labels[-1]


def dte_bucket(days: int) -> str:
    labels = ("expiry day", "1 day", "2-3 days", "4-7 days", "8+ days")
    for edge, lbl in zip(DTE_EDGES, labels):
        if days < edge:
            return lbl
    return labels[-1]


def tod_bucket(t: time) -> str:
    labels = ("09:15-10:00", "10:00-12:00", "12:00-14:00", "14:00-close")
    for edge, lbl in zip(TOD_EDGES, labels):
        if t < edge:
            return lbl
    return labels[-1]


def otm_pct(spot: float, strike: float, option_type: OptionType) -> float:
    """How far out of the money, as % of spot (negative = in the money)."""
    return (strike - spot) / spot * 100 if option_type == OptionType.CE else (spot - strike) / spot * 100


@dataclass
class Sample:
    ratio: float  # implied vol / (VIX / 100)
    m: str
    d: str
    t: str


@dataclass
class Calibration:
    underlying: str
    fitted_from: str
    fitted_to: str
    created_at: str
    samples: int
    global_ratio: float
    by_mdt: dict[str, tuple[float, int]] = field(default_factory=dict)
    by_md: dict[str, tuple[float, int]] = field(default_factory=dict)
    by_d: dict[str, tuple[float, int]] = field(default_factory=dict)

    @property
    def version(self) -> str:
        raw = json.dumps([self.underlying, self.fitted_from, self.fitted_to, self.samples, round(self.global_ratio, 6)])
        return f"{self.underlying}-{self.fitted_from}-{self.fitted_to}-{hashlib.sha256(raw.encode()).hexdigest()[:8]}"

    def ratio(self, spot: float, strike: float, option_type: OptionType, expiry: date, at: datetime) -> float:
        local = at.astimezone(IST)
        m = moneyness_bucket(otm_pct(spot, strike, option_type))
        d = dte_bucket((expiry - local.date()).days)
        t = tod_bucket(local.time())
        for key, table in ((f"{m}|{d}|{t}", self.by_mdt), (f"{m}|{d}", self.by_md), (d, self.by_d)):
            hit = table.get(key)
            if hit and hit[1] >= MIN_SAMPLES:
                return hit[0]
        return self.global_ratio

    def to_dict(self) -> dict:
        return {"version": self.version, "underlying": self.underlying, "fitted_from": self.fitted_from,
                "fitted_to": self.fitted_to, "created_at": self.created_at, "samples": self.samples,
                "global_ratio": self.global_ratio,
                "by_mdt": {k: list(v) for k, v in self.by_mdt.items()}, "by_md": {k: list(v) for k, v in self.by_md.items()},
                "by_d": {k: list(v) for k, v in self.by_d.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "Calibration":
        return cls(d["underlying"], d["fitted_from"], d["fitted_to"], d["created_at"], int(d["samples"]),
                   float(d["global_ratio"]), {k: (v[0], v[1]) for k, v in d["by_mdt"].items()},
                   {k: (v[0], v[1]) for k, v in d["by_md"].items()}, {k: (v[0], v[1]) for k, v in d["by_d"].items()})


def sample(price: float, spot: float, strike: float, option_type: OptionType, expiry: date, at: datetime,
           vix: float | None, r: float = 0.065) -> Sample | None:
    """One observation: the candle's implied vol over VIX, with its buckets. None when no meaningful IV exists."""
    if vix is None or vix <= 0 or price <= 0.05:
        return None
    t = bs.time_to_expiry(expiry, at)
    if t <= 0:
        return None
    iv = bs.implied_vol(price, spot, strike, t, r, option_type)
    if iv is None or not (0.02 < iv < 3.0):
        return None
    local = at.astimezone(IST)
    return Sample(iv / (vix / 100), moneyness_bucket(otm_pct(spot, strike, option_type)),
                  dte_bucket((expiry - local.date()).days), tod_bucket(local.time()))


def fit(samples: list[Sample], underlying: str, fitted_from: date, fitted_to: date) -> Calibration:
    if not samples:
        raise ValueError("no usable option candles to fit (download expired options first)")

    def med(groups: dict[str, list[float]]) -> dict[str, tuple[float, int]]:
        return {k: (round(median(v), 5), len(v)) for k, v in groups.items()}

    mdt: dict[str, list[float]] = {}
    md: dict[str, list[float]] = {}
    d: dict[str, list[float]] = {}
    for s in samples:
        mdt.setdefault(f"{s.m}|{s.d}|{s.t}", []).append(s.ratio)
        md.setdefault(f"{s.m}|{s.d}", []).append(s.ratio)
        d.setdefault(s.d, []).append(s.ratio)
    return Calibration(underlying, fitted_from.isoformat(), fitted_to.isoformat(),
                       datetime.now(timezone.utc).isoformat(timespec="seconds"), len(samples),
                       round(median(x.ratio for x in samples), 5), med(mdt), med(md), med(d))


def path_for(data_dir: Path, underlying: str) -> Path:
    return data_dir / "calibration" / f"{underlying.upper()}.json"


def save(cal: Calibration, data_dir: Path) -> Path:
    p = path_for(data_dir, cal.underlying)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cal.to_dict(), indent=1), encoding="utf-8")
    return p


def load(data_dir: Path, underlying: str) -> Calibration | None:
    try:
        return Calibration.from_dict(json.loads(path_for(data_dir, underlying).read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


def load_default(underlying: str) -> Calibration | None:
    from trader.config import get_settings

    return load(get_settings().data_dir, underlying)


# ---- reading stored data ------------------------------------------------------------------------------------------
async def collect(sm, underlying: str, start: date, end: date, every_minutes: int = 15, max_otm_pct: float = 3.0,  # noqa: ANN001
                  r: float = 0.065, progress=None) -> list[tuple[Sample, dict]]:  # noqa: ANN001
    """(sample, raw observation) for stored 1m option candles of `underlying` in [start, end], one every `every_minutes`,
    within `max_otm_pct` of the money. Reads one expiry at a time so years of data never sit in memory at once."""
    from sqlalchemy import select

    from trader.backtest.data import _bounds, _naive
    from trader.db.models import CandleRow, InstrumentRow
    from trader.instruments.specs import VIX_SYMBOL

    out: list[tuple[Sample, dict]] = []
    async with sm() as s:
        naive = _naive(s)
        opts = (await s.execute(select(InstrumentRow).where(
            InstrumentRow.underlying == underlying, InstrumentRow.instrument_type == "OPT",
            InstrumentRow.token.not_like("SIM:%"), InstrumentRow.expiry >= start,
            InstrumentRow.expiry <= end + timedelta(days=40)))).scalars().all()
        idx = (await s.execute(select(InstrumentRow.id, InstrumentRow.symbol).where(
            InstrumentRow.instrument_type == "INDEX", InstrumentRow.symbol.in_((underlying, VIX_SYMBOL)),
            InstrumentRow.token.not_like("SIM:%")))).all()
        ids = {sym: i for i, sym in idx}
        if underlying not in ids or VIX_SYMBOL not in ids:
            raise ValueError("index or VIX 1m candles are missing: run the index step of fetch_prime_history first")
        by_exp: dict[date, list] = {}
        for o in opts:
            by_exp.setdefault(o.expiry, []).append(o)
        exps = sorted(by_exp)
        for n, exp in enumerate(exps):
            insts = {o.id: o for o in by_exp[exp]}
            lo = max(start, exp - timedelta(days=14))
            hi = min(end, exp)
            if lo > hi:
                continue
            a, b = _bounds(lo, hi)
            if naive:
                a, b = a.replace(tzinfo=None), b.replace(tzinfo=None)

            async def series(iid: int) -> dict[datetime, float]:
                rows = (await s.execute(select(CandleRow.ts, CandleRow.close).where(
                    CandleRow.instrument_id == iid, CandleRow.timeframe == "1m", CandleRow.ts >= a, CandleRow.ts <= b))).all()
                return {(ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)): c for ts, c in rows}

            spot, vix = await series(ids[underlying]), await series(ids[VIX_SYMBOL])
            rows = (await s.execute(select(CandleRow.instrument_id, CandleRow.ts, CandleRow.close).where(
                CandleRow.instrument_id.in_(list(insts)), CandleRow.timeframe == "1m",
                CandleRow.ts >= a, CandleRow.ts <= b))).all()
            for iid, ts, close in rows:
                ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
                if ts.astimezone(IST).minute % every_minutes:
                    continue
                sp, vx = spot.get(ts), vix.get(ts)
                o = insts[iid]
                if sp is None or vx is None:
                    continue
                ot = OptionType(o.option_type)
                if abs(otm_pct(sp, float(o.strike), ot)) > max_otm_pct:
                    continue
                at = ts + timedelta(minutes=1)  # the candle's close
                smp = sample(close, sp, float(o.strike), ot, exp, at, vx, r)
                if smp is not None:
                    out.append((smp, {"price": close, "spot": sp, "strike": float(o.strike), "type": ot, "expiry": exp,
                                      "at": at, "vix": vx}))
            if progress:
                progress(n + 1, len(exps))
    return out


def validate(cal: Calibration | None, observations: list[dict], mult: float, r: float = 0.065) -> dict:
    """Pricing error on observations the fit did not use: calibrated model vs the plain VIX x multiplier model."""

    def errors(use_cal: bool) -> list[float]:
        errs = []
        for x in observations:
            ratio = cal.ratio(x["spot"], x["strike"], x["type"], x["expiry"], x["at"]) if use_cal and cal else mult
            iv = x["vix"] / 100 * ratio
            t = max(bs.time_to_expiry(x["expiry"], x["at"]), bs.MIN_T)
            model = bs.price(x["spot"], x["strike"], t, r, iv, x["type"])
            errs.append((model - x["price"]) / x["price"] * 100)
        return errs

    def summary(e: list[float]) -> dict:
        if not e:
            return {"n": 0}
        ab = sorted(abs(v) for v in e)
        return {"n": len(e), "median_abs_error_pct": round(ab[len(ab) // 2], 2),
                "p90_abs_error_pct": round(ab[int(0.9 * (len(ab) - 1))], 2), "bias_pct": round(median(e), 2)}

    return {"calibrated": summary(errors(True)), "plain_vix": summary(errors(False))}


async def spread_estimate(sm, underlying: str) -> dict | None:  # noqa: ANN001
    """Median bid-ask spread (% of mid) of near-the-money options the live collector recorded, if any (B8.4)."""
    from sqlalchemy import select

    from trader.db.models import OptionSnapshotRow

    async with sm() as s:
        rows = (await s.execute(select(OptionSnapshotRow.bid, OptionSnapshotRow.ask, OptionSnapshotRow.spot,
                                       OptionSnapshotRow.strike).where(OptionSnapshotRow.underlying == underlying)
                                .limit(500_000))).all()
    spreads = [(a - b) / ((a + b) / 2) * 100 for b, a, sp, k in rows
               if b and a and a > b > 0 and sp and abs(k - sp) / sp < 0.01]
    if len(spreads) < 100:
        return None
    spreads.sort()
    return {"samples": len(spreads), "median_spread_pct": round(spreads[len(spreads) // 2], 3),
            "p90_spread_pct": round(spreads[int(0.9 * (len(spreads) - 1))], 3)}


def summary_line(cal: Calibration) -> str:
    return (f"Synthetic prices calibrated to real {cal.underlying} options ({cal.fitted_from} to {cal.fitted_to}, "
            f"{cal.samples:,} samples, version {cal.version}).")

