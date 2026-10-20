"""Bulk expired-option download: planning, resume, quota, and that the result is usable by the backtester."""

import json
from datetime import date, datetime, time, timezone

import pytest

from tests.test_paper_runner import REPLAY_DAY, world  # noqa: F401 - `world` seeds 13-21 Oct 2026 index candles
from trader.backtest import data as bt_data
from trader.brokers.fyers import expired, expired_bulk as bulk
from trader.instruments.calendar import IST, get_calendar
from trader.instruments.strikes import OptionType

CAL = get_calendar()


def test_windows_chain_from_expiry_to_expiry_and_flag_the_monthly_contract():
    wins = bulk.expiry_windows("NIFTY", date(2025, 10, 1), date(2025, 12, 1), CAL, today=date(2026, 1, 1))
    exps = [w.expiry for w in wins]
    assert exps == sorted(exps) and all(w.start < w.expiry for w in wins)
    assert all(a.expiry == b.start for a, b in zip(wins, wins[1:]))  # each contract is live from the last one's expiry
    by_month = {}
    for w in wins:
        by_month.setdefault((w.expiry.year, w.expiry.month), []).append(w)
    for (y, m), ws in by_month.items():
        if (y, m) == (2025, 12):
            continue  # cut off by the range end: only its first expiry is in the list
        assert [w.monthly for w in ws] == [False] * (len(ws) - 1) + [True]  # the month's last expiry is the monthly


def test_banknifty_is_monthly_only_after_the_rule_change_and_weekly_before():
    after = bulk.expiry_windows("BANKNIFTY", date(2025, 10, 1), date(2026, 3, 1), CAL, today=date(2026, 6, 1))
    assert after and all(w.monthly for w in after)
    before = bulk.expiry_windows("BANKNIFTY", date(2024, 3, 1), date(2024, 4, 15), CAL, today=date(2026, 6, 1))
    assert len(before) >= 5 and not all(w.monthly for w in before)


def test_an_expiry_that_has_not_expired_yet_is_never_planned():
    wins = bulk.expiry_windows("NIFTY", date(2026, 10, 1), date(2026, 10, 31), CAL, today=date(2026, 10, 19))
    assert date(2026, 10, 19) not in [w.expiry for w in wins] and max(w.expiry for w in wins) < date(2026, 10, 19)


def test_strikes_cover_every_atm_the_index_visited_plus_padding():
    assert bulk.strikes_for("NIFTY", 25010, 25140, 1) == [24950, 25000, 25050, 25100, 25150, 25200]
    assert bulk.strikes_for("BANKNIFTY", 55040, 55040, 0) == [55000]


def test_symbol_format_follows_weekly_or_monthly():
    weekly = bulk.Item(bulk.Window("NIFTY", date(2025, 11, 11), date(2025, 11, 4), False), 25000, OptionType.CE)
    monthly = bulk.Item(bulk.Window("NIFTY", date(2025, 11, 25), date(2025, 11, 18), True), 25000, OptionType.PE)
    assert weekly.symbol == "NSE:NIFTY25N1125000CE" and monthly.symbol == "NSE:NIFTY25NOV25000PE"


class FakeRest:
    """Fyers stand-in: every contract has a few candles except those whose strike ends in 50 (never traded)."""

    def __init__(self, fail: bool = False) -> None:
        self.requests: list[dict] = []
        self.fail = fail

    def fno_historical_data(self, req: dict) -> dict:
        self.requests.append(req)
        if self.fail:
            raise ConnectionError("fyers is down")
        strike = int("".join(ch for ch in req["symbol"].split("NIFTY")[1][5:] if ch.isdigit()))
        if strike % 100 == 50:
            return {"s": "no_data", "candles": []}
        t0 = int(datetime.combine(date(2026, 10, 19), time(9, 15), tzinfo=IST).timestamp())
        rows = [[t0 + 60 * i, 100 + i, 102 + i, 99 + i, 101 + i, 500, 1000] for i in range(5)]
        return {"s": "ok", "columns": ["timestamp", "open", "high", "low", "close", "volume", "oi"], "candles": rows}


async def plan_for(world, tmp_path, **kw):
    return await bulk.build_plan(world, CAL, ["NIFTY"], date(2026, 10, 13), date(2026, 10, 21), timeframe="1m",
                                 pad=kw.get("pad", 0), today=date(2026, 10, 22),
                                 ledger=bulk.EmptyLedger(tmp_path / "empty.json"))


async def no_sleep(_):
    return None


async def test_plan_covers_each_expired_expiry_newest_first_and_skips_days_without_index_data(world, tmp_path):
    plan = await plan_for(world, tmp_path)
    exps = {it.window.expiry for it in plan.items}
    assert exps == {date(2026, 10, 13), date(2026, 10, 19)}  # 20 Oct is an exchange holiday, so that week expires on the 19th; 27 Oct has not expired yet
    assert plan.items[0].window.expiry == date(2026, 10, 19)  # newest first
    assert all(it.strike % 50 == 0 for it in plan.items)
    both = {(it.strike, it.option_type) for it in plan.items if it.window.expiry == date(2026, 10, 19)}
    assert all((k, ot) in both for k, _ in list(both) for ot in OptionType)  # calls and puts together
    # an expiry whose days have no stored index candles cannot be planned: flagged, not guessed
    far = await bulk.build_plan(world, CAL, ["NIFTY"], date(2026, 7, 1), date(2026, 7, 31), timeframe="1m", pad=0,
                                today=date(2026, 10, 22), ledger=bulk.EmptyLedger(tmp_path / "e2.json"))
    assert far.items == [] and far.skipped_windows


