"""Helpers for tests that run the real control plane against real agent processes
(in-process, over real WebSockets) and then break things."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from devicelab.adapters.providers.fake import FakeBehaviour, FakeFleetProvider
from devicelab.agent.config import AgentConfig
from devicelab.agent.connection import AgentRuntime
from tests.live import Live

CHAOS_TEMPLATE = """
name: chaos-flow
steps:
  - {id: open, type: launch_app, bundle_id: com.apple.Preferences}
  - {id: settle, type: appium_actions, actions: [{action: wait, seconds: 0.3}]}
  - {id: shot, type: screenshot, artifact_name: home}
  - {id: close, type: terminate_app, bundle_id: com.apple.Preferences, always: true}
"""


AGENTS: list[RunningAgent] = []


@dataclass
class RunningAgent:
    runtime: AgentRuntime
    provider: FakeFleetProvider
    task: asyncio.Task[None]
    host_name: str

    async def stop(self) -> None:
        await self.runtime.stop()
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task

    async def kill(self) -> None:
        """Vanish without saying goodbye."""
        self.runtime.kill()
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self.task


async def spawn_agent(
    live: Live,
    tmp_path: Path,
    site: str,
    devices: int,
    *,
    latency: tuple[float, float] = (0.15, 0.25),
    reconnect_base_s: float = 0.2,
    behaviour: FakeBehaviour | None = None,
) -> RunningAgent:
    host = f"host-{site}"
    token = (
        await live.client.post(
            "/v1/enrolment-tokens", json={"host_name": host, "site": site, "max_concurrency": 8}
        )
    ).json()["token"]
    provider = FakeFleetProvider(
        site, devices, seed=11, behaviour=behaviour or FakeBehaviour(latency_s=latency)
    )
    config = AgentConfig(
        server_url=live.ws_url,
        host_name=host,
        state_dir=tmp_path / "agent-state",
        enrol_token=token,
        reconnect_base_s=reconnect_base_s,
        reconnect_max_s=reconnect_base_s,
        inventory_interval_s=30,
    )
    runtime = AgentRuntime(config, provider, provider.ui)
    task = asyncio.create_task(runtime.run(), name=f"agent-{site}")
    await asyncio.wait_for(runtime.connected.wait(), 10)
    agent = RunningAgent(runtime, provider, task, host)
    AGENTS.append(agent)
    return agent


async def wait_until(
    predicate: Callable[[], Awaitable[bool]],
    *,
    timeout: float = 30.0,
    interval: float = 0.1,
    what: str = "condition",
) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with contextlib.suppress(Exception):
            if await predicate():
                return
        await asyncio.sleep(interval)
    raise AssertionError(f"timed out waiting for {what}")


async def submit(live: Live, **body: Any) -> str:
    response = await live.client.post("/v1/jobs", json={"template": "chaos-flow", **body})
    assert response.status_code in (200, 201), response.text
    return str(response.json()["id"])


async def job(live: Live, job_id: str) -> dict[str, Any]:
    data: dict[str, Any] = (await live.client.get(f"/v1/jobs/{job_id}")).json()
    return data


async def job_state(live: Live, job_id: str) -> str:
    return str((await job(live, job_id))["state"])


async def devices(live: Live) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = (
        await live.client.get("/v1/devices", params={"limit": 200})
    ).json()["items"]
    return items


async def hosts_of_running(live: Live, job_id: str) -> list[UUID]:
    detail = await job(live, job_id)
    return [
        UUID(e["host_id"]) for e in detail["executions"] if e["state"] in ("pending", "running")
    ]


async def assert_converged(engine: AsyncEngine) -> None:
    """The invariants that must hold once a scenario has settled."""
    checks = {
        "a device has two active leases": "SELECT device_id FROM leases WHERE state='active' GROUP BY 1 HAVING count(*) > 1",
        "an active lease has no live execution": (
            "SELECT l.id FROM leases l LEFT JOIN executions e ON e.lease_id = l.id "
            "WHERE l.state='active' AND (e.id IS NULL OR e.state IN ('succeeded','failed','cancelled','lost'))"
        ),
        "a device is leased/busy without a lease": (
            "SELECT d.id FROM devices d LEFT JOIN leases l ON l.device_id = d.id AND l.state='active' "
            "WHERE d.state IN ('leased','busy') AND l.id IS NULL"
        ),
        "a job is in flight without a live execution": (
            "SELECT j.id FROM jobs j WHERE j.state IN ('leased','running') AND NOT EXISTS ("
            "SELECT 1 FROM executions e WHERE e.job_id = j.id AND e.state IN ('pending','running'))"
        ),
        "a job succeeded more than once": (
            "SELECT job_id FROM executions WHERE state='succeeded' GROUP BY 1 HAVING count(*) > 1"
        ),
        "a job has more executions per attempt than devices requested": (
            "SELECT job_id, attempt FROM executions GROUP BY 1, 2 HAVING count(*) > 1"
        ),
    }
    async with engine.connect() as conn:
        for message, sql in checks.items():
            rows = (await conn.execute(text(sql))).all()
            assert not rows, f"{message}: {rows}"
