"""Tick data-quality checks (PRD §1.8, §14.7).

Rejects invalid / duplicate / out-of-order ticks and tracks stale feeds and gaps.
Counters are exposed at `/health/data`; the risk engine will later refuse to trade on
stale underlyings.
"""

from __future__ import annotations

import math
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from trader.domain import Tick


class Issue(str, Enum):
    INVALID_PRICE = "invalid_price"
    CROSSED_QUOTE = "crossed_quote"
    PRICE_JUMP = "price_jump"
    DUPLICATE = "duplicate"
    OUT_OF_ORDER = "out_of_order"
    FUTURE_TIMESTAMP = "future_timestamp"
    GAP = "gap"
    STALE = "stale"


REJECTING = {Issue.INVALID_PRICE, Issue.PRICE_JUMP, Issue.DUPLICATE, Issue.OUT_OF_ORDER, Issue.FUTURE_TIMESTAMP}


@dataclass
class QualityConfig:
    max_jump_pct_index: float = 0.05  # 5% tick-to-tick move on an index is a bad print
    max_jump_pct_derivative: float = 0.60
    max_future_skew: timedelta = timedelta(seconds=5)
    gap_after: timedelta = timedelta(seconds=30)
    stale_after: timedelta = timedelta(seconds=10)


@dataclass
class Verdict:
    accept: bool
    issues: list[Issue] = field(default_factory=list)


@dataclass
class _Last:
    ts: datetime
    ltp: float
    volume: int | None


class DataQualityMonitor:
    def __init__(self, config: QualityConfig | None = None, index_tokens: set[str] | None = None,
                 watched_tokens: set[str] | None = None) -> None:
        self.cfg = config or QualityConfig()
        self.index_tokens = index_tokens or set()
        # Tokens expected to tick continuously (underlyings); only these are checked for staleness.
        self.watched_tokens = watched_tokens or set()
        self._last: dict[str, _Last] = {}
        self.counts: Counter[str] = Counter()
        self.by_token: dict[str, Counter[str]] = {}
        self.recent: deque[dict] = deque(maxlen=200)
        self.accepted = 0
        self.rejected = 0
        self.stale_tokens: set[str] = set()

    def check(self, tick: Tick, now: datetime) -> Verdict:
        issues: list[Issue] = []
        last = self._last.get(tick.token)

        if tick.ltp is None or not math.isfinite(tick.ltp) or tick.ltp <= 0:
            issues.append(Issue.INVALID_PRICE)
        if tick.bid and tick.ask and tick.bid > tick.ask:
            issues.append(Issue.CROSSED_QUOTE)
        if tick.ts - now > self.cfg.max_future_skew:
            issues.append(Issue.FUTURE_TIMESTAMP)

        if last is not None:
            if tick.ts < last.ts:
                issues.append(Issue.OUT_OF_ORDER)
            elif tick.ts == last.ts and tick.ltp == last.ltp and tick.volume == last.volume:
                issues.append(Issue.DUPLICATE)
            elif tick.token in self.watched_tokens and tick.ts - last.ts > self.cfg.gap_after:
                # Illiquid options legitimately go quiet; only continuous feeds can have gaps.
                issues.append(Issue.GAP)
            if Issue.INVALID_PRICE not in issues and last.ltp > 0:
                limit = (self.cfg.max_jump_pct_index if tick.token in self.index_tokens
                         else self.cfg.max_jump_pct_derivative)
                if abs(tick.ltp / last.ltp - 1) > limit:
                    issues.append(Issue.PRICE_JUMP)

        accept = not any(i in REJECTING for i in issues)
        if accept:
            self._last[tick.token] = _Last(tick.ts, tick.ltp, tick.volume)
            self.stale_tokens.discard(tick.token)
            self.accepted += 1
        else:
            self.rejected += 1
        for issue in issues:
            self._record(tick.token, issue, now, ltp=tick.ltp)
        return Verdict(accept, issues)

    def sweep_stale(self, now: datetime) -> set[str]:
        """Mark watched tokens with no tick for `stale_after` as stale. Returns newly-stale tokens."""
        newly: set[str] = set()
        for token in self.watched_tokens:
            last = self._last.get(token)
            if last is None or token in self.stale_tokens:
                continue
            if now - last.ts > self.cfg.stale_after:
                self.stale_tokens.add(token)
                newly.add(token)
                self._record(token, Issue.STALE, now, last_tick=last.ts.isoformat())
        return newly

    def last_tick_age(self, token: str, now: datetime) -> float | None:
        last = self._last.get(token)
        return (now - last.ts).total_seconds() if last else None

    def _record(self, token: str, issue: Issue, now: datetime, **detail: object) -> None:
        self.counts[issue.value] += 1
        self.by_token.setdefault(token, Counter())[issue.value] += 1
        self.recent.append({"ts": now.isoformat(), "token": token, "issue": issue.value, **detail})

    def snapshot(self) -> dict:
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "issues": dict(self.counts),
            "stale_tokens": sorted(self.stale_tokens),
            "recent": list(self.recent)[-20:],
        }
