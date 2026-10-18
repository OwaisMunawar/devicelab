"""Shared fixtures for use-case tests: a fake clock, recording ports and a small DSL."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from devicelab.application.config import ControlPlaneConfig
from devicelab.application.ports import Event, UowFactory
from devicelab.application.records import AlertRecord, DeviceRecord, HostRecord, WebhookRecord
from devicelab.application.support import SYSTEM, Actor
from devicelab.container import Services, build_services
from devicelab.domain.models import Role
from devicelab.protocol.messages import (
    ActiveExecution,
    ArtifactRef,
    ArtifactUpload,
    DeviceReport,
    ExecutionFinished,
    HealthReport,
    StepFinished,
    StepLog,
    StepStarted,
)

START = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
ADMIN = Actor("key:test", Role.ADMIN)

TEMPLATE = """
name: {name}
requirements: {{count: {count}}}
steps:
  - {{id: open, type: launch_app, bundle_id: com.apple.Preferences}}
  - {{id: shot, type: screenshot}}
"""


class FakeClock:
    def __init__(self) -> None:
        self.current = START

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class RecordingGateway:
    def __init__(self) -> None:
        self.wakes: list[UUID] = []
        self.cancels: list[tuple[UUID, UUID, str]] = []

    def wake(self, host_id: UUID) -> None:
        self.wakes.append(host_id)

    def cancel(self, host_id: UUID, execution_id: UUID, reason: str) -> None:
        self.cancels.append((host_id, execution_id, reason))


class RecordingBus:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def publish(self, event: Event) -> None:
        self.events.append(event)

    def topics(self) -> list[str]:
        return [e.topic for e in self.events]


class MemoryStore:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.blobs[key] = data

    async def get(self, key: str) -> bytes:
        return self.blobs[key]

    async def delete(self, key: str) -> None:
        self.blobs.pop(key, None)


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[AlertRecord, list[WebhookRecord]]] = []

    async def notify(self, alert: AlertRecord, webhooks: list[WebhookRecord]) -> None:
        self.sent.append((alert, webhooks))


@dataclass
class Env:
    svc: Services
    clock: FakeClock
    gateway: RecordingGateway
    bus: RecordingBus
    store: MemoryStore
    notifier: RecordingNotifier
    uow: UowFactory
    cfg: ControlPlaneConfig
    hosts: dict[str, HostRecord] = field(default_factory=dict)
    seq: int = 0

    async def host(
        self, name: str = "mac-1", site: str = "berlin", *, max_concurrency: int = 8
    ) -> HostRecord:
        token, _ = await self.svc.fleet.create_enrolment_token(
            ADMIN, host_name=name, site=site, region="eu", max_concurrency=max_concurrency
        )
        host, _ = await self.svc.fleet.enrol(token, name)
        self.hosts[name] = host
        return host

    def report(
        self,
        udid: str,
        *,
        ios: str = "18.2",
        model: str = "iPhone 15",
        battery: int = 90,
        thermal: str = "nominal",
        connectivity: str = "connected",
        labels: list[str] | None = None,
    ) -> DeviceReport:
        return DeviceReport(
            udid=udid,
            name=f"Device {udid}",
            model=model,
            model_family="iPad" if "iPad" in model else "iPhone",
            ios_version=ios,
            kind="fake",
            labels=labels or [],
            health=HealthReport(
                udid=udid,
                battery_pct=battery,
                thermal=thermal,
                storage_free_gb=40.0,
                connectivity=connectivity,
            ),
        )

    async def fleet(
        self, n: int = 2, *, host: str = "mac-1", site: str = "berlin", **kw: Any
    ) -> tuple[HostRecord, list[DeviceRecord]]:
        record = self.hosts.get(host) or await self.host(host, site)
        reports = [self.report(f"{host}-u{i}", **kw) for i in range(n)]
        await self.svc.fleet.register_inventory(record, reports)
        devices = await self.svc.fleet.list_devices(limit=200)
        return record, [d for d in devices.items if d.host_id == record.id]

    async def template(self, name: str = "smoke", count: int = 1) -> None:
        await self.svc.templates.create(ADMIN, TEMPLATE.format(name=name, count=count))

    async def submit(self, template: str = "smoke", **kw: Any):  # type: ignore[no-untyped-def]
        from devicelab.application.jobs import SubmitJob

        job, _ = await self.svc.jobs.submit(ADMIN, SubmitJob(template=template, **kw))
        return job

    async def beat(self, host: HostRecord, udids: list[str], **health: Any) -> Any:
        reports = [
            HealthReport(
                udid=u,
                battery_pct=health.get("battery", 90),
                thermal="nominal",
                storage_free_gb=40.0,
                connectivity="connected",
            )
            for u in udids
        ]
        return await self.svc.fleet.heartbeat(host, reports, health.get("executions", []))

    async def dispatch(self, host: HostRecord):  # type: ignore[no-untyped-def]
        return await self.svc.executions.take_dispatchable(host.id)

    def refs(self, items: Any) -> list[ActiveExecution]:
        return [
            ActiveExecution(
                execution_id=str(i.execution.id),
                lease_id=str(i.execution.lease_id),
                fencing_token=i.execution.fencing_token,
            )
            for i in items
        ]

    async def run_ok(self, item: Any) -> None:
        """Play the agent's side of a successful run."""
        ex = item.execution
        token = ex.fencing_token
        for index, step in enumerate(item.steps):
            await self.svc.executions.step_started(
                StepStarted(
                    execution_id=str(ex.id),
                    fencing_token=token,
                    step_index=index,
                    step_id=step["id"],
                )
            )
            await self.svc.executions.step_log(
                StepLog(
                    execution_id=str(ex.id),
                    fencing_token=token,
                    step_index=index,
                    message=f"running {step['id']}",
                )
            )
            await self.svc.executions.step_finished(
                StepFinished(
                    execution_id=str(ex.id),
                    fencing_token=token,
                    step_index=index,
                    state="succeeded",
                    duration_ms=120,
                )
            )
        await self.svc.executions.execution_finished(
            ExecutionFinished(execution_id=str(ex.id), fencing_token=token, state="succeeded")
        )

    async def run_fail(self, item: Any, code: str = "wda_crashed", error: str = "boom") -> None:
        ex = item.execution
        token = ex.fencing_token
        await self.svc.executions.step_started(
            StepStarted(
                execution_id=str(ex.id),
                fencing_token=token,
                step_index=0,
                step_id=item.steps[0]["id"],
            )
        )
        await self.svc.executions.step_finished(
            StepFinished(
                execution_id=str(ex.id),
                fencing_token=token,
                step_index=0,
                state="failed",
                failure_code=code,
                error=error,
            )
        )
        await self.svc.executions.execution_finished(
            ExecutionFinished(
                execution_id=str(ex.id),
                fencing_token=token,
                state="failed",
                failure_code=code,
                error=error,
            )
        )

    async def artifact(self, item: Any, data: bytes = b"png") -> Any:
        import base64

        ex = item.execution
        return await self.svc.executions.artifact_uploaded(
            ArtifactUpload(
                execution_id=str(ex.id),
                fencing_token=ex.fencing_token,
                step_index=1,
                artifact=ArtifactRef(name="shot.png", kind="screenshot", content_type="image/png"),
                data_b64=base64.b64encode(data).decode(),
            )
        )

    async def device(self, device_id: UUID) -> DeviceRecord:
        return await self.svc.fleet.get_device(device_id)


def make_env(uow: UowFactory, **cfg_overrides: Any) -> Env:
    clock, gateway, bus = FakeClock(), RecordingGateway(), RecordingBus()
    store, notifier = MemoryStore(), RecordingNotifier()
    cfg = ControlPlaneConfig(**cfg_overrides)
    svc = build_services(
        uow_factory=uow,
        clock=clock,
        config=cfg,
        store=store,
        notifier=notifier,
        events=bus,
        gateway=gateway,
    )
    return Env(svc, clock, gateway, bus, store, notifier, uow, cfg)


__all__ = ["ADMIN", "SYSTEM", "Env", "make_env"]
