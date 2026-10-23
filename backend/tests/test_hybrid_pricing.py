"""Hybrid option pricing: stored candles where they exist, the model where they do not, and nothing hidden."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.test_backtest import CAL, FREE, NO_FRICTION, TF, Scripted, bars_for
from trader.backtest.engine import BacktestEngine, EngineConfig
from trader.backtest.pricing import HybridPricer, RealPricer, SyntheticPricer, expiry_for
from trader.instruments.strikes import OptionType
from trader.market_data.candles import Candle
from trader.strategy.base import Direction

D = date(2026, 10, 21)  # a Wednesday: the Tuesday weekly expiry is on the 27th
EXPIRY = expiry_for("NIFTY", D, CAL)


def real_candles(bars, strike, option_type=OptionType.CE, premium=120.0, skip=()):
    """Stored option candles for one contract: a flat premium, optionally with some bars missing (no trade printed)."""
    out = {}
    for i, b in enumerate(bars):
        if i in skip:
            continue
        out[b.ts] = Candle("OPT", "5m", b.ts, premium, premium + 1, premium - 1, premium, 100, True)
    return {(EXPIRY, strike, option_type): out}


def pricer(candles):
    return HybridPricer(RealPricer(candles, {}, CAL), SyntheticPricer(CAL))


def run(bars, signal_bar, candles, **kw):
    eng = BacktestEngine("NIFTY", Scripted({bars[signal_bar].ts: Direction.LONG}), pricer(candles), FREE, 5,
                         EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9, **kw), NO_FRICTION)
    return eng.run(bars)


CLOSES = [25000, 25010, 25020, 25030, 25040, 25050, 25060, 25070]


def test_a_contract_with_stored_candles_is_priced_from_them():
    bars = bars_for(D, CLOSES)
    res = run(bars, 0, real_candles(bars, 25000.0))
    t = res.trades[0]
    assert t.pricing == "real" and t.entry_price == pytest.approx(120.0)  # the stored print, not a Black-Scholes number
    assert res.pricing_mode == "hybrid" and any("1 trade(s) used stored real option candles" in w for w in res.warnings)


def test_a_contract_with_no_stored_data_falls_back_to_the_model_instead_of_being_skipped():
    bars = bars_for(D, CLOSES)
    res = run(bars, 0, {})  # nothing stored at all
    assert len(res.trades) == 1 and res.trades[0].pricing == "modelled"
    assert "no option data for contract" not in res.skipped_entries and res.signals == 1
    assert any("Black-Scholes" in w and "MIX" in w for w in res.warnings)


def test_a_stored_contract_with_no_print_at_the_entry_bar_also_falls_back():
    bars = bars_for(D, CLOSES)
    res = run(bars, 0, real_candles(bars, 25000.0, skip={1}))  # the entry bar (index 1) has no print
    assert res.trades[0].pricing == "modelled" and "no option print at entry bar" not in res.skipped_entries


def test_one_source_per_trade_even_if_the_stored_candles_have_gaps_later():
    bars = bars_for(D, CLOSES)
    res = run(bars, 0, real_candles(bars, 25000.0, skip={3, 4}), max_hold_bars=None)
    t = res.trades[0]
    assert t.pricing == "real"  # entered on a real print: managed on real data (no print = held until the end of day)


def test_both_kinds_in_one_run_are_counted_in_the_report():
    bars = bars_for(D, CLOSES) + bars_for(D + timedelta(days=1), CLOSES)
    sig = {bars[0].ts: Direction.LONG, bars[len(CLOSES)].ts: Direction.LONG}
    day1_only = real_candles(bars[: len(CLOSES)], 25000.0)  # stored candles exist for the first day only
    eng = BacktestEngine("NIFTY", Scripted(sig), pricer(day1_only), FREE, 5,
                         EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9), NO_FRICTION)
    res = eng.run(bars)
    assert sorted(t.pricing for t in res.trades) == ["modelled", "real"]
    assert any("1 trade(s) used stored real" in w and "1 used the Black-Scholes" in w for w in res.warnings)


def test_the_real_only_mode_still_skips_and_counts_what_it_cannot_price():
    bars = bars_for(D, CLOSES)
    eng = BacktestEngine("NIFTY", Scripted({bars[0].ts: Direction.LONG}), RealPricer({}, {}, CAL), FREE, 5,
                         EngineConfig(trail_activate_pct=None, trail_pct=None, max_daily_loss=1e9), NO_FRICTION)
    res = eng.run(bars)
    assert res.trades == [] and res.skipped_entries == {"no option data for contract": 1}


# ---- through the API -------------------------------------------------------------------------------------------------
def test_the_api_accepts_hybrid_and_reports_it():
    import asyncio

    from fastapi.testclient import TestClient

    from tests.test_backtest_api import END, _seed
    from trader.api.main import create_app
    from trader.config import Settings
    from trader.db.migrate import upgrade_db
    import tempfile
    import pathlib

    with tempfile.TemporaryDirectory() as d:
        url = f"sqlite+aiosqlite:///{(pathlib.Path(d) / 'h.db').as_posix()}"
        upgrade_db(url)
        asyncio.run(_seed(url))
        st = Settings(_env_file=None, database_url=url, sim_seed=2, sim_tick_interval_seconds=0.2, sim_backfill_minutes=0)
        with TestClient(create_app(st)) as c:
            body = {"start": (END - timedelta(days=30)).isoformat(), "end": END.isoformat(), "pricing": "hybrid",
                    "strategy_params": {"adx_min": 10}}
            r = c.post("/backtests", json=body)
            assert r.status_code == 200, r.text
            res = r.json()
            assert res["pricing_mode"] == "hybrid" and res["metrics"]["trades"] > 0
            assert all(t["pricing"] == "modelled" for t in res["trades"])  # simulated history has no stored option candles
            assert any("MIX" in w for w in res["warnings"])
