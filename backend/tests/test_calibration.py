"""B8 (PRD §24.9): synthetic option prices calibrated against stored real ones."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest

from trader.backtest import calibration as C
from trader.backtest.pricing import Contract, SyntheticPricer
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.domain import Instrument, InstrumentType
from trader.instruments.calendar import IST, TradingCalendar
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle
from trader.options import black_scholes as bs

EXPIRY = date(2026, 10, 27)
DAYS = [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
SPOT, VIX = 25000.0, 14.0
STRIKES = [24800.0 + 50 * i for i in range(9)]


def smile(otm: float) -> float:
    """The 'true' implied vol / VIX ratio of this synthetic market: dearer away from the money."""
    return 1.1 if otm < -0.25 else 1.0 if otm < 0.25 else 1.2 if otm < 1.0 else 1.4


def true_price(k: float, ot: OptionType, at: datetime) -> float:
    iv = VIX / 100 * smile(C.otm_pct(SPOT, k, ot))
    return bs.price(SPOT, k, max(bs.time_to_expiry(EXPIRY, at), bs.MIN_T), 0.065, iv, ot)


@pytest.fixture
async def sm(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'c.db').as_posix()}"
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    idx = Instrument("NSE:NIFTY50-INDEX", "NIFTY", "NIFTY", "NSE", InstrumentType.INDEX)
    vix = Instrument("NSE:INDIAVIX-INDEX", "INDIAVIX", "INDIAVIX", "NSE", InstrumentType.INDEX)
    opts = [Instrument(f"NSE:NIFTY26O27{int(k)}{ot.value}", f"NIFTY26O27{int(k)}{ot.value}", "NIFTY", "NFO",
                       InstrumentType.OPT, 65, 0.05, EXPIRY, k, ot) for k in STRIKES for ot in OptionType]
    candles = []
    for d in DAYS:
        t0 = datetime.combine(d, time(9, 15), tzinfo=IST).astimezone(timezone.utc)
        for i in range(0, 375):
            ts = t0 + timedelta(minutes=i)
            candles += [Candle(idx.token, "1m", ts, SPOT, SPOT, SPOT, SPOT, 0, True),
                        Candle(vix.token, "1m", ts, VIX, VIX, VIX, VIX, 0, True)]
            if i % 15 == 0:
                for o in opts:
                    p = true_price(o.strike, o.option_type, ts + timedelta(minutes=1))
                    candles.append(Candle(o.token, "1m", ts, p, p, p, p, 1000, True))
    async with sm() as s, s.begin():
        ids = await repo.upsert_instruments(s, [idx, vix, *opts])
        await repo.upsert_candles(s, candles, ids)
    yield sm
    await engine.dispose()


def test_buckets():
    assert C.moneyness_bucket(0.0) == "ATM" and C.moneyness_bucket(0.5) == "OTM 0.25-1%"
    assert C.moneyness_bucket(-2.0) == "ITM >1%" and C.moneyness_bucket(3.0) == "OTM >2.5%"
    assert [C.dte_bucket(n) for n in (0, 1, 3, 5, 9)] == ["expiry day", "1 day", "2-3 days", "4-7 days", "8+ days"]
    assert C.otm_pct(25000, 25250, OptionType.CE) == pytest.approx(1.0)
    assert C.otm_pct(25000, 25250, OptionType.PE) == pytest.approx(-1.0)


async def test_the_fit_recovers_the_markets_smile_and_beats_the_plain_model(sm, tmp_path):
    obs = await C.collect(sm, "NIFTY", DAYS[0], DAYS[-1])
    assert len(obs) > 500
    fit_on = [s for s, o in obs if o["at"].date() < DAYS[-1]]
    held = [o for s, o in obs if o["at"].date() == DAYS[-1]]
    cal = C.fit(fit_on, "NIFTY", DAYS[0], DAYS[1])
    for m, want in (("ATM", 1.0), ("OTM 0.25-1%", 1.2), ("ITM 0.25-1%", 1.1)):
        got = cal.by_md[f"{m}|4-7 days"][0]
        assert got == pytest.approx(want, abs=0.01), m
    v = C.validate(cal, held, 1.0)
    assert v["calibrated"]["median_abs_error_pct"] < 1.0 < v["plain_vix"]["median_abs_error_pct"]
    # saved and loaded back unchanged
    C.save(cal, tmp_path)
    back = C.load(tmp_path, "NIFTY")
    assert back.version == cal.version and back.by_md == cal.by_md


async def test_the_calibrated_pricer_reproduces_the_market(sm):
    obs = await C.collect(sm, "NIFTY", DAYS[0], DAYS[-1])
    cal = C.fit([s for s, _ in obs], "NIFTY", DAYS[0], DAYS[-1])
    plain, fitted = SyntheticPricer(TradingCalendar()), SyntheticPricer(TradingCalendar(), calibration=cal)
    ts = datetime.combine(DAYS[1], time(11, 0), tzinfo=IST).astimezone(timezone.utc)
    bar = Candle("NIFTY", "1m", ts, SPOT, SPOT, SPOT, SPOT, 0, True)
    c = Contract("NIFTY", EXPIRY, 25150.0, OptionType.CE, 65)  # 0.6% out of the money: true ratio 1.2
    truth = true_price(25150.0, OptionType.CE, ts)
    assert fitted.bar(c, bar, timedelta(minutes=1), VIX).open == pytest.approx(truth, rel=0.02)
    assert plain.bar(c, bar, timedelta(minutes=1), VIX).open < truth * 0.9  # the plain model underprices the wing


def test_sparse_buckets_fall_back_to_coarser_ones():
    s = [C.Sample(1.3, "ATM", "1 day", "09:15-10:00")] * 5 + [C.Sample(1.0, "ATM", "1 day", "10:00-12:00")] * 40
    cal = C.fit(s, "NIFTY", date(2026, 1, 1), date(2026, 2, 1))
    at = datetime.combine(date(2026, 10, 26), time(9, 30), tzinfo=IST)
    # the 09:15-10:00 bucket has only 5 samples: the moneyness x days-to-expiry bucket (all 45) answers instead
    assert cal.ratio(SPOT, SPOT, OptionType.CE, date(2026, 10, 27), at) == cal.by_md["ATM|1 day"][0]
