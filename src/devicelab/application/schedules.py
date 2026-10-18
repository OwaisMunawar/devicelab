from __future__ import annotations

from datetime import UTC
from typing import Any
from uuid import UUID

from devicelab.application.jobs import JobService, SubmitJob
from devicelab.application.ports import Clock, IdFactory, UowFactory
from devicelab.application.records import ScheduleRecord
from devicelab.application.support import SYSTEM, Actor, audit
from devicelab.domain.errors import NotFoundError, ValidationFailedError
from devicelab.domain.schedules import MisfirePolicy, due_fire, next_fire, validate_cron


class ScheduleService:
    def __init__(
        self, uow_factory: UowFactory, clock: Clock, ids: IdFactory, jobs: JobService
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids
        self._jobs = jobs

    async def create(
        self,
        actor: Actor,
        *,
        name: str,
        template_name: str,
        cron: str,
        timezone: str = "UTC",
        params: dict[str, Any] | None = None,
        requirement: dict[str, Any] | None = None,
        priority: int = 0,
        misfire_policy: str = "skip",
        enabled: bool = True,
    ) -> ScheduleRecord:
        validate_cron(cron, timezone)
        try:
            MisfirePolicy(misfire_policy)
        except ValueError:
            raise ValidationFailedError(
                "misfire_policy must be skip or run_once", code="invalid_misfire"
            ) from None
        now = self._clock.now()
        async with self._uow() as uow:
            if await uow.templates.get_by_name(template_name) is None:
                raise NotFoundError("template not found", code="template_not_found")
            record = ScheduleRecord(
                id=self._ids(),
                name=name,
                template_name=template_name,
                params=params or {},
                requirement=requirement or {},
                priority=priority,
                cron=cron,
                timezone=timezone,
                misfire_policy=misfire_policy,
                enabled=enabled,
                created_at=now,
                checked_through=now,
                next_fire_at=next_fire(cron, timezone, now),
                created_by=actor.name,
            )
            await uow.schedules.add(record)
            await audit(uow, actor, "schedule.created", "schedule", record.id, now, cron=cron)
            await uow.commit()
        return record

    async def set_enabled(self, actor: Actor, schedule_id: UUID, enabled: bool) -> ScheduleRecord:
        now = self._clock.now()
        async with self._uow() as uow:
            schedule = await uow.schedules.get(schedule_id)
            if schedule is None:
                raise NotFoundError("schedule not found", code="schedule_not_found")
            await uow.schedules.update(
                schedule_id,
                enabled=enabled,
                # Re-enabling must not replay everything missed while it was off.
                checked_through=now,
                next_fire_at=next_fire(schedule.cron, schedule.timezone, now) if enabled else None,
            )
            await audit(
                uow, actor, "schedule.toggled", "schedule", schedule_id, now, enabled=enabled
            )
            await uow.commit()
            updated = await uow.schedules.get(schedule_id)
        assert updated is not None
        return updated

    async def delete(self, actor: Actor, schedule_id: UUID) -> None:
        async with self._uow() as uow:
            if await uow.schedules.get(schedule_id) is None:
                raise NotFoundError("schedule not found", code="schedule_not_found")
            await uow.schedules.delete(schedule_id)
            await audit(uow, actor, "schedule.deleted", "schedule", schedule_id, self._clock.now())
            await uow.commit()

    async def list(self) -> list[ScheduleRecord]:
        async with self._uow() as uow:
            return await uow.schedules.list_all()

    async def tick(self) -> int:
        """Enqueue due schedules. The job's idempotency key is the schedule and fire time,
        so a crash between enqueue and bookkeeping re-runs harmlessly."""
        now = self._clock.now()
        fired = 0
        async with self._uow() as uow:
            for schedule in await uow.schedules.lock_due(now):
                fire = due_fire(
                    schedule.cron,
                    schedule.timezone,
                    checked_through=schedule.checked_through,
                    now=now,
                    policy=MisfirePolicy(schedule.misfire_policy),
                )
                changes: dict[str, Any] = {
                    "checked_through": now,
                    "next_fire_at": next_fire(schedule.cron, schedule.timezone, now),
                }
                if fire is not None:
                    actor = Actor(f"schedule:{schedule.name}", SYSTEM.role)
                    await self._jobs.submit(
                        actor,
                        SubmitJob(
                            template=schedule.template_name,
                            params=schedule.params,
                            priority=schedule.priority,
                            requirement=schedule.requirement or None,
                            idempotency_key=f"{schedule.id}:{fire.astimezone(UTC).isoformat()}",
                            schedule_id=schedule.id,
                        ),
                    )
                    changes["last_fire_at"] = fire
                    fired += 1
                await uow.schedules.update(schedule.id, **changes)
            await uow.commit()
        return fired
