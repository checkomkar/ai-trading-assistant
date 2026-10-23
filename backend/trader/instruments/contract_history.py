"""What contracts looked like on past dates (PRD §24.2 B1.4, B1.5).

* **Lot sizes** change through NSE circulars. `config/lot_sizes.yaml` holds the dated history; the broker's
  symbol master stays the source for live contracts. Unverified rows are reported, never silently trusted.
* **Expiry overrides:** `data/fyers/expiry_map.json`, written by the expired-option downloader when a planned
  expiry turned out to be on another day of its week (an unlisted holiday, a changed weekday rule). Expiry
  resolution consults it so backtests trade the contract that really existed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

from trader.config import CONFIG_DIR


@dataclass(frozen=True)
class LotSize:
    lot: int
    verified: bool
    since: date


class LotSizeTable:
    def __init__(self, rows: dict[str, list[LotSize]]) -> None:
        self.rows = {k: sorted(v, key=lambda r: r.since) for k, v in rows.items()}

    @classmethod
    def from_yaml(cls, path: Path) -> "LotSizeTable":
        if not path.exists():
            return cls({})
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls({und: [LotSize(int(r["lot"]), bool(r.get("verified", False)),
                                  r["from"] if isinstance(r["from"], date) else date.fromisoformat(str(r["from"])))
                          for r in rows] for und, rows in raw.items()})

    def on(self, underlying: str, expiry: date) -> LotSize | None:
        """The lot size of a contract of `underlying` expiring on `expiry`, or None when the table does not reach back."""
        rows = [r for r in self.rows.get(underlying.upper(), []) if r.since <= expiry]
        return rows[-1] if rows else None


@lru_cache
def lot_size_table() -> LotSizeTable:
    return LotSizeTable.from_yaml(CONFIG_DIR / "lot_sizes.yaml")


ExpiryOverrides = dict[tuple[str, date], "date | None"]


def load_expiry_overrides(path: Path) -> ExpiryOverrides:
    """{(underlying, planned expiry): actual expiry, or None if no contract existed that week}. Missing file = {}."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    out: ExpiryOverrides = {}
    for key, v in raw.items():
        und, planned = key.split("|")
        out[(und, date.fromisoformat(planned))] = date.fromisoformat(v["expiry"]) if v else None
    return out


def default_expiry_overrides() -> ExpiryOverrides:
    from trader.config import get_settings

    return load_expiry_overrides(get_settings().data_dir / "fyers" / "expiry_map.json")
