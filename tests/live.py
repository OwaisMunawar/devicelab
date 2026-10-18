"""A real uvicorn server inside the test's event loop, so HTTP, SSE and WebSocket traffic
take the same path they do in production."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI

from devicelab.api.app import create_app
from devicelab.config import Settings
from tests.conftest import TEST_URL

ADMIN_KEY = "dlk_test_admin_key_for_integration_tests"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Live:
    settings: Settings
    port: int
    background: bool
    app: FastAPI | None = None
    server: uvicorn.Server | None = None
    task: asyncio.Task[None] | None = None
    client: httpx.AsyncClient = field(init=False)

    def __post_init__(self) -> None:
        self.client = httpx.AsyncClient(
            base_url=self.url, headers={"Authorization": f"Bearer {ADMIN_KEY}"}, timeout=10
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v1/agent/ws"

    async def start(self) -> None:
        self.app = create_app(self.settings, run_background=self.background)
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            ws_max_size=self.settings.ws_max_message_bytes,
            lifespan="on",
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve())
        for _ in range(200):
            if self.server.started:
                return
            await asyncio.sleep(0.05)
        raise RuntimeError("server did not start")

    async def stop(self) -> None:
        if self.server and self.task:
            self.server.should_exit = True
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(self.task, timeout=15)
        self.server = self.task = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    async def aclose(self) -> None:
        await self.stop()
        await self.client.aclose()


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": TEST_URL,
        "migrate_on_start": False,
        "bootstrap_admin_key": ADMIN_KEY,
        "artifact_dir": tmp_path / "artifacts",
        "cors_origins": ["http://localhost:5173"],
        "log_json": False,
        "log_level": "WARNING",
        "scheduler_interval_s": 0.1,
        "sweep_interval_s": 0.3,
        "schedule_interval_s": 1.0,
        "alert_interval_s": 3600.0,
        "retention_interval_s": 3600.0,
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]
