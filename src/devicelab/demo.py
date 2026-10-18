"""Demo mode: templates, schedules, a simulated fleet's inventory and a week of history, so
the dashboard has something real to show on first start.

Everything here goes through the same repositories and services as production code; only
the data is made up. History rows are generated with a fixed seed.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from devicelab.adapters.providers.fake import FakeDevice, FakeFleetProvider, render_screen
from devicelab.application.jobs import SubmitJob
from devicelab.application.records import (
    AlertRecord,
    ArtifactRecord,
    AuditRecord,
    DeviceRecord,
    ExecutionRecord,
    HealthSampleRecord,
    HostRecord,
    JobRecord,
    LeaseRecord,
    LogRecord,
    StepRecord,
)
from devicelab.application.support import Actor
from devicelab.domain.models import Role
from devicelab.domain.workflow import WorkflowTemplate, parse_template
from devicelab.runtime import Runtime

log = structlog.get_logger("demo")

SITES: tuple[tuple[str, str], ...] = (
    ("berlin", "eu"),
    ("austin", "us"),
    ("tokyo", "apac"),
    ("lisbon", "eu"),
)
DEVICE_COUNT = 24
HISTORY_DAYS = 7
FAILURES = (
    (
        "wda_crashed",
        "Could not proxy command to the remote server: WebDriverAgent stopped responding",
    ),
    ("device_unreachable", "Unable to connect to device: lockdown connection dropped"),
    ("assertion_failed", "assertion failed: expected text 'Welcome' in 'welcome_label'"),
    ("step_timeout", "step exceeded its 60s timeout"),
    ("app_crashed", "app crashed on launch (EXC_BAD_ACCESS)"),
)
DEMO_ACTOR = Actor("key:demo-operator", Role.OPERATOR)


def _examples_dir() -> Path:
    # Container images run from /app; a source checkout keeps examples/ beside src/.
    candidates = (
        Path.cwd() / "examples" / "templates",
        Path(__file__).resolve().parents[2] / "examples" / "templates",
    )
    return next((c for c in candidates if c.is_dir()), candidates[0])


async def seed_demo(runtime: Runtime, examples_dir: Path | None = None) -> None:
    svc = runtime.services
    async with runtime.uow_factory() as uow:
        if await uow.hosts.list_all():
            return
    rng = random.Random(2026)
    now = datetime.now(tz=UTC)
    root = examples_dir or _examples_dir()

    templates: list[WorkflowTemplate] = []
    for path in sorted(root.glob("*.yaml")):
        text = path.read_text()
        templates.append(parse_template(text))
        await svc.templates.create(DEMO_ACTOR, text)

    devices = await _seed_fleet(runtime, now, rng)
    for name, template, cron, tz, params in (
        (
            "nightly-store-screenshots",
            "app-store-screenshots",
            "0 2 * * *",
            "Europe/Berlin",
            {"bundle_id": "com.apple.Maps"},
        ),
        (
            "hourly-smoke",
            "app-smoke-test",
            "0 * * * *",
            "UTC",
            {"app_path": "Demo.app", "bundle_id": "com.example.demo"},
        ),
        ("diagnostics-sweep", "collect-diagnostics", "*/20 * * * *", "UTC", {}),
        ("morning-appearance-check", "appearance-sweep", "30 7 * * 1-5", "America/Chicago", {}),
    ):
        await svc.schedules.create(
            DEMO_ACTOR,
            name=name,
            template_name=template,
            cron=cron,
            timezone=tz,
            params=params,
            priority=3 if "nightly" in name else 1,
            misfire_policy="run_once" if "nightly" in name else "skip",
        )
    await _seed_history(runtime, templates, devices, now, rng)
    await svc.alerts.ensure_default_rules()
    await _seed_alerts(runtime, devices, now)

    for i in range(10):
        chosen = templates[i % len(templates)]
        job_params: dict[str, Any] = {}
        if chosen.name == "app-smoke-test":
            job_params = {"app_path": "Demo.app", "bundle_id": "com.example.demo"}
        elif chosen.name == "app-store-screenshots":
            job_params = {"bundle_id": "com.apple.Maps"}
        await svc.jobs.submit(
            DEMO_ACTOR,
            SubmitJob(template=chosen.name, params=job_params, priority=rng.randint(0, 6)),
        )
    log.info("demo_seeded", devices=len(devices))


async def _seed_fleet(runtime: Runtime, now: datetime, rng: random.Random) -> list[DeviceRecord]:
    per_site = [DEVICE_COUNT // len(SITES)] * len(SITES)
    records: list[DeviceRecord] = []
    async with runtime.uow_factory() as uow:
        for (site, region), count in zip(SITES, per_site, strict=True):
            host = HostRecord(
                id=uuid4(),
                name=f"demo-{site}",
                site=site,
                region=region,
                max_concurrency=6,
                created_at=now - timedelta(days=HISTORY_DAYS + 1),
            )
            await uow.hosts.add(host)
            provider = FakeFleetProvider(site, count, seed=7)
            for fake in provider.devices.values():
                record = _device_record(host, fake, now)
                await uow.devices.add(record)
                records.append(record)
        await uow.commit()
    # One device parked for maintenance, one benched after repeated failures.
    parked, benched = records[3], records[11]
    async with runtime.uow_factory() as uow:
        await uow.devices.update(
            parked.id, state="maintenance", state_changed_at=now - timedelta(hours=5)
        )
        await uow.devices.update(
            benched.id,
            state="quarantined",
            state_changed_at=now - timedelta(hours=2),
            consecutive_infra_failures=3,
            quarantine_reason="3 consecutive infrastructure failures (wda_crashed)",
        )
        await uow.audit.add(
            AuditRecord(
                ts=now - timedelta(hours=2),
                actor="system",
                action="device.quarantined",
                target_type="device",
                target_id=str(benched.id),
                details={"reason": "3 consecutive infrastructure failures (wda_crashed)"},
            )
        )
        for hours in range(48, 0, -1):
            for device in rng.sample(records, 8):
                battery = 100 - ((hours * 7 + device.created_at.minute) % 70)
                await uow.devices.add_sample(
                    HealthSampleRecord(
                        device_id=device.id,
                        ts=now - timedelta(hours=hours),
                        state="available",
                        thermal=rng.choice(["nominal"] * 5 + ["fair", "serious"]),
                        connectivity="connected",
                        battery_pct=battery,
                    )
                )
        await uow.commit()
    return records


def _device_record(host: HostRecord, fake: FakeDevice, now: datetime) -> DeviceRecord:
    return DeviceRecord(
        id=uuid4(),
        host_id=host.id,
        udid=fake.udid,
        name=fake.name,
        model=fake.model,
        model_family=fake.family,
        ios_version=fake.ios,
        kind="fake",
        site=host.site,
        region=host.region,
        state="offline",
        created_at=now - timedelta(days=HISTORY_DAYS + 1),
        labels=list(fake.labels),
        battery_pct=round(fake.battery),
        storage_free_gb=64.0,
        last_seen=now,
        state_changed_at=now,
    )


async def _seed_history(
    runtime: Runtime,
    templates: list[WorkflowTemplate],
    devices: list[DeviceRecord],
    now: datetime,
    rng: random.Random,
) -> None:
    store = runtime.services.executions._store
    usable = [d for d in devices if d.state == "offline"]
    async with runtime.uow_factory() as uow:
        for n in range(260):
            template = rng.choice(templates)
            created = now - timedelta(minutes=rng.randint(30, HISTORY_DAYS * 24 * 60))
            outcome = rng.choices(
                ["ok", "recovered", "test_failure", "infra_failure"], [78, 12, 6, 4]
            )[0]
            first_device = rng.choice(usable)
            steps = [s.model_dump(mode="json") for s in template.steps]
            queue_wait = round(rng.expovariate(1 / 6.0), 2)
            job_id = uuid4()
            attempts = 2 if outcome == "recovered" else 1
            state = "succeeded" if outcome in ("ok", "recovered") else "failed"
            failure = (
                rng.choice(FAILURES[:2] + FAILURES[3:4])
                if outcome in ("recovered", "infra_failure")
                else FAILURES[2]
            )
            cursor = created + timedelta(seconds=queue_wait)
            execs: list[tuple[UUID, bool]] = []

            job = JobRecord(
                id=job_id,
                template_id=None,
                template_name=template.name,
                spec=template.model_dump(mode="json"),
                params={},
                requirement={"count": 1},
                priority=rng.randint(0, 6),
                state=state,
                attempts=attempts,
                max_attempts=3,
                submitted_by=rng.choice(
                    ["schedule:hourly-smoke", "key:demo-operator", "schedule:diagnostics-sweep"]
                ),
                created_at=created,
                queued_at=created,
                queue_wait_s=queue_wait,
                started_at=cursor,
                excluded_device_ids=[first_device.id] if attempts == 2 else [],
            )
            plan: list[tuple[DeviceRecord, str, bool]] = []
            if outcome == "recovered":
                plan = [
                    (
                        first_device,
                        "lost" if failure[0] == "device_unreachable" else "failed",
                        False,
                    ),
                    (rng.choice([d for d in usable if d.id != first_device.id]), "succeeded", True),
                ]
            elif outcome == "ok":
                plan = [(first_device, "succeeded", True)]
            else:
                plan = [(first_device, "failed", True)]

            for attempt, (device, ex_state, final) in enumerate(plan, start=1):
                duration = rng.uniform(8, 55)
                lease_id, ex_id = uuid4(), uuid4()
                end = cursor + timedelta(seconds=duration)
                host_id = device.host_id
                if attempt == 1:
                    await uow.jobs.add(job)
                await uow.leases.add(
                    LeaseRecord(
                        id=lease_id,
                        job_id=job_id,
                        device_id=device.id,
                        host_id=host_id,
                        fencing_token=n * 2 + attempt,
                        state="released" if ex_state != "lost" else "expired",
                        acquired_at=cursor,
                        expires_at=end + timedelta(seconds=45),
                        released_at=end,
                        release_reason="completed" if ex_state == "succeeded" else "failed",
                    )
                )
                fclass = None if ex_state == "succeeded" else failure[0]
                await uow.executions.add(
                    ExecutionRecord(
                        id=ex_id,
                        job_id=job_id,
                        device_id=device.id,
                        host_id=host_id,
                        lease_id=lease_id,
                        attempt=attempt,
                        state=ex_state,
                        fencing_token=n * 2 + attempt,
                        created_at=cursor,
                        steps=steps,
                        dispatched_at=cursor,
                        started_at=cursor + timedelta(seconds=1),
                        finished_at=end,
                        failure_class=fclass,
                        error=None if fclass is None else failure[1],
                        session_restarts=1 if outcome == "recovered" and attempt == 1 else 0,
                    )
                )
                fail_at = rng.randrange(len(steps)) if ex_state != "succeeded" else -1
                t = cursor
                for index, step in enumerate(steps):
                    step_ms = int(rng.uniform(600, 9000))
                    s_state = (
                        "succeeded"
                        if index < fail_at or fail_at == -1
                        else ("failed" if index == fail_at else "skipped")
                    )
                    await uow.executions.upsert_step(
                        StepRecord(
                            id=uuid4(),
                            execution_id=ex_id,
                            index=index,
                            step_id=step["id"],
                            type=step["type"],
                            name=step.get("name") or step["id"],
                            state=s_state,
                            attempts=1 if s_state != "skipped" else 0,
                            started_at=t,
                            finished_at=t + timedelta(milliseconds=step_ms)
                            if s_state != "skipped"
                            else None,
                            duration_ms=step_ms if s_state != "skipped" else None,
                            failure_class=fclass if s_state == "failed" else None,
                            error=failure[1] if s_state == "failed" else None,
                        )
                    )
                    await uow.executions.add_log(
                        LogRecord(
                            execution_id=ex_id,
                            ts=t,
                            level="info" if s_state != "failed" else "warning",
                            message=f"{step['id']}: {step['type']}"
                            if s_state != "failed"
                            else f"{step['id']} failed [{fclass}]: {failure[1]}",
                            step_index=index,
                        )
                    )
                    t += timedelta(milliseconds=step_ms)
                if ex_state == "succeeded" and any(
                    s["type"] in ("screenshot", "capture_screenshots") for s in steps
                ):
                    key = f"executions/{ex_id}/{uuid4()}-home.png"
                    fake = FakeDevice(
                        device.udid,
                        device.name,
                        device.model,
                        device.model_family,
                        device.ios_version,
                        battery=float(device.battery_pct or 80),
                        appearance=rng.choice(["light", "dark"]),
                        foreground="com.apple.Preferences",
                    )
                    await store.put(key, render_screen(fake, float(n)), "image/png")
                    await uow.artifacts.add(
                        ArtifactRecord(
                            id=uuid4(),
                            execution_id=ex_id,
                            name="home.png",
                            kind="screenshot",
                            content_type="image/png",
                            size_bytes=2048,
                            storage_key=key,
                            created_at=end,
                            expires_at=end + timedelta(days=14),
                            step_index=1,
                        )
                    )
                execs.append((ex_id, final))
                if attempt == 1 and len(plan) == 2:
                    await uow.audit.add(
                        AuditRecord(
                            ts=end,
                            actor="system",
                            action="job.requeued",
                            target_type="job",
                            target_id=str(job_id),
                            details={
                                "failure_class": failure[0],
                                "excluded_device": str(device.id),
                                "attempt": 1,
                            },
                        )
                    )
                cursor = end + timedelta(seconds=rng.uniform(1, 4))
            await uow.jobs.update(job_id, finished_at=cursor)
            if n % 40 == 39:
                await uow.commit()
        await uow.commit()


async def _seed_alerts(runtime: Runtime, devices: list[DeviceRecord], now: datetime) -> None:
    benched = next(d for d in devices if d.id and d.udid == devices[11].udid)
    async with runtime.uow_factory() as uow:
        for kind, severity, key, message, state, ago in (
            (
                "device_offline",
                "warning",
                f"device_offline:{benched.id}",
                f"{benched.name} ({benched.site}) has been offline for 12 min",
                "resolved",
                300,
            ),
            (
                "failure_rate",
                "warning",
                "failure_rate",
                "34% of 41 recent executions failed",
                "resolved",
                1500,
            ),
            (
                "lease_reclaimed",
                "warning",
                f"lease_reclaimed:{uuid4()}",
                "lease on Lisbon iPhone 15 #2 was reclaimed from an unresponsive agent",
                "resolved",
                900,
            ),
            (
                "queue_wait",
                "warning",
                "queue_wait",
                "oldest queued job has waited 412s (SLA 300s)",
                "resolved",
                2400,
            ),
        ):
            await uow.alerts.add_alert(
                AlertRecord(
                    id=uuid4(),
                    rule_id=None,
                    kind=kind,
                    severity=severity,
                    dedupe_key=key,
                    message=message,
                    payload={},
                    state=state,
                    occurrences=3,
                    first_seen=now - timedelta(minutes=ago),
                    last_seen=now - timedelta(minutes=ago - 7),
                    last_notified_at=now - timedelta(minutes=ago),
                    resolved_at=now - timedelta(minutes=ago - 12),
                )
            )
        await uow.commit()


__all__ = ["seed_demo"]
