"""The scheduler tick and lease reclamation.

One tick = one transaction: lock candidate jobs and available devices with SKIP LOCKED,
ask the pure planner for assignments, write leases. A second scheduler process running at
the same time would simply see disjoint rows, and the partial unique index on active
leases is the backstop if anything above it is ever wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from devicelab.application.config import ControlPlaneConfig
from devicelab.application.lifecycle import Lifecycle
from devicelab.application.ports import (
    AgentGateway,
    Clock,
    EventPublisher,
    IdFactory,
    UnitOfWork,
    UowFactory,
)
from devicelab.application.records import (
    DeviceRecord,
    ExecutionRecord,
    JobRecord,
    LeaseRecord,
    StepRecord,
)
from devicelab.application.support import SYSTEM, audit, emit
from devicelab.domain.errors import ValidationFailedError
from devicelab.domain.failures import FailureClass
from devicelab.domain.leasing import expiry_for, next_fence
from devicelab.domain.models import (
    DeviceRequirement,
    DeviceState,
    DeviceView,
    ExecutionState,
    JobState,
    JobView,
    LeaseState,
    SchedulerPolicy,
    Thermal,
)
from devicelab.domain.scheduler import plan_assignments
from devicelab.domain.state_machines import transition_job
from devicelab.domain.templating import render_steps
from devicelab.domain.workflow import WorkflowTemplate


@dataclass(frozen=True, slots=True)
class TickResult:
    jobs_assigned: int = 0
    devices_leased: int = 0
    jobs_failed: int = 0


def requirement_from_dict(raw: dict[str, Any]) -> DeviceRequirement:
    return DeviceRequirement(
        count=int(raw.get("count", 1)),
        min_ios=raw.get("min_ios"),
        max_ios=raw.get("max_ios"),
        model_family=raw.get("model_family"),
        site=raw.get("site"),
        region=raw.get("region"),
        labels=frozenset(raw.get("labels") or ()),
    )


def device_view(record: DeviceRecord) -> DeviceView:
    return DeviceView(
        id=record.id,
        host_id=record.host_id,
        site=record.site,
        region=record.region,
        model=record.model,
        model_family=record.model_family,
        ios_version=record.ios_version,
        labels=frozenset(record.labels),
        battery_pct=record.battery_pct,
        thermal=Thermal(record.thermal) if record.thermal in Thermal else Thermal.NOMINAL,
        state=DeviceState(record.state),
    )


class SchedulerService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        ids: IdFactory,
        config: ControlPlaneConfig,
        lifecycle: Lifecycle,
        events: EventPublisher,
        gateway: AgentGateway,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids
        self._cfg = config
        self._life = lifecycle
        self._events = events
        self._gateway = gateway

    async def tick(self) -> TickResult:
        now = self._clock.now()
        async with self._uow() as uow:
            jobs = await uow.jobs.lock_queued(self._cfg.scan_limit)
            if not jobs:
                return TickResult()
            devices = await uow.devices.lock_available()
            if not devices:
                return TickResult()
            hosts = {h.id: h for h in await uow.hosts.list_all()}
            active = await uow.leases.active_counts_by_host()

            by_job = {j.id: j for j in jobs}
            by_device = {d.id: d for d in devices}
            plan = plan_assignments(
                [
                    JobView(
                        id=j.id,
                        priority=j.priority,
                        queued_at=j.queued_at,
                        requirement=requirement_from_dict(j.requirement),
                        excluded_device_ids=frozenset(j.excluded_device_ids),
                    )
                    for j in jobs
                ],
                [device_view(d) for d in devices],
                host_active=active,
                host_limits={h.id: h.max_concurrency for h in hosts.values()},
                now=now,
                policy=SchedulerPolicy(
                    aging_per_minute=self._cfg.aging_per_minute,
                    min_battery_pct=self._cfg.min_battery_pct,
                ),
            )

            assigned = leased = failed = 0
            for assignment in plan:
                job = by_job[assignment.job_id]
                chosen = [by_device[i] for i in assignment.device_ids]
                try:
                    await self._grant(uow, job, chosen, now)
                except ValidationFailedError as exc:
                    await self._fail_unrenderable(uow, job, exc, now)
                    failed += 1
                    continue
                assigned += 1
                leased += len(chosen)
            await uow.commit()
        return TickResult(assigned, leased, failed)

    async def _grant(
        self, uow: UnitOfWork, job: JobRecord, devices: list[DeviceRecord], now: Any
    ) -> None:
        template = WorkflowTemplate.model_validate(job.spec)
        attempt = job.attempts + 1
        prepared: list[tuple[DeviceRecord, list[dict[str, Any]]]] = []
        for device in devices:
            rendered = render_steps(
                template,
                job.params,
                {
                    "udid": device.udid,
                    "name": device.name,
                    "model": device.model,
                    "ios_version": device.ios_version,
                    "site": device.site,
                },
            )
            prepared.append((device, [s.model_dump(mode="json") for s in rendered]))

        for device, steps in prepared:
            token = next_fence(device.fence_counter)
            lease = LeaseRecord(
                id=self._ids(),
                job_id=job.id,
                device_id=device.id,
                host_id=device.host_id,
                fencing_token=token,
                state=LeaseState.ACTIVE.value,
                acquired_at=now,
                expires_at=expiry_for(now, self._cfg.lease_ttl_s),
            )
            await uow.leases.add(lease)
            await self._life.set_device_state(uow, device, DeviceState.LEASED, fence_counter=token)
            execution = ExecutionRecord(
                id=self._ids(),
                job_id=job.id,
                device_id=device.id,
                host_id=device.host_id,
                lease_id=lease.id,
                attempt=attempt,
                state=ExecutionState.PENDING.value,
                fencing_token=token,
                created_at=now,
                steps=steps,
            )
            await uow.executions.add(execution)
            for index, step in enumerate(steps):
                await uow.executions.upsert_step(
                    StepRecord(
                        id=self._ids(),
                        execution_id=execution.id,
                        index=index,
                        step_id=step["id"],
                        type=step["type"],
                        name=step.get("name") or step["id"],
                    )
                )
            host_id = device.host_id
            uow.on_commit(lambda h=host_id: self._gateway.wake(h))  # type: ignore[misc]

        transition_job(JobState(job.state), JobState.LEASED)
        first = job.queue_wait_s is None
        await uow.jobs.update(
            job.id,
            state=JobState.LEASED.value,
            attempts=attempt,
            queue_wait_s=(now - job.created_at).total_seconds() if first else job.queue_wait_s,
            started_at=job.started_at or now,
        )
        emit(uow, self._events, "job.updated", id=str(job.id), state="leased")

    async def _fail_unrenderable(
        self, uow: UnitOfWork, job: JobRecord, exc: ValidationFailedError, now: Any
    ) -> None:
        transition_job(JobState(job.state), JobState.FAILED)
        await uow.jobs.update(
            job.id,
            state=JobState.FAILED.value,
            finished_at=now,
            failure_class=FailureClass.UNKNOWN.value,
            error=f"workflow could not be rendered: {exc.message}",
        )
        await audit(uow, SYSTEM, "job.failed", "job", job.id, now, reason=exc.code)
        emit(uow, self._events, "job.updated", id=str(job.id), state="failed")

    async def reclaim_expired(self) -> int:
        """Take leases back from agents that stopped heartbeating, and requeue their jobs.

        One transaction per lease, and the expired set is only read (not locked) up front:
        the locks that matter are taken by the lifecycle in its usual order, so a reclaim and
        a late report from the same agent serialise instead of deadlocking.
        """
        now = self._clock.now()
        async with self._uow() as uow:
            expired = await uow.leases.list_expired(now, self._cfg.reclaim_batch)
        reclaimed = 0
        for lease in expired:
            async with self._uow() as uow:
                execution = await uow.executions.get_by_lease(lease.id)
                if execution is not None:
                    changed = await self._life.fail_execution(
                        uow,
                        execution.id,
                        failure_class=FailureClass.DEVICE_UNREACHABLE,
                        error="lease expired: the agent stopped heartbeating",
                        lost=True,
                    )
                else:
                    changed = False
                if not changed:
                    current = await uow.leases.get(lease.id, for_update=True)
                    if current is None or current.state != LeaseState.ACTIVE.value:
                        continue
                    await uow.leases.update(
                        lease.id,
                        state=LeaseState.EXPIRED.value,
                        released_at=now,
                        release_reason="ttl_expired",
                    )
                await audit(
                    uow,
                    SYSTEM,
                    "lease.reclaimed",
                    "lease",
                    lease.id,
                    now,
                    device_id=str(lease.device_id),
                    job_id=str(lease.job_id),
                )
                await uow.commit()
                reclaimed += 1
        return reclaimed

    async def active_leases(self) -> int:
        async with self._uow() as uow:
            return await uow.leases.count_active()

    async def queue_depth(self) -> int:
        async with self._uow() as uow:
            return (await uow.jobs.count_by_state()).get(JobState.QUEUED.value, 0)


__all__ = ["SchedulerService", "TickResult", "device_view", "requirement_from_dict"]
