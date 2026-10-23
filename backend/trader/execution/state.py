"""Paper-account snapshot and restore (PRD §22.3).

A snapshot is the part of the paper account a restart would otherwise lose: open positions with the
runner's stop/target/high-water, the risk engine's counters for the trading day, and the kill switch.
It is rewritten whenever it changes (`PaperStore.set_state`) and read once, at start-up.

Restore policy, because a wrong resume is worse than none:
* **Kill switch and disabled strategies** come back whatever day they were saved on. A safety latch must
  not clear itself by restarting; a stale one is reported so the operator knows to reset it.
* **Positions, cash, P&L and risk counters** come back only if the snapshot is from the *same IST trading
  day*. An older snapshot is reported (`stale`, with the positions it held) and not resumed: the position
  was a previous day's, its quote feed and stop context are gone, and it must be looked at by a person.
* Snapshots are stored per data source (replay, simulated, live never share one), and a mismatch is ignored.
* An entry that was ordered but not yet filled is not saved: it cannot survive a restart.
"""

from __future__ import annotations

from datetime import datetime

from trader.execution.paper import PaperBroker
from trader.risk import RiskEngine
from trader.strategy.base import Direction, Signal

VERSION = 1


def signal_to_dict(s: Signal) -> dict:
    return {"direction": s.direction.value, "confidence": s.confidence, "underlying": s.underlying,
            "option_selection": s.option_selection, "entry_ref": s.entry_ref, "stop_loss_pct": s.stop_loss_pct,
            "target_pct": s.target_pct, "reason": s.reason, "timestamp": s.timestamp.isoformat(),
            "strategy": s.strategy, "features": s.features, "risk_pct": s.risk_pct,
            "index_stop_dist": s.index_stop_dist, "index_target_dist": s.index_target_dist}


def signal_from_dict(d: dict) -> Signal:
    return Signal(Direction(d["direction"]), d["confidence"], d["underlying"], d["option_selection"], d["entry_ref"],
                  d["stop_loss_pct"], d["target_pct"], d["reason"], datetime.fromisoformat(d["timestamp"]),
                  d["strategy"], d.get("features") or {}, risk_pct=d.get("risk_pct"),
                  index_stop_dist=d.get("index_stop_dist"), index_target_dist=d.get("index_target_dist"))


def build_snapshot(risk: RiskEngine, broker: PaperBroker, lives: list[dict], unfilled: set[str]) -> dict:
    """`lives` are the OPEN/EXIT positions the runner manages; `unfilled` the correlation ids of entries
    still waiting for a fill, whose risk reservations must not be carried over."""
    return {"version": VERSION, "risk": risk.export_state(unfilled), "broker": broker.export_state(), "lives": lives}


def restore_snapshot(risk: RiskEngine, broker: PaperBroker, row: dict | None, *, today: str,
                     data_source: str) -> tuple[dict, list[dict]]:
    """Apply a stored snapshot. Returns (report, lives to hand to `PaperRunner.adopt`)."""
    if row is None:
        return {"status": "none", "detail": "no saved paper state"}, []
    state = row["state"] or {}
    saved = {"saved_trading_day": row["trading_day"], "saved_at": row["saved_at"].isoformat(),
             "saved_data_source": row["data_source"]}
    if row["data_source"] != data_source or state.get("version") != VERSION:
        return {"status": "ignored", **saved,
                "detail": f"saved state is from {row['data_source']!r} (v{state.get('version')}), "
                          f"this run is {data_source!r}: not restored"}, []
    risk.restore_latch(state["risk"])
    latch = risk.kill_switch.active
    lives = state.get("lives") or []
    if row["trading_day"] != today:
        report = {"status": "stale", **saved, "kill_switch": latch,
                  "stale_positions": [f"{d['symbol']} x{d['quantity']} ({d['state']}, strategy {d['strategy']})"
                                      for d in lives],
                  "detail": f"saved state is from {row['trading_day']}, today is {today}: positions, cash and "
                            "counters NOT restored" + ("; kill switch restored and still ON" if latch else "")}
        return report, []
    risk.restore_day(state["risk"])
    broker.restore_state(state["broker"])
    return {"status": "restored", **saved, "kill_switch": latch, "positions": len(lives),
            "detail": f"restored {len(lives)} open position(s), {risk.trades_today} trade(s) today"}, lives
