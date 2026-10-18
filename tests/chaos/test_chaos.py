from __future__ import annotations

import functools
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.chaos.harness import (
    RunningAgent,
    assert_converged,
    devices,
    hosts_of_running,
    job,
    job_state,
    spawn_agent,
    submit,
    wait_until,
)
from tests.live import Live

pytestmark = pytest.mark.chaos


async def succeeded(live: Live, job_id: str) -> bool:
    return await job_state(live, job_id) == "succeeded"


async def test_end_to_end_with_artifacts(cluster: Live, db: AsyncEngine, tmp_path: Path) -> None:
    agent = await spawn_agent(cluster, tmp_path, "berlin", 3)
    ids = [await submit(cluster, priority=i) for i in range(6)]
    for job_id in ids:
        await wait_until(functools.partial(succeeded, cluster, job_id), what="job success")

    detail = await job(cluster, ids[0])
    assert detail["attempts"] == 1 and detail["executions"][0]["state"] == "succeeded"
    execution = (await cluster.client.get(f"/v1/executions/{detail['executions'][0]['id']}")).json()
    assert [s["state"] for s in execution["steps"]] == ["succeeded"] * 4
    shot = next(a for a in execution["artifacts"] if a["name"] == "home.png")
    content = await cluster.client.get(f"/v1/artifacts/{shot['id']}/content")
    assert content.headers["content-type"] == "image/png" and content.content.startswith(b"\x89PNG")
    logs = (await cluster.client.get(f"/v1/executions/{execution['id']}/logs")).json()
    assert logs, "step logs were persisted"
    metrics = (await cluster.client.get("/metrics")).text
    assert "devicelab_step_duration_seconds_count" in metrics
    await agent.stop()
    await assert_converged(db)


async def test_agent_killed_mid_step_is_requeued_exactly_once(
    cluster: Live, db: AsyncEngine, tmp_path: Path
) -> None:
    first = await spawn_agent(cluster, tmp_path, "berlin", 1, latency=(0.5, 0.6))
    second = await spawn_agent(cluster, tmp_path, "austin", 1, latency=(0.5, 0.6))
    job_id = await submit(cluster)

    async def running() -> bool:
        return await job_state(cluster, job_id) == "running"

    await wait_until(running, what="job running")
    (host_id,) = await hosts_of_running(cluster, job_id)
    hosts = {h["id"]: h["name"] for h in (await cluster.client.get("/v1/hosts")).json()}
    victim, survivor = (
        (first, second) if hosts[str(host_id)] == first.host_name else (second, first)
    )

    await victim.kill()
    await wait_until(lambda: succeeded(cluster, job_id), timeout=40, what="requeue and finish")

    detail = await job(cluster, job_id)
    states = sorted(e["state"] for e in detail["executions"])
    assert states == ["lost", "succeeded"], states
    assert detail["attempts"] == 2
    async with db.connect() as conn:
        reclaimed = (
            await conn.execute(text("SELECT count(*) FROM leases WHERE state='expired'"))
        ).scalar()
        requeues = (
            await conn.execute(text("SELECT count(*) FROM audit_log WHERE action='job.requeued'"))
        ).scalar()
    assert reclaimed == 1 and requeues == 1
    by_name = {d["name"]: d["state"] for d in await devices(cluster)}
    assert "offline" in by_name.values()
    await survivor.stop()
    await assert_converged(db)


async def test_dropped_connection_within_the_ttl_loses_nothing(
    cluster: Live, db: AsyncEngine, tmp_path: Path
) -> None:
    agent = await spawn_agent(cluster, tmp_path, "berlin", 1, latency=(0.6, 0.7))
    job_id = await submit(cluster)

    async def running() -> bool:
        return await job_state(cluster, job_id) == "running"

    await wait_until(running, what="job running")
    await agent.runtime.drop_connection()
    await wait_until(
        lambda: succeeded(cluster, job_id), timeout=30, what="completion after reconnect"
    )

    detail = await job(cluster, job_id)
    assert detail["attempts"] == 1 and [e["state"] for e in detail["executions"]] == ["succeeded"]
    assert agent.runtime.connection_count >= 2
    await agent.stop()
    await assert_converged(db)


