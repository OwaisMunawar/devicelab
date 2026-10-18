"""Scale and correctness run: a simulated fleet, hundreds of jobs, real Postgres.

Starts the real API (scheduler, sweeper and reclaimer on), connects one agent per site
over WebSocket, submits a mixed workload through the REST API, and then audits the
database. The run fails if any device was ever leased twice at once.

    uv run python -m scripts.loadtest --devices 50 --sites 5 --jobs 520
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import random
import shutil
import statistics
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tests.live import ADMIN_KEY, Live, free_port, make_settings

from devicelab.adapters.db.migrate import upgrade
from devicelab.adapters.providers.fake import FakeBehaviour, FakeFleetProvider
from devicelab.agent.config import AgentConfig
from devicelab.agent.connection import AgentRuntime
from devicelab.application.scheduling import SchedulerService
from devicelab.domain.stats import percentile

SITES = ("berlin", "austin", "tokyo", "lisbon", "toronto")
TEMPLATE = """
name: load-flow
steps:
  - {id: open, type: launch_app, bundle_id: com.apple.Preferences}
  - {id: shot, type: screenshot, artifact_name: home}
  - {id: close, type: terminate_app, bundle_id: com.apple.Preferences, always: true}
"""


@dataclass
class Report:
    devices: int
    sites: int
    jobs_submitted: int
    jobs_succeeded: int = 0
    jobs_failed: int = 0
    unsatisfiable_left_queued: int = 0
    wall_s: float = 0.0
    leases_granted: int = 0
    scheduling_throughput_per_s: float = 0.0
    completed_jobs_per_s: float = 0.0
    queue_wait_s: dict[str, float] = field(default_factory=dict)
    queue_wait_high_priority_p50_s: float = 0.0
    queue_wait_low_priority_p50_s: float = 0.0
    tick_ms: dict[str, float] = field(default_factory=dict)
    double_leases: int = -1
    requeues: int = 0
    db_connections: dict[str, float] = field(default_factory=dict)
    db_cpu_percent: dict[str, float] = field(default_factory=dict)
    api_submit_ms: dict[str, float] = field(default_factory=dict)


def job_mix(rng: random.Random, count: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for _ in range(count):
        roll = rng.random()
        body: dict[str, Any] = {"template": "load-flow", "priority": rng.randint(0, 9)}
        if roll < 0.15:
            body["requirement"] = {"site": rng.choice(SITES)}
        elif roll < 0.25:
            body["requirement"] = {"min_ios": "18.0"}
        elif roll < 0.33:
            body["requirement"] = {"model_family": "iPad"}
        elif roll < 0.39:
            body["requirement"] = {"labels": ["wifi-only"]}
        elif roll < 0.43:
            body["requirement"] = {"count": 2}
        jobs.append(body)
    return jobs


async def sample_db(
    engine: Any, container: str | None, stop: asyncio.Event
) -> tuple[list[int], list[float]]:
    connections: list[int] = []
    cpu: list[float] = []
    docker = shutil.which("docker") if container else None
    while not stop.is_set():
        async with engine.connect() as conn:
            connections.append(
                int(
                    (
                        await conn.execute(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                            )
                        )
                    ).scalar_one()
                )
            )
        if docker and container:
            out = await asyncio.to_thread(
                subprocess.run,
                [docker, "stats", "--no-stream", "--format", "{{.CPUPerc}}", container],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            with contextlib.suppress(ValueError):
                cpu.append(float(out.stdout.strip().rstrip("%")))
        else:
            await asyncio.sleep(0.5)
    return connections, cpu


def dist(values: list[float], *points: float) -> dict[str, float]:
    return {f"p{int(p)}": round(percentile(values, p), 3) for p in points} | {
        "max": round(max(values), 3) if values else 0.0
    }


async def run(
    database_url: str,
    admin_url: str,
    *,
    devices: int,
    sites: int,
    jobs: int,
    seed: int,
    container: str | None,
    step_latency: tuple[float, float],
    step_failure_rate: float,
    timeout_s: float,
) -> Report:
    name = database_url.rsplit("/", 1)[-1]
    admin = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    await asyncio.to_thread(upgrade, database_url)

    rng = random.Random(seed)
    workdir = Path(tempfile.mkdtemp(prefix="devicelab-load-"))
    settings = make_settings(
        workdir,
        database_url=database_url,
        scheduler_interval_s=0.05,
        sweep_interval_s=1.0,
        lease_ttl_s=20.0,
        heartbeat_interval_s=2.0,
        offline_after_s=15.0,
        db_pool_size=20,
    )
    live = Live(settings, free_port(), background=True)
    tick_durations: list[float] = []
    original_tick = SchedulerService.tick

    async def timed_tick(self: SchedulerService) -> Any:
        started = time.perf_counter()
        result = await original_tick(self)
        if result.jobs_assigned:
            tick_durations.append((time.perf_counter() - started) * 1000)
        return result

    SchedulerService.tick = timed_tick  # type: ignore[method-assign]
    engine = create_async_engine(database_url, pool_size=4)
    stop_sampling = asyncio.Event()
    agents: list[AgentRuntime] = []
    tasks: list[asyncio.Task[None]] = []
    report = Report(devices=devices, sites=sites, jobs_submitted=jobs)
    try:
        await live.start()
        template = await live.client.post("/v1/templates", json={"content": TEMPLATE})
        template.raise_for_status()

        per_site = [devices // sites + (1 if i < devices % sites else 0) for i in range(sites)]
        for site, count in zip(SITES[:sites], per_site, strict=False):
            token = (
                await live.client.post(
                    "/v1/enrolment-tokens",
                    json={"host_name": f"load-{site}", "site": site, "max_concurrency": count},
                )
            ).json()["token"]
            provider = FakeFleetProvider(
                site,
                count,
                seed=seed,
                behaviour=FakeBehaviour(
                    latency_s=step_latency, step_failure_rate=step_failure_rate
                ),
            )
            runtime = AgentRuntime(
                AgentConfig(
                    server_url=live.ws_url,
                    host_name=f"load-{site}",
                    state_dir=workdir / "state",
                    enrol_token=token,
                    inventory_interval_s=60,
                ),
                provider,
                provider.ui,
                rng=random.Random(seed),
            )
            agents.append(runtime)
            tasks.append(asyncio.create_task(runtime.run()))
        await asyncio.gather(*(a.connected.wait() for a in agents))

        sampler = asyncio.create_task(sample_db(engine, container, stop_sampling))
        bodies = job_mix(rng, jobs)
        unsatisfiable = 5
        bodies += [
            {"template": "load-flow", "requirement": {"min_ios": "99"}}
            for _ in range(unsatisfiable)
        ]
        rng.shuffle(bodies)
        report.jobs_submitted = len(bodies)

        submit_ms: list[float] = []
        semaphore = asyncio.Semaphore(25)
        headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
        async with httpx.AsyncClient(base_url=live.url, headers=headers, timeout=30) as client:

            async def submit(body: dict[str, Any]) -> None:
                async with semaphore:
                    started = time.perf_counter()
                    response = await client.post("/v1/jobs", json=body)
                    response.raise_for_status()
                    submit_ms.append((time.perf_counter() - started) * 1000)

            started_at = time.perf_counter()
            await asyncio.gather(*(submit(b) for b in bodies))

            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                counts = (await client.get("/v1/fleet/summary")).json()["jobs"]
                done = counts.get("succeeded", 0) + counts.get("failed", 0)
                if done >= len(bodies) - unsatisfiable:
                    break
                await asyncio.sleep(0.5)
            report.wall_s = round(time.perf_counter() - started_at, 2)
            metrics_text = (await client.get("/metrics")).text
        report.api_submit_ms = dist(submit_ms, 50, 95, 99)
        stop_sampling.set()
        connections, cpu = await sampler
        if connections:
            report.db_connections = {
                "mean": round(statistics.mean(connections), 1),
                "max": float(max(connections)),
            }
        if cpu:
            report.db_cpu_percent = {
                "mean": round(statistics.mean(cpu), 1),
                "max": round(max(cpu), 1),
            }

        async with engine.connect() as conn:
            states: dict[str, int] = dict(
                (await conn.execute(text("SELECT state, count(*) FROM jobs GROUP BY 1"))).all()
            )
            report.jobs_succeeded = int(states.get("succeeded", 0))
            report.jobs_failed = int(states.get("failed", 0))
            report.unsatisfiable_left_queued = int(states.get("queued", 0))
            report.double_leases = int(
                (
                    await conn.execute(
                        text(
                            "SELECT count(*) FROM leases a JOIN leases b "
                            "ON a.device_id = b.device_id AND a.id < b.id "
                            "AND a.acquired_at < COALESCE(b.released_at, now()) "
                            "AND b.acquired_at < COALESCE(a.released_at, now())"
                        )
                    )
                ).scalar_one()
            )
            span = (
                await conn.execute(
                    text(
                        "SELECT count(*), extract(epoch FROM max(acquired_at) - min(acquired_at)) FROM leases"
                    )
                )
            ).one()
            report.leases_granted = int(span[0])
            if span[1]:
                report.scheduling_throughput_per_s = round(float(span[0]) / float(span[1]), 1)
            report.completed_jobs_per_s = round(
                (report.jobs_succeeded + report.jobs_failed) / report.wall_s, 1
            )
            waits = [
                float(r[0])
                for r in (
                    await conn.execute(
                        text("SELECT queue_wait_s FROM jobs WHERE queue_wait_s IS NOT NULL")
                    )
                ).all()
            ]
            report.queue_wait_s = dist(waits, 50, 95, 99)
            high = [
                float(r[0])
                for r in (
                    await conn.execute(
                        text(
                            "SELECT queue_wait_s FROM jobs WHERE priority >= 7 AND queue_wait_s IS NOT NULL"
                        )
                    )
                ).all()
            ]
            low = [
                float(r[0])
                for r in (
                    await conn.execute(
                        text(
                            "SELECT queue_wait_s FROM jobs WHERE priority <= 2 AND queue_wait_s IS NOT NULL"
                        )
                    )
                ).all()
            ]
            report.queue_wait_high_priority_p50_s = round(percentile(high, 50), 3)
            report.queue_wait_low_priority_p50_s = round(percentile(low, 50), 3)
            report.requeues = int(
                (
                    await conn.execute(
                        text("SELECT count(*) FROM audit_log WHERE action = 'job.requeued'")
                    )
                ).scalar_one()
            )
        report.tick_ms = dist(tick_durations, 50, 95, 99)
        del metrics_text
    finally:
        SchedulerService.tick = original_tick  # type: ignore[method-assign]
        stop_sampling.set()
        for agent in agents:
            agent.kill()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await live.aclose()
        await engine.dispose()
        shutil.rmtree(workdir, ignore_errors=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--database-url",
        default="postgresql+asyncpg://devicelab:devicelab@localhost:55432/devicelab_loadtest",
    )
    parser.add_argument(
        "--admin-url", default="postgresql+asyncpg://devicelab:devicelab@localhost:55432/postgres"
    )
    parser.add_argument("--devices", type=int, default=50)
    parser.add_argument("--sites", type=int, default=5)
    parser.add_argument("--jobs", type=int, default=520)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--db-container", default=None, help="docker container name, for CPU sampling"
    )
    parser.add_argument("--step-latency", type=float, nargs=2, default=(0.05, 0.15))
    parser.add_argument("--step-failure-rate", type=float, default=0.02)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()
    report = asyncio.run(
        run(
            args.database_url,
            args.admin_url,
            devices=args.devices,
            sites=args.sites,
            jobs=args.jobs,
            seed=args.seed,
            container=args.db_container,
            step_latency=tuple(args.step_latency),
            step_failure_rate=args.step_failure_rate,
            timeout_s=args.timeout,
        )
    )
    print(json.dumps(asdict(report), indent=2))
    if args.json:
        args.json.write_text(json.dumps(asdict(report), indent=2) + "\n")
    if report.double_leases != 0:
        raise SystemExit("FAIL: a device was leased twice at the same time")
    if (
        report.jobs_succeeded + report.jobs_failed + report.unsatisfiable_left_queued
        != report.jobs_submitted
    ):
        raise SystemExit("FAIL: jobs were lost or duplicated")


if __name__ == "__main__":
    main()
