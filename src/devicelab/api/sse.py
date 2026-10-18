"""Server-sent events over fetch-friendly streaming responses (auth stays in a header)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from typing import Any

from devicelab.adapters.events import InProcessEventBus

KEEPALIVE_S = 15.0


def _frame(topic: str, data: dict[str, Any]) -> str:
    return f"event: {topic}\ndata: {json.dumps(data, default=str)}\n\n"


async def event_stream(
    bus: InProcessEventBus,
    *,
    topics: set[str] | None = None,
    match: Callable[[dict[str, Any]], bool] | None = None,
    backlog: Iterable[tuple[str, dict[str, Any]]] = (),
    is_closed: Callable[[], Awaitable[bool]],
    keepalive_s: float = KEEPALIVE_S,
) -> AsyncIterator[str]:
    async with bus.subscribe() as queue:
        yield ": connected\n\n"
        for topic, data in backlog:
            yield _frame(topic, data)
        while not await is_closed():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=keepalive_s)
            except TimeoutError:
                yield ": keepalive\n\n"
                continue
            if topics is not None and event.topic not in topics:
                continue
            if match is not None and not match(event.data):
                continue
            yield _frame(event.topic, event.data)
