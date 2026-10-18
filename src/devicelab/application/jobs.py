"""Workflow templates and job submission."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from devicelab.application.config import ControlPlaneConfig
from devicelab.application.lifecycle import Lifecycle
from devicelab.application.ports import Clock, EventPublisher, IdFactory, UowFactory
from devicelab.application.records import JobRecord, Page, TemplateRecord
from devicelab.application.support import Actor, audit, emit
from devicelab.domain.errors import ConflictError, NotFoundError, ValidationFailedError
from devicelab.domain.models import JobState
from devicelab.domain.state_machines import transition_job
from devicelab.domain.templating import render_steps
from devicelab.domain.workflow import (
    STEP_TYPES,
    RequirementSpec,
    WorkflowTemplate,
    parse_template,
)

# Placeholder values used to prove a template renders before it is queued.
_PROBE_DEVICE = {
    "udid": "probe",
    "name": "probe",
    "model": "probe",
    "ios_version": "0",
    "site": "probe",
}


@dataclass(frozen=True, slots=True)
class TemplateReport:
    valid: bool
    name: str | None = None
    step_count: int = 0
    step_types: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


class TemplateService:
    def __init__(self, uow_factory: UowFactory, clock: Clock, ids: IdFactory) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids

    @staticmethod
    def validate(text: str) -> TemplateReport:
        """Never raises for bad input: the editor wants every problem, not an exception."""
        try:
            template = parse_template(text)
        except ValidationFailedError as exc:
            problems = exc.details.get("problems") or [{"path": "", "message": exc.message}]
            return TemplateReport(valid=False, errors=problems)
        return TemplateReport(
            valid=True,
            name=template.name,
            step_count=len(template.steps),
            step_types=[s.type for s in template.steps],
        )

    async def create(self, actor: Actor, text: str) -> TemplateRecord:
        template = parse_template(text)
        now = self._clock.now()
        record = TemplateRecord(
            id=self._ids(),
            name=template.name,
            version=1,
            description=template.description,
            spec=template.model_dump(mode="json"),
            created_at=now,
            updated_at=now,
            created_by=actor.name,
        )
        async with self._uow() as uow:
            if await uow.templates.get_by_name(template.name):
                raise ConflictError("template name already exists", code="template_exists")
            await uow.templates.add(record)
            await audit(
                uow, actor, "template.created", "template", record.id, now, name=record.name
            )
            await uow.commit()
        return record

    async def update(self, actor: Actor, name: str, text: str) -> TemplateRecord:
        template = parse_template(text)
        if template.name != name:
            raise ValidationFailedError("template name cannot change", code="template_rename")
        now = self._clock.now()
        async with self._uow() as uow:
            current = await uow.templates.get_by_name(name)
            if current is None:
                raise NotFoundError("template not found", code="template_not_found")
            await uow.templates.update(
                current.id,
                version=current.version + 1,
                description=template.description,
                spec=template.model_dump(mode="json"),
                updated_at=now,
            )
            await audit(
                uow,
                actor,
                "template.updated",
                "template",
                current.id,
                now,
                version=current.version + 1,
            )
            await uow.commit()
            updated = await uow.templates.get(current.id)
        assert updated is not None
        return updated

    async def delete(self, actor: Actor, name: str) -> None:
        async with self._uow() as uow:
            current = await uow.templates.get_by_name(name)
            if current is None:
                raise NotFoundError("template not found", code="template_not_found")
            await uow.templates.delete(current.id)
            await audit(uow, actor, "template.deleted", "template", current.id, self._clock.now())
            await uow.commit()

    async def get(self, name: str) -> TemplateRecord:
        async with self._uow() as uow:
            record = await uow.templates.get_by_name(name)
        if record is None:
            raise NotFoundError("template not found", code="template_not_found")
        return record

    async def list(self) -> list[TemplateRecord]:
        async with self._uow() as uow:
            return await uow.templates.list_all()

    @staticmethod
    def step_types() -> tuple[str, ...]:
        return STEP_TYPES


@dataclass(frozen=True, slots=True)
class SubmitJob:
    template: str
    params: dict[str, Any] = field(default_factory=dict)
    priority: int = 0
    requirement: dict[str, Any] | None = None
    idempotency_key: str | None = None
    max_attempts: int | None = None
    correlation_id: str | None = None
    schedule_id: UUID | None = None


class JobService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        ids: IdFactory,
        config: ControlPlaneConfig,
        lifecycle: Lifecycle,
        events: EventPublisher,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids
        self._cfg = config
        self._life = lifecycle
        self._events = events

    async def submit(self, actor: Actor, cmd: SubmitJob) -> tuple[JobRecord, bool]:
        """Returns (job, created). A repeated idempotency key returns the original job."""
        if not 0 <= cmd.priority <= 100:
            raise ValidationFailedError("priority must be 0..100", code="invalid_priority")
        max_attempts = (
            cmd.max_attempts if cmd.max_attempts is not None else self._cfg.default_max_attempts
        )
        if not 1 <= max_attempts <= 10:
            raise ValidationFailedError("max_attempts must be 1..10", code="invalid_attempts")
        now = self._clock.now()

        async with self._uow() as uow:
            if cmd.idempotency_key:
                existing = await uow.jobs.find_by_idempotency(actor.name, cmd.idempotency_key)
                if existing is not None:
                    return existing, False
            template_record = await uow.templates.get_by_name(cmd.template)
            if template_record is None:
                raise NotFoundError("template not found", code="template_not_found")
            template = WorkflowTemplate.model_validate(template_record.spec)
            render_steps(template, cmd.params, _PROBE_DEVICE)

            merged = {**template.requirements.model_dump(), **(cmd.requirement or {})}
            requirement = RequirementSpec.model_validate(merged).model_dump()

            job = JobRecord(
                id=self._ids(),
                template_id=template_record.id,
                template_name=template.name,
                spec=template_record.spec,
                params=dict(cmd.params),
                requirement=requirement,
                priority=cmd.priority,
                state=JobState.QUEUED.value,
                attempts=0,
                max_attempts=max_attempts,
                submitted_by=actor.name,
                created_at=now,
                queued_at=now,
                idempotency_key=cmd.idempotency_key,
                schedule_id=cmd.schedule_id,
                correlation_id=cmd.correlation_id or actor.correlation_id,
            )
            try:
                await uow.jobs.add(job)
            except ConflictError:
                await uow.rollback()
                async with self._uow() as again:
                    assert cmd.idempotency_key is not None
                    winner = await again.jobs.find_by_idempotency(actor.name, cmd.idempotency_key)
                if winner is None:
                    raise
                return winner, False
            await audit(
                uow,
                actor,
                "job.submitted",
                "job",
                job.id,
                now,
                template=template.name,
                priority=cmd.priority,
            )
            emit(uow, self._events, "job.updated", id=str(job.id), state="queued")
            await uow.commit()
        return job, True

    async def cancel(self, actor: Actor, job_id: UUID) -> JobRecord:
        now = self._clock.now()
        async with self._uow() as uow:
            job = await uow.jobs.get(job_id, for_update=True)
            if job is None:
                raise NotFoundError("job not found", code="job_not_found")
            transition_job(JobState(job.state), JobState.CANCELLED)
            if job.state != JobState.CANCELLED.value:
                await self._life.cancel_job_executions(uow, job, "job cancelled")
                await uow.jobs.update(job.id, state="cancelled", finished_at=now)
                await audit(uow, actor, "job.cancelled", "job", job.id, now)
                emit(uow, self._events, "job.updated", id=str(job.id), state="cancelled")
            await uow.commit()
            cancelled = await uow.jobs.get(job_id)
        assert cancelled is not None
        return cancelled

    async def get(self, job_id: UUID) -> JobRecord:
        async with self._uow() as uow:
            job = await uow.jobs.get(job_id)
        if job is None:
            raise NotFoundError("job not found", code="job_not_found")
        return job

    async def search(
        self,
        *,
        state: str | None = None,
        template_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[JobRecord]:
        async with self._uow() as uow:
            return await uow.jobs.search(
                state=state, template_name=template_name, limit=limit, offset=offset
            )

    async def counts(self) -> dict[str, int]:
        async with self._uow() as uow:
            return await uow.jobs.count_by_state()
