"""Generate multi-day SIMULATED index / futures / VIX candles for trying out the backtester.

    python -m trader.scripts.generate_sim_history --days 120

Data goes to the `SIM:*` instruments only, and every backtest on it is labelled SIMULATED.
It is for exercising the pipeline, not for drawing conclusions about the market.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
from datetime import date, datetime, time, timedelta, timezone

from trader.config import get_settings
from trader.db import repository as repo
from trader.db.migrate import upgrade_db
from trader.db.session import create_engine, create_sessionmaker
from trader.domain import InstrumentType
from trader.instruments.calendar import IST, get_calendar
from trader.market_data.candles import Candle, CandleAggregator
from trader.market_data.simulated import SimConfig, SimulatedMarketDataProvider

STEP_S = 15
SESSION_S = 375 * 60
VOL_MULT = {"NIFTY": 1.0, "BANKNIFTY": 1.2}
START_PX = {"NIFTY": 24_000.0, "BANKNIFTY": 52_000.0}


def trading_days(n: int, end: date) -> list[date]:
    cal, out, d = get_calendar(), [], end
    while len(out) < n:
        if cal.is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def generate(days: list[date], tokens: dict[str, str], seed: int = 11,
             timeframes: tuple[str, ...] = ("1m", "5m")) -> list[Candle]:
    """tokens: {"NIFTY": idx, "NIFTY-FUT": fut, ..., "INDIAVIX": vix}."""
    rng = random.Random(seed)
    agg = CandleAggregator(timeframes)
    out: list[Candle] = []
    px = dict(START_PX)
    vix = 13.5
    steps = SESSION_S // STEP_S
    dt = STEP_S / (252 * 6.25 * 3600)
    for d in days:
        open_ts = datetime.combine(d, time(9, 15), tzinfo=IST).astimezone(timezone.utc)
        z_day = rng.gauss(0, 1)
        vix_open = vix
        vix = min(max(vix + 0.25 * (13.5 - vix) - 0.9 * z_day + 0.5 * rng.gauss(0, 1), 9.0), 35.0)
        regime = rng.choice((-1, 0, 0, 1))  # trend days vs range days
        for und in ("NIFTY", "BANKNIFTY"):
            sigma = (vix_open + vix) / 200 * VOL_MULT[und]
            s = px[und] * math.exp(rng.gauss(0, 0.004))  # overnight gap
            drift = regime * rng.uniform(0.5, 2.5)
            cum = 0
            for i in range(steps):
                drift += -6.0 * drift * dt * 252 + 4.0 * rng.gauss(0, 1) * math.sqrt(dt * 252)
                s *= math.exp((drift - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * rng.gauss(0, 1))
                ts = open_ts + timedelta(seconds=i * STEP_S)
                u_shape = 1.8 - 1.4 * math.sin(math.pi * i / steps)  # heavier at open and close
                cum += int(rng.expovariate(1 / (900 * u_shape)))
                out += agg.update(tokens[und], ts, round(s, 2), 0)
                out += agg.update(tokens[f"{und}-FUT"], ts, round(s * 1.004, 2), cum)
            px[und] = s
        for i in range(0, steps, 4):
            v = vix_open + (vix - vix_open) * i / steps + rng.gauss(0, 0.05)
            out += agg.update(tokens["INDIAVIX"], open_ts + timedelta(seconds=i * STEP_S), round(v, 4), 0)
        out += agg.flush()  # sessions end; close the last bars of the day
    return out


async def run(n_days: int, seed: int) -> int:
    settings = get_settings()
    url = settings.resolved_database_url
    await asyncio.to_thread(upgrade_db, url)
    engine = create_engine(url)
    sm = create_sessionmaker(engine)
    sim = SimulatedMarketDataProvider(SimConfig(seed=seed))
    insts = await sim.discover_instruments()
    tokens = {i.symbol: i.token for i in insts if i.instrument_type == InstrumentType.INDEX}
    tokens |= {f"{i.underlying}-FUT": i.token for i in insts if i.instrument_type == InstrumentType.FUT}
    days = trading_days(n_days, datetime.now(IST).date() - timedelta(days=1))
    candles = generate(days, tokens, seed)
    async with sm() as s, s.begin():
        ids = await repo.upsert_instruments(s, [i for i in insts if i.instrument_type != InstrumentType.OPT])
        n = await repo.upsert_candles(s, candles, ids)
        await repo.record_event(s, "sim", "sim.history_generated",
                                {"days": n_days, "from": days[0].isoformat(), "to": days[-1].isoformat(), "candles": n})
    await engine.dispose()
    print(f"Generated {n} SIMULATED candles for {len(days)} trading days ({days[0]} .. {days[-1]})")
    return n


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--seed", type=int, default=11)
    a = p.parse_args()
    asyncio.run(run(a.days, a.seed))


if __name__ == "__main__":
    main()
