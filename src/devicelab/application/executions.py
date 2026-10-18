"""Agent-facing execution use cases: dispatch, progress reports, artifacts, completion.

Every report carries the fencing token the agent was handed with its assignment. If the
lease has been reclaimed (or re-granted) since, the token no longer matches and the report
is rejected, so a partitioned agent cannot corrupt a job that has moved on.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any
from uuid import UUID

from devicelab.application.config import ControlPlaneConfig
from devicelab.application.lifecycle import TERMINAL_EXECUTION, Lifecycle
from devicelab.application.ports import (
    ArtifactStore,
    Clock,
    EventPublisher,
    IdFactory,
    UnitOfWork,
    UowFactory,
)
from devicelab.application.records import (
    ArtifactRecord,
    ExecutionRecord,
    LogRecord,
    Page,
    StepRecord,
)
from devicelab.application.support import emit
from devicelab.domain.errors import NotFoundError, StaleFenceError, ValidationFailedError
from devicelab.domain.failures import FailureClass, classify
from devicelab.domain.leasing import check_fence
from devicelab.domain.models import DeviceState, ExecutionState, JobState
from devicelab.domain.state_machines import transition_execution, transition_job
from devicelab.protocol.messages import (
    ActiveExecution,
    ArtifactUpload,
    ExecutionFinished,
    StepFinished,
    StepLog,
    StepStarted,
)

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]")


@dataclass(frozen=True, slots=True)
class DispatchItem:
    execution: ExecutionRecord
    device_udid: str
    failure_policy: str
    steps: list[dict[str, Any]]
    correlation_id: str | None


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    orphans: list[str]
    lost: list[UUID]


class ExecutionService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        ids: IdFactory,
        config: ControlPlaneConfig,
        lifecycle: Lifecycle,
        events: EventPublisher,
        store: ArtifactStore,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids
        self._cfg = config
        self._life = lifecycle
        self._events = events
        self._store = store

    # -- dispatch ---------------------------------------------------------------------

    async def take_dispatchable(self, host_id: UUID) -> list[DispatchItem]:
        """Pending executions not yet sent to the host; marks them dispatched."""
        now = self._clock.now()
        items: list[DispatchItem] = []
        async with self._uow() as uow:
            for ex in await uow.executions.list_for_host(host_id, [ExecutionState.PENDING.value]):
                if ex.dispatched_at is not None:
                    continue
                device = await uow.devices.get(ex.device_id)
                job = await uow.jobs.get(ex.job_id)
                if device is None or job is None:
                    continue
                await uow.executions.update(ex.id, dispatched_at=now)
                items.append(
                    DispatchItem(
                        execution=ex,
                        device_udid=device.udid,
                        failure_policy=str(job.spec.get("failure_policy", "abort")),
                        steps=ex.steps,
                        correlation_id=job.correlation_id,
                    )
                )
            await uow.commit()
        return items

    async def reconcile_host(
        self, host_id: UUID, reported: list[ActiveExecution]
    ) -> ReconcileResult:
        """Compare what the agent says it is running with what the database expects.

        Called on every (re)connect. Work the agent no longer knows about is lost and gets
        requeued; work the database no longer wants is an orphan the agent must drop.
        """
        reported_ids = {r.execution_id for r in reported}
        orphans: list[str] = []
        lost: list[UUID] = []
        async with self._uow() as uow:
            expected = await uow.executions.list_for_host(
                host_id, [ExecutionState.PENDING.value, ExecutionState.RUNNING.value]
            )
            for ex in expected:
                started_on_agent = ex.dispatched_at is not None or ex.state == "running"
                if started_on_agent and str(ex.id) not in reported_ids:
                    changed = await self._life.fail_execution(
                        uow,
                        ex.id,
                        failure_class=FailureClass.DEVICE_UNREACHABLE,
                        error="agent restarted and no longer has this execution",
                        lost=True,
                    )
                    if changed:
                        lost.append(ex.id)
                elif ex.dispatched_at is not None and ex.state == "pending":
                    # The agent has it; make sure it is not re-sent on the next dispatch pass.
                    continue
            live = {str(e.id) for e in expected}
            for ref in reported:
                if ref.execution_id not in live:
                    orphans.append(ref.execution_id)
            await uow.commit()
        return ReconcileResult(orphans, lost)

    # -- progress reports -------------------------------------------------------------

    async def _authorise(
        self, uow: UnitOfWork, execution_id: str, fencing_token: int, *, lock: bool = True
    ) -> ExecutionRecord:
        """Check the report against the lease. `lock=False` leaves row locking to the caller,
        which must then take them in lifecycle order (job first)."""
        try:
            key = UUID(execution_id)
        except ValueError:
            raise NotFoundError("execution not found", code="execution_not_found") from None
        ex = await uow.executions.get(key, for_update=lock)
        if ex is None:
            raise NotFoundError("execution not found", code="execution_not_found")
        lease = await uow.leases.get(ex.lease_id)
        if lease is None or lease.state != "active":
            raise StaleFenceError("lease is no longer active", details={"execution": execution_id})
        check_fence(lease.fencing_token, fencing_token)
        if ex.state in TERMINAL_EXECUTION:
            raise StaleFenceError("execution already finished", details={"execution": execution_id})
        return ex

    async def step_started(self, msg: StepStarted) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            ex = await self._authorise(uow, msg.execution_id, msg.fencing_token, lock=False)
            locked = await self._life.lock_execution(uow, ex.id)
            if locked is None:
                raise NotFoundError("execution not found", code="execution_not_found")
            job, ex = locked
            if ex.state in TERMINAL_EXECUTION:
                raise StaleFenceError("execution already finished")
            if ex.state == ExecutionState.PENDING.value:
                transition_execution(ExecutionState.PENDING, ExecutionState.RUNNING)
                await uow.executions.update(ex.id, state="running", started_at=now)
                device = await uow.devices.get(ex.device_id, for_update=True)
                if device and device.state == DeviceState.LEASED.value:
                    await self._life.set_device_state(uow, device, DeviceState.BUSY)
                if job.state == JobState.LEASED.value:
                    transition_job(JobState.LEASED, JobState.RUNNING)
                    await uow.jobs.update(job.id, state="running")
                    emit(uow, self._events, "job.updated", id=str(job.id), state="running")
            step = await uow.executions.get_step(ex.id, msg.step_index)
            if step is None:
                raise NotFoundError("step not found", code="step_not_found")
            await uow.executions.upsert_step(
                _replace_step(
                    step,
                    state="running",
                    attempts=msg.attempt,
                    started_at=step.started_at or now,
                )
            )
            emit(uow, self._events, "execution.updated", id=str(ex.id), state="running")
            await uow.commit()

    async def step_log(self, msg: StepLog) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            ex = await self._authorise(uow, msg.execution_id, msg.fencing_token)
            log_id = await uow.executions.add_log(
                LogRecord(
                    execution_id=ex.id,
                    ts=now,
                    level=msg.level,
                    message=msg.message,
                    step_index=msg.step_index,
                )
            )
            emit(
                uow,
                self._events,
                "execution.log",
                execution_id=str(ex.id),
                id=log_id,
                level=msg.level,
                message=msg.message,
                ts=now.isoformat(),
                step_index=msg.step_index,
            )
            await uow.commit()

    async def step_finished(self, msg: StepFinished) -> None:
        now = self._clock.now()
        async with self._uow() as uow:
            ex = await self._authorise(uow, msg.execution_id, msg.fencing_token)
            step = await uow.executions.get_step(ex.id, msg.step_index)
            if step is None:
                raise NotFoundError("step not found", code="step_not_found")
            failure_class = (
                classify(msg.failure_code, msg.error or "").value if msg.state == "failed" else None
            )
            await uow.executions.upsert_step(
                _replace_step(
                    step,
                    state=msg.state,
                    attempts=msg.attempts,
                    finished_at=now,
                    duration_ms=msg.duration_ms,
                    failure_class=failure_class,
                    error=msg.error,
                    output=msg.output,
                )
            )
            emit(uow, self._events, "execution.updated", id=str(ex.id), state=ex.state)
            await uow.commit()

    async def artifact_uploaded(self, msg: ArtifactUpload) -> ArtifactRecord:
        try:
            data = base64.b64decode(msg.data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationFailedError(
                "artifact is not valid base64", code="invalid_artifact"
            ) from None
        if len(data) > MAX_ARTIFACT_BYTES:
            raise ValidationFailedError("artifact too large", code="artifact_too_large")
        now = self._clock.now()
        async with self._uow() as uow:
            ex = await self._authorise(uow, msg.execution_id, msg.fencing_token)
            artifact_id = self._ids()
            safe = _SAFE_NAME.sub("_", msg.artifact.name)[:96]
            key = f"executions/{ex.id}/{artifact_id}-{safe}"
            await self._store.put(key, data, msg.artifact.content_type)
            record = ArtifactRecord(
                id=artifact_id,
                execution_id=ex.id,
                name=msg.artifact.name,
                kind=msg.artifact.kind,
                content_type=msg.artifact.content_type,
                size_bytes=len(data),
                storage_key=key,
                created_at=now,
                expires_at=now + timedelta(days=self._cfg.artifact_retention_days),
                step_index=msg.step_index,
            )
            await uow.artifacts.add(record)
            emit(uow, self._events, "execution.artifact", execution_id=str(ex.id), name=record.name)
            await uow.commit()
        return record

    async def execution_finished(self, msg: ExecutionFinished) -> None:
        async with self._uow() as uow:
            ex = await self._authorise(uow, msg.execution_id, msg.fencing_token, lock=False)
            applied: bool
            if msg.state == "succeeded":
                applied = await self._life.complete_execution(
                    uow, ex.id, session_restarts=msg.session_restarts, reboots=msg.reboots
                )
            elif msg.state == "failed":
                applied = await self._life.fail_execution(
                    uow,
                    ex.id,
                    failure_class=classify(msg.failure_code, msg.error or ""),
                    error=msg.error or "execution failed",
                    session_restarts=msg.session_restarts,
                    reboots=msg.reboots,
                )
            else:
                locked = await self._life.lock_execution(uow, ex.id)
                applied = locked is not None and locked[1].state not in TERMINAL_EXECUTION
                if locked is not None and applied:
                    await self._life.cancel_execution(
                        uow, locked[1], msg.error or "cancelled by agent"
                    )
            if not applied:
                # Someone (a reclaim, an operator) ended it between the fence check and the lock.
                raise StaleFenceError(
                    "execution already finished", details={"execution": msg.execution_id}
                )
            await uow.commit()

    # -- reads ------------------------------------------------------------------------

    async def get(
        self, execution_id: UUID
    ) -> tuple[ExecutionRecord, list[StepRecord], list[ArtifactRecord]]:
        async with self._uow() as uow:
            ex = await uow.executions.get(execution_id)
            if ex is None:
                raise NotFoundError("execution not found", code="execution_not_found")
            return (
                ex,
                await uow.executions.list_steps(ex.id),
                await uow.artifacts.list_for_execution(ex.id),
            )

    async def search(
        self,
        *,
        job_id: UUID | None = None,
        device_id: UUID | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[ExecutionRecord]:
        async with self._uow() as uow:
            return await uow.executions.search(
                job_id=job_id, device_id=device_id, state=state, limit=limit, offset=offset
            )

    async def logs(
        self, execution_id: UUID, after_id: int = 0, limit: int = 500
    ) -> list[LogRecord]:
        async with self._uow() as uow:
            return await uow.executions.list_logs(execution_id, after_id, limit)

    async def artifact_content(self, artifact_id: UUID) -> tuple[ArtifactRecord, bytes]:
        async with self._uow() as uow:
            record = await uow.artifacts.get(artifact_id)
        if record is None:
            raise NotFoundError("artifact not found", code="artifact_not_found")
        return record, await self._store.get(record.storage_key)

    async def apply_retention(self, batch: int = 200) -> int:
        """Delete expired artifacts: blob first, then the row, so a crash leaves a retry."""
        now = self._clock.now()
        removed = 0
        async with self._uow() as uow:
            for record in await uow.artifacts.list_expired(now, batch):
                await self._store.delete(record.storage_key)
                await uow.artifacts.delete(record.id)
                removed += 1
            await uow.commit()
        return removed


def _replace_step(step: StepRecord, **changes: Any) -> StepRecord:
    return replace(step, **changes)


__all__ = ["DispatchItem", "ExecutionService", "ReconcileResult"]
