from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio

from tests.live import Live, free_port, make_settings


@pytest_asyncio.fixture
async def live(db, tmp_path: Path) -> AsyncIterator[Live]:  # type: ignore[no-untyped-def]
    server = Live(make_settings(tmp_path), free_port(), background=False)
    await server.start()
    yield server
    await server.aclose()
