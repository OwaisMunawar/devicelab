"""In-process fan-out of dashboard events to SSE subscribers.

Single-process on purpose (see docs/SCALING.md): with more than one API replica this is
the piece to swap for Postgres LISTEN/NOTIFY, behind the same EventPublisher port.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from devicelab.application.ports import Event


class InProcessEventBus:
    def __init__(self, queue_size: int = 1000) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._queue_size = queue_size

    def publish(self, event: Event) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A slow dashboard loses events and refetches; it must not stall the control plane.
                self._subscribers.discard(queue)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
