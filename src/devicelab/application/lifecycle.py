"""State changes that span jobs, executions, leases and devices.

Every path that ends an execution (agent report, lease expiry, corrupt health, operator
cancel) goes through here, inside the caller's transaction. Keeping it in one place is what
makes "requeued exactly once" a property of the code rather than of each caller: each
method locks the execution row first and does nothing if it is already terminal.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from devicelab.application.config import ControlPlaneConfig
from devicelab.application.ports import AgentGateway, Clock, EventPublisher, UnitOfWork
from devicelab.application.records import DeviceRecord, ExecutionRecord, JobRecord
from devicelab.application.support import SYSTEM, Actor, audit, emit
from devicelab.domain.failures import FailureClass, JobAction, decide_job_outcome, is_infrastructure
from devicelab.domain.models import DeviceState, ExecutionState, JobState, LeaseState
from devicelab.domain.state_machines import (
    transition_device,
    transition_execution,
    transition_job,
)

TERMINAL_EXECUTION = {
    ExecutionState.SUCCEEDED.value,
    ExecutionState.FAILED.value,
    ExecutionState.CANCELLED.value,
    ExecutionState.LOST.value,
}
IN_USE = {DeviceState.LEASED.value, DeviceState.BUSY.value}


class Lifecycle:
    def __init__(
        self,
        config: ControlPlaneConfig,
        clock: Clock,
        events: EventPublisher,
        gateway: AgentGateway,
    ) -> None:
        self._cfg = config
        self._clock = clock
        self._events = events
        self._gateway = gateway

    # -- device helpers ---------------------------------------------------------------

    async def set_device_state(
        self, uow: UnitOfWork, device: DeviceRecord, target: DeviceState, **extra: object
    ) -> None:
        """Validated state change; no-op when already there."""
        current = DeviceState(device.state)
        transition_device(current, target)
        if current is not target:
            extra = {**extra, "state_changed_at": self._clock.now()}
        await uow.devices.update(device.id, state=target.value, **extra)
        emit(uow, self._events, "device.updated", id=str(device.id), state=target.value)

    async def quarantine_device(
        self, uow: UnitOfWork, device: DeviceRecord, reason: str, actor: Actor = SYSTEM
    ) -> None:
        await self.set_device_state(uow, device, DeviceState.QUARANTINED, quarantine_reason=reason)
        await audit(
            uow, actor, "device.quarantined", "device", device.id, self._clock.now(), reason=reason
        )

    async def _settle_device(
        self, uow: UnitOfWork, device: DeviceRecord, target: DeviceState, **extra: object
    ) -> None:
        """Move a device out of leased/busy; leave it alone if an operator already changed it."""
        if device.state in IN_USE:
            await self.set_device_state(uow, device, target, **extra)
        elif extra:
            await uow.devices.update(device.id, **extra)

    # -- execution endings ------------------------------------------------------------

    @staticmethod
    async def lock_execution(
        uow: UnitOfWork, execution_id: UUID
    ) -> tuple[JobRecord, ExecutionRecord] | None:
        """Lock the job, then the execution.

        Every path that touches both takes locks in the same order (job, execution, lease,
        device). A late report from an agent and a lease reclaim then queue up behind each
        other instead of deadlocking.
        """
        peek = await uow.executions.get(execution_id)
        if peek is None:
            return None
        job = await uow.jobs.get(peek.job_id, for_update=True)
        ex = await uow.executions.get(execution_id, for_update=True)
        if job is None or ex is None:
            return None
        return job, ex

    async def complete_execution(
        self, uow: UnitOfWork, execution_id: UUID, *, session_restarts: int = 0, reboots: int = 0
    ) -> bool:
        locked = await self.lock_execution(uow, execution_id)
        if locked is None or locked[1].state in TERMINAL_EXECUTION:
            return False
        job, ex = locked
        now = self._clock.now()
        transition_execution(ExecutionState(ex.state), ExecutionState.SUCCEEDED)
        await uow.executions.update(
            ex.id,
            state=ExecutionState.SUCCEEDED.value,
            finished_at=now,
            session_restarts=session_restarts,
            reboots=reboots,
        )
        await self._release_lease(uow, ex.lease_id, LeaseState.RELEASED, "completed")
        device = await uow.devices.get(ex.device_id, for_update=True)
        if device:
            await self._settle_device(
                uow, device, DeviceState.AVAILABLE, consecutive_infra_failures=0
            )
        if job.state not in (JobState.CANCELLED.value, JobState.FAILED.value):
            siblings = await uow.executions.list_for_job(job.id)
            current = [e for e in siblings if e.attempt == ex.attempt]
            if all(e.state == ExecutionState.SUCCEEDED.value or e.id == ex.id for e in current):
                transition_job(JobState(job.state), JobState.SUCCEEDED)
                await uow.jobs.update(
                    job.id, state=JobState.SUCCEEDED.value, finished_at=now, error=None
                )
                emit(uow, self._events, "job.updated", id=str(job.id), state="succeeded")
        emit(uow, self._events, "execution.updated", id=str(ex.id), state="succeeded")
        return True

    async def fail_execution(
        self,
        uow: UnitOfWork,
        execution_id: UUID,
        *,
        failure_class: FailureClass,
        error: str,
        lost: bool = False,
        session_restarts: int = 0,
        reboots: int = 0,
    ) -> bool:
        """End an execution as failed (agent said so) or lost (agent stopped answering)."""
        locked = await self.lock_execution(uow, execution_id)
        if locked is None or locked[1].state in TERMINAL_EXECUTION:
            return False
        job, ex = locked
        now = self._clock.now()
        target = ExecutionState.LOST if lost else ExecutionState.FAILED
        transition_execution(ExecutionState(ex.state), target)
        await uow.executions.update(
            ex.id,
            state=target.value,
            finished_at=now,
            failure_class=failure_class.value,
            error=error,
            session_restarts=session_restarts,
            reboots=reboots,
        )
        await self._release_lease(
            uow,
            ex.lease_id,
            LeaseState.EXPIRED if lost else LeaseState.RELEASED,
            "ttl_expired" if lost else "failed",
        )

        device = await uow.devices.get(ex.device_id, for_update=True)
        if device is None:
            return True

        infra = is_infrastructure(failure_class)
        streak = device.consecutive_infra_failures + 1 if infra else 0
        outcome = decide_job_outcome(
            failure_class,
            attempts=job.attempts,
            max_attempts=job.max_attempts,
            consecutive_infra_failures=streak,
            quarantine_threshold=self._cfg.quarantine_threshold,
        )

        if outcome.quarantine_device and device.state in IN_USE:
            await uow.devices.update(device.id, consecutive_infra_failures=streak)
            await self.quarantine_device(
                uow, device, f"{streak} consecutive infrastructure failures ({failure_class.value})"
            )
        elif lost:
            await self._settle_device(
                uow, device, DeviceState.OFFLINE, consecutive_infra_failures=streak
            )
        else:
            await self._settle_device(
                uow, device, DeviceState.AVAILABLE, consecutive_infra_failures=streak
            )

        if job.state in (JobState.CANCELLED.value, JobState.SUCCEEDED.value, JobState.FAILED.value):
            return True
        await self._cancel_siblings(uow, job, keep=ex.id, reason="sibling execution failed")
        if outcome.action is JobAction.REQUEUE:
            await self._requeue(uow, job, device.id, failure_class, now)
        else:
            transition_job(JobState(job.state), JobState.FAILED)
            await uow.jobs.update(
                job.id,
                state=JobState.FAILED.value,
                finished_at=now,
                failure_class=failure_class.value,
                error=error,
            )
            emit(uow, self._events, "job.updated", id=str(job.id), state="failed")
        emit(uow, self._events, "execution.updated", id=str(ex.id), state=target.value)
        return True

    async def _requeue(
        self, uow: UnitOfWork, job: JobRecord, device_id: UUID, fc: FailureClass, now: datetime
    ) -> None:
        transition_job(JobState(job.state), JobState.QUEUED)
        excluded = [*job.excluded_device_ids, device_id]
        # queued_at is kept on purpose: a requeued job has already waited and keeps its age.
        await uow.jobs.update(
            job.id,
            state=JobState.QUEUED.value,
            excluded_device_ids=excluded,
            failure_class=fc.value,
        )
        await audit(
            uow,
            SYSTEM,
            "job.requeued",
            "job",
            job.id,
            now,
            failure_class=fc.value,
            excluded_device=str(device_id),
            attempt=job.attempts,
        )
        emit(uow, self._events, "job.updated", id=str(job.id), state="queued")

    async def cancel_execution(self, uow: UnitOfWork, ex: ExecutionRecord, reason: str) -> None:
        if ex.state in TERMINAL_EXECUTION:
            return
        locked = await uow.executions.get(ex.id, for_update=True)
        if locked is None or locked.state in TERMINAL_EXECUTION:
            return
        transition_execution(ExecutionState(locked.state), ExecutionState.CANCELLED)
        await uow.executions.update(
            ex.id,
            state=ExecutionState.CANCELLED.value,
            finished_at=self._clock.now(),
            error=reason,
        )
        await self._release_lease(uow, ex.lease_id, LeaseState.RELEASED, "cancelled")
        device = await uow.devices.get(ex.device_id, for_update=True)
        if device:
            await self._settle_device(uow, device, DeviceState.AVAILABLE)
        self._gateway_cancel(uow, ex, reason)
        emit(uow, self._events, "execution.updated", id=str(ex.id), state="cancelled")

    async def _cancel_siblings(
        self, uow: UnitOfWork, job: JobRecord, *, keep: UUID, reason: str
    ) -> None:
        for sibling in await uow.executions.list_for_job(job.id):
            if sibling.id != keep:
                await self.cancel_execution(uow, sibling, reason)

    async def cancel_job_executions(self, uow: UnitOfWork, job: JobRecord, reason: str) -> None:
        for ex in await uow.executions.list_for_job(job.id):
            await self.cancel_execution(uow, ex, reason)

    def _gateway_cancel(self, uow: UnitOfWork, ex: ExecutionRecord, reason: str) -> None:
        uow.on_commit(lambda: self._gateway.cancel(ex.host_id, ex.id, reason))

    async def _release_lease(
        self, uow: UnitOfWork, lease_id: UUID, state: LeaseState, reason: str
    ) -> None:
        lease = await uow.leases.get(lease_id, for_update=True)
        if lease is not None and lease.state == LeaseState.ACTIVE.value:
            await uow.leases.update(
                lease_id, state=state.value, released_at=self._clock.now(), release_reason=reason
            )
