"""In-process async pub/sub (PRD §14.9). Same shape a Redis-backed bus will implement later."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import contextmanager
from typing import Any, Iterator


class EventBus:
    def __init__(self, maxsize: int = 1000) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._maxsize = maxsize

    def publish(self, topic: str, message: Any) -> None:
        for q in list(self._subs.get(topic, ())):
            if q.full():
                # Slow consumer: drop the oldest message rather than block the feed.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)

    @contextmanager
    def subscribe(self, topic: str) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(self._maxsize)
        self._subs[topic].add(q)
        try:
            yield q
        finally:
            self._subs[topic].discard(q)

    def subscriber_count(self, topic: str) -> int:
        return len(self._subs.get(topic, ()))