async def test_partition_longer_than_the_ttl_fences_the_stale_agent(
    cluster: Live, db: AsyncEngine, tmp_path: Path
) -> None:
    agents = [
        await spawn_agent(cluster, tmp_path, site, 1, latency=(0.5, 0.6), reconnect_base_s=6.0)
        for site in ("berlin", "austin")
    ]
    job_id = await submit(cluster)

    async def running() -> bool:
        return await job_state(cluster, job_id) == "running"

    await wait_until(running, what="job running")
    (host_id,) = await hosts_of_running(cluster, job_id)
    names = {h["id"]: h["name"] for h in (await cluster.client.get("/v1/hosts")).json()}
    cut = next(a for a in agents if a.host_name == names[str(host_id)])
    other = next(a for a in agents if a is not cut)

    await cut.runtime.drop_connection()  # gone ~6s, twice the 3s lease
    await wait_until(lambda: succeeded(cluster, job_id), timeout=40, what="retry on the other host")

    detail = await job(cluster, job_id)
    assert sorted(e["state"] for e in detail["executions"]) == ["lost", "succeeded"]
    # The cut-off agent comes back, is told its work is gone, and cannot resurrect the job.
    await wait_until(lambda: _idle(cut), timeout=15, what="stale agent to stand down")
    assert await job_state(cluster, job_id) == "succeeded"
    await cut.stop()
    await other.stop()
    await assert_converged(db)


async def _idle(agent: RunningAgent) -> bool:
    return agent.runtime.connection_count >= 2 and agent.runtime.active_execution_ids == []


async def test_api_restart_during_a_run_converges(
    cluster: Live, db: AsyncEngine, tmp_path: Path
) -> None:
    agent = await spawn_agent(cluster, tmp_path, "berlin", 2, latency=(0.5, 0.6))
    ids = [await submit(cluster) for _ in range(2)]

    async def both_running() -> bool:
        states = [await job_state(cluster, i) for i in ids]
        return all(x == "running" for x in states)

    await wait_until(both_running, what="jobs running")
    await cluster.restart()
    await cluster.client.aclose()
    cluster.__post_init__()  # fresh client for the new server instance

    for job_id in ids:
        await wait_until(
            functools.partial(succeeded, cluster, job_id),
            timeout=40,
            what="finish after restart",
        )
    for job_id in ids:
        detail = await job(cluster, job_id)
        assert detail["attempts"] == 1, detail
    assert agent.runtime.connection_count >= 2
    await agent.stop()
    await assert_converged(db)


async def test_corrupt_device_health_quarantines_and_work_moves(
    cluster: Live, db: AsyncEngine, tmp_path: Path
) -> None:
    agent = await spawn_agent(cluster, tmp_path, "berlin", 2, latency=(0.4, 0.5))
    job_id = await submit(cluster)

    async def running() -> bool:
        return await job_state(cluster, job_id) == "running"

    await wait_until(running, what="job running")
    detail = await job(cluster, job_id)
    device_id = detail["executions"][0]["device_id"]
    udid = next(d["udid"] for d in await devices(cluster) if d["id"] == device_id)
    agent.provider.devices[udid].force_battery = 400  # a reporter gone haywire

    async def quarantined() -> bool:
        return any(
            d["id"] == device_id and d["state"] == "quarantined" for d in await devices(cluster)
        )

    await wait_until(quarantined, timeout=15, what="quarantine")
    await wait_until(
        lambda: succeeded(cluster, job_id), timeout=40, what="work moved to the healthy device"
    )
    final = await job(cluster, job_id)
    assert final["attempts"] == 2
    assert {e["device_id"] for e in final["executions"] if e["state"] == "succeeded"} != {device_id}

    # Operator fixes the device and returns it to service.
    agent.provider.devices[udid].force_battery = None
    back = await cluster.client.post(f"/v1/devices/{device_id}/return-to-service")
    assert back.status_code == 200, back.text
    await agent.stop()
    await assert_converged(db)
