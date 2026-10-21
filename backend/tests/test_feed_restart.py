"""The collector can be restarted in-process (new credentials after the daily broker sign-in) and keeps producing data."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from trader.api.main import create_app
from trader.config import Settings


def test_collector_restart_keeps_the_feed_running(tmp_path):
    st = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{(tmp_path / 'r.db').as_posix()}", market_data_provider="simulated",
                  sim_ignore_session=True, sim_tick_interval_seconds=0.05, paper_autostart=False)
    with TestClient(create_app(st, start_collector=True)) as c:
        md = c.app.state.md

        def ticks() -> int:
            return md.provider.health.ticks_received

        deadline = time.time() + 10
        while ticks() == 0 and time.time() < deadline:
            time.sleep(0.1)
        assert ticks() > 0
        c.portal.call(md.restart)
        before = ticks()
        deadline = time.time() + 10
        while ticks() <= before and time.time() < deadline:
            time.sleep(0.1)
        assert ticks() > before  # ticks keep arriving after the restart
        assert md.provider.health.connected and len(md._tasks) == 3