async def test_fetch_stores_candles_the_backtester_can_use_then_resumes_without_refetching(world, tmp_path):
    ledger = bulk.EmptyLedger(tmp_path / "empty.json")
    plan = await plan_for(world, tmp_path)
    rest = FakeRest()
    quota = expired.DailyQuota(tmp_path / "q.json", 0)  # Prime
    res = await bulk.fetch(rest, world, plan, timeframe="1m", quota=quota, ledger=ledger, say=lambda _: None,
                           sleep=no_sleep)
    assert res.errors == 0 and res.fetched > 0 and res.no_data > 0 and res.stopped is None
    assert res.fetched + res.no_data == len(plan.items) == len(rest.requests)
    first = rest.requests[0]  # newest expiry: asked only for the days it was the nearest contract
    assert (first["range_from"], first["range_to"]) == ("2026-10-13", "2026-10-19") and first["resolution"] == "1"

    async with world() as s:
        d = await bt_data.load(s, "NIFTY", "5m", date(2026, 10, 19), date(2026, 10, 19), "real", with_options=True)
    assert d.options and all(k[0] == date(2026, 10, 19) or k[0] == date(2026, 10, 13) for k in d.options)
    key = next(iter(d.options))
    assert len(d.options[key]) == 1  # five 1m candles at 09:15-09:19 build one 5m bar

    again = await plan_for(world, tmp_path)  # stored ones and known-empty ones are not planned again
    assert again.items == [] and again.stored == res.fetched and again.empty == res.no_data
    rest2 = FakeRest()
    assert (await bulk.fetch(rest2, world, again, timeframe="1m", quota=quota, ledger=ledger,
                             say=lambda _: None, sleep=no_sleep)).fetched == 0 and rest2.requests == []
    assert set(json.loads((tmp_path / "empty.json").read_text())) == {
        f"NSE:{it.symbol.removeprefix('NSE:')}|1m" for it in plan.items if it.strike % 100 == 50}


async def test_standard_quota_stops_the_run_and_the_next_day_continues(world, tmp_path):
    plan = await plan_for(world, tmp_path)
    ledger = bulk.EmptyLedger(tmp_path / "empty.json")
    day1 = datetime(2026, 10, 22, 5, tzinfo=timezone.utc)
    rest = FakeRest()
    res = await bulk.fetch(rest, world, plan, timeframe="1m", ledger=ledger, say=lambda _: None, sleep=no_sleep,
                           quota=expired.DailyQuota(tmp_path / "q.json", 3, now_fn=lambda: day1))
    assert len(rest.requests) == 3 and "daily quota of 3" in res.stopped
    plan2 = await plan_for(world, tmp_path)
    assert len(plan2.items) + plan2.stored + plan2.empty == len(plan.items) + 0 and len(plan2.items) < len(plan.items)
    rest2 = FakeRest()
    await bulk.fetch(rest2, world, plan2, timeframe="1m", ledger=ledger, say=lambda _: None, sleep=no_sleep,
                     quota=expired.DailyQuota(tmp_path / "q.json", 3, now_fn=lambda: day1.replace(day=23)))
    assert len(rest2.requests) == 3
    assert not {r["symbol"] for r in rest.requests} & {r["symbol"] for r in rest2.requests}


async def test_repeated_errors_stop_the_run_before_burning_more_requests(world, tmp_path):
    plan = await plan_for(world, tmp_path)
    rest = FakeRest(fail=True)
    res = await bulk.fetch(rest, world, plan, timeframe="1m", quota=expired.DailyQuota(tmp_path / "q.json", 0),
                           ledger=bulk.EmptyLedger(tmp_path / "empty.json"), say=lambda _: None, sleep=no_sleep)
    assert len(rest.requests) == 5 and res.errors == 5 and "in a row" in res.stopped and res.fetched == 0
    assert not (tmp_path / "empty.json").exists()  # a failure is not "no data"


async def test_max_requests_is_a_budget(world, tmp_path):
    plan = await plan_for(world, tmp_path)
    rest = FakeRest()
    res = await bulk.fetch(rest, world, plan, timeframe="1m", quota=expired.DailyQuota(tmp_path / "q.json", 0),
                           ledger=bulk.EmptyLedger(tmp_path / "empty.json"), max_requests=4, say=lambda _: None,
                           sleep=no_sleep)
    assert len(rest.requests) == 4 and "max-requests" in res.stopped


def test_summary_states_how_many_days_the_standard_plan_needs():
    plan = bulk.Plan([bulk.Item(bulk.Window("NIFTY", date(2026, 10, 13), date(2026, 10, 6), False), 25000, OptionType.CE)] * 25,
                     stored=3, empty=2, skipped_windows=[])
    text = " ".join(bulk.summarise(plan, 10))
    assert "25 contracts" in text and "3 day(s)" in text and "3 already stored" in text
