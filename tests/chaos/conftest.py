from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.chaos.harness import AGENTS, CHAOS_TEMPLATE
from tests.live import Live, free_port, make_settings


@pytest_asyncio.fixture
async def cluster(db: AsyncEngine, tmp_path: Path) -> AsyncIterator[Live]:
    """A control plane with the scheduler, sweeper and reclaimer running, tuned so a lease
    lasts seconds rather than a minute. The behaviour under test is the same; the clock is faster."""
    settings = make_settings(
        tmp_path,
        lease_ttl_s=3.0,
        heartbeat_interval_s=0.5,
        offline_after_s=2.0,
        scheduler_interval_s=0.05,
        sweep_interval_s=0.2,
        quarantine_threshold=3,
    )
    live = Live(settings, free_port(), background=True)
    await live.start()
    response = await live.client.post("/v1/templates", json={"content": CHAOS_TEMPLATE})
    assert response.status_code == 201, response.text
    yield live
    for agent in AGENTS:
        await agent.kill()
    AGENTS.clear()
    await live.aclose()


def pytest_exception_interact(node, call, report):  # type: ignore[no-untyped-def]  # TEMP
    print(report.longreprtext, flush=True)
