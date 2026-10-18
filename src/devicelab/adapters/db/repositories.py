"""SQLAlchemy implementations of the repository ports."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import fields
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

import structlog
from sqlalchemy import Select, and_, delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from devicelab.adapters.db import models as m
from devicelab.application import records as r
from devicelab.domain.errors import ConflictError

log = structlog.get_logger()


def to_record[R](row: Any, record_type: type[R]) -> R:
    return record_type(**{f.name: getattr(row, f.name) for f in fields(record_type)})  # type: ignore[arg-type]


def to_values(record: Any, *, skip_none_id: bool = True) -> dict[str, Any]:
    values = {f.name: getattr(record, f.name) for f in fields(record)}
    if skip_none_id and "id" in values and values["id"] is None:
        del values["id"]
    return values


class _Base:
    def __init__(self, session: AsyncSession) -> None:
        self.s = session

    async def _all(self, stmt: Select[Any]) -> list[Any]:
        result = await self.s.execute(stmt.execution_options(populate_existing=True))
        return list(result.scalars().all())

    async def _one(self, stmt: Select[Any]) -> Any | None:
        result = await self.s.execute(stmt.execution_options(populate_existing=True))
        return result.scalars().first()

    async def _insert(self, model: type[m.Base], record: Any) -> Any:
        row = model(**to_values(record))
        self.s.add(row)
        try:
            await self.s.flush()
        except IntegrityError as exc:
            await self.s.rollback()
            raise ConflictError(
                "record violates a uniqueness constraint",
                code="duplicate",
                details={"detail": str(exc.orig)},
            ) from exc
        return row

    async def _update(self, model: type[m.Base], pk: Any, changes: dict[str, Any]) -> None:
        if changes:
            await self.s.execute(update(model).where(model.id == pk).values(**changes))  # type: ignore[attr-defined]

    async def _page[R](
        self,
        model: type[m.Base],
        record: type[R],
        where: list[Any],
        order: Any,
        limit: int,
        offset: int,
    ) -> r.Page[R]:
        count = await self.s.scalar(select(func.count()).select_from(model).where(*where))
        rows = await self._all(
            select(model).where(*where).order_by(*order).limit(limit).offset(offset)
        )
        return r.Page([to_record(x, record) for x in rows], int(count or 0), limit, offset)


class HostRepo(_Base):
    async def add(self, record: r.HostRecord) -> None:
        await self._insert(m.Host, record)

    async def get(self, host_id: UUID) -> r.HostRecord | None:
        row = await self._one(select(m.Host).where(m.Host.id == host_id))
        return to_record(row, r.HostRecord) if row else None

    async def get_by_name(self, name: str) -> r.HostRecord | None:
        row = await self._one(select(m.Host).where(m.Host.name == name))
        return to_record(row, r.HostRecord) if row else None

    async def get_by_credential_hash(self, credential_hash: str) -> r.HostRecord | None:
        row = await self._one(select(m.Host).where(m.Host.credential_hash == credential_hash))
        return to_record(row, r.HostRecord) if row else None

    async def update(self, host_id: UUID, **changes: Any) -> None:
        await self._update(m.Host, host_id, changes)

    async def list_all(self) -> list[r.HostRecord]:
        rows = await self._all(select(m.Host).order_by(m.Host.site, m.Host.name))
        return [to_record(x, r.HostRecord) for x in rows]


class EnrolmentRepo(_Base):
    async def add(self, record: r.EnrolmentTokenRecord) -> None:
        await self._insert(m.EnrolmentToken, record)

    async def get_by_hash(self, token_hash: str) -> r.EnrolmentTokenRecord | None:
        row = await self._one(
            select(m.EnrolmentToken).where(m.EnrolmentToken.token_hash == token_hash)
        )
        return to_record(row, r.EnrolmentTokenRecord) if row else None

    async def consume(self, token_id: UUID, now: datetime) -> bool:
        result = await self.s.execute(
            update(m.EnrolmentToken)
            .where(
                m.EnrolmentToken.id == token_id,
                m.EnrolmentToken.used_at.is_(None),
                m.EnrolmentToken.expires_at > now,
            )
            .values(used_at=now)
            .returning(m.EnrolmentToken.id)
        )
        return result.first() is not None

    async def list_pending(self, now: datetime) -> list[r.EnrolmentTokenRecord]:
        rows = await self._all(
            select(m.EnrolmentToken).where(
                m.EnrolmentToken.used_at.is_(None), m.EnrolmentToken.expires_at > now
            )
        )
        return [to_record(x, r.EnrolmentTokenRecord) for x in rows]


class DeviceRepo(_Base):
    async def add(self, record: r.DeviceRecord) -> None:
        await self._insert(m.Device, record)

    async def get(self, device_id: UUID, *, for_update: bool = False) -> r.DeviceRecord | None:
        stmt = select(m.Device).where(m.Device.id == device_id)
        row = await self._one(stmt.with_for_update() if for_update else stmt)
        return to_record(row, r.DeviceRecord) if row else None

    async def get_by_udid(self, udid: str) -> r.DeviceRecord | None:
        row = await self._one(select(m.Device).where(m.Device.udid == udid))
        return to_record(row, r.DeviceRecord) if row else None

    async def update(self, device_id: UUID, **changes: Any) -> None:
        await self._update(m.Device, device_id, changes)

    async def search(
        self,
        *,
        state: str | None = None,
        site: str | None = None,
        host_id: UUID | None = None,
        label: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> r.Page[r.DeviceRecord]:
        where: list[Any] = []
        if state:
            where.append(m.Device.state == state)
        if site:
            where.append(m.Device.site == site)
        if host_id:
            where.append(m.Device.host_id == host_id)
        if label:
            where.append(m.Device.labels.contains([label]))
        return await self._page(
            m.Device, r.DeviceRecord, where, [m.Device.site, m.Device.name], limit, offset
        )

    async def lock_available(self) -> list[r.DeviceRecord]:
        rows = await self._all(
            select(m.Device)
            .where(m.Device.state == "available")
            .order_by(m.Device.id)
            .with_for_update(skip_locked=True)
        )
        return [to_record(x, r.DeviceRecord) for x in rows]

    async def list_for_host(self, host_id: UUID) -> list[r.DeviceRecord]:
        rows = await self._all(
            select(m.Device).where(m.Device.host_id == host_id).order_by(m.Device.id)
        )
        return [to_record(x, r.DeviceRecord) for x in rows]

    async def list_in_states(self, states: list[str]) -> list[r.DeviceRecord]:
        rows = await self._all(
            select(m.Device).where(m.Device.state.in_(states)).order_by(m.Device.id)
        )
        return [to_record(x, r.DeviceRecord) for x in rows]

    async def count_by_state(self) -> dict[str, int]:
        result = await self.s.execute(select(m.Device.state, func.count()).group_by(m.Device.state))
        return {state: int(n) for state, n in result.all()}

    async def add_sample(self, sample: r.HealthSampleRecord) -> None:
        await self._insert(m.HealthSample, sample)

    async def list_samples(
        self, device_id: UUID, since: datetime, limit: int
    ) -> list[r.HealthSampleRecord]:
        rows = await self._all(
            select(m.HealthSample)
            .where(m.HealthSample.device_id == device_id, m.HealthSample.ts >= since)
            .order_by(m.HealthSample.ts)
            .limit(limit)
        )
        return [to_record(x, r.HealthSampleRecord) for x in rows]


class TemplateRepo(_Base):
    async def add(self, record: r.TemplateRecord) -> None:
        await self._insert(m.Template, record)

    async def get(self, template_id: UUID) -> r.TemplateRecord | None:
        row = await self._one(select(m.Template).where(m.Template.id == template_id))
        return to_record(row, r.TemplateRecord) if row else None

    async def get_by_name(self, name: str) -> r.TemplateRecord | None:
        row = await self._one(select(m.Template).where(m.Template.name == name))
        return to_record(row, r.TemplateRecord) if row else None

    async def update(self, template_id: UUID, **changes: Any) -> None:
        await self._update(m.Template, template_id, changes)

    async def list_all(self) -> list[r.TemplateRecord]:
        rows = await self._all(select(m.Template).order_by(m.Template.name))
        return [to_record(x, r.TemplateRecord) for x in rows]

    async def delete(self, template_id: UUID) -> None:
        await self.s.execute(delete(m.Template).where(m.Template.id == template_id))


class JobRepo(_Base):
    async def add(self, record: r.JobRecord) -> None:
        await self._insert(m.Job, record)

    async def get(self, job_id: UUID, *, for_update: bool = False) -> r.JobRecord | None:
        stmt = select(m.Job).where(m.Job.id == job_id)
        row = await self._one(stmt.with_for_update() if for_update else stmt)
        return to_record(row, r.JobRecord) if row else None

    async def find_by_idempotency(self, submitted_by: str, key: str) -> r.JobRecord | None:
        row = await self._one(
            select(m.Job).where(m.Job.submitted_by == submitted_by, m.Job.idempotency_key == key)
        )
        return to_record(row, r.JobRecord) if row else None

    async def lock_queued(self, limit: int) -> list[r.JobRecord]:
        rows = await self._all(
            select(m.Job)
            .where(m.Job.state == "queued")
            .order_by(m.Job.priority.desc(), m.Job.queued_at, m.Job.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [to_record(x, r.JobRecord) for x in rows]

    async def update(self, job_id: UUID, **changes: Any) -> None:
        await self._update(m.Job, job_id, changes)

    async def search(
        self,
        *,
        state: str | None = None,
        template_name: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> r.Page[r.JobRecord]:
        where: list[Any] = []
        if state:
            where.append(m.Job.state == state)
        if template_name:
            where.append(m.Job.template_name == template_name)
        return await self._page(
            m.Job, r.JobRecord, where, [m.Job.created_at.desc(), m.Job.id], limit, offset
        )

    async def count_by_state(self) -> dict[str, int]:
        result = await self.s.execute(select(m.Job.state, func.count()).group_by(m.Job.state))
        return {state: int(n) for state, n in result.all()}

    async def oldest_queued_at(self) -> datetime | None:
        return await self.s.scalar(select(func.min(m.Job.queued_at)).where(m.Job.state == "queued"))


class LeaseRepo(_Base):
    async def add(self, record: r.LeaseRecord) -> None:
        await self._insert(m.Lease, record)

    async def get(self, lease_id: UUID, *, for_update: bool = False) -> r.LeaseRecord | None:
        stmt = select(m.Lease).where(m.Lease.id == lease_id)
        row = await self._one(stmt.with_for_update() if for_update else stmt)
        return to_record(row, r.LeaseRecord) if row else None

    async def update(self, lease_id: UUID, **changes: Any) -> None:
        await self._update(m.Lease, lease_id, changes)

    async def get_active_for_device(self, device_id: UUID) -> r.LeaseRecord | None:
        row = await self._one(
            select(m.Lease).where(m.Lease.device_id == device_id, m.Lease.state == "active")
        )
        return to_record(row, r.LeaseRecord) if row else None

    async def list_active(self) -> list[r.LeaseRecord]:
        rows = await self._all(select(m.Lease).where(m.Lease.state == "active"))
        return [to_record(x, r.LeaseRecord) for x in rows]

    async def list_expired(self, now: datetime, limit: int) -> list[r.LeaseRecord]:
        rows = await self._all(
            select(m.Lease)
            .where(m.Lease.state == "active", m.Lease.expires_at <= now)
            .order_by(m.Lease.expires_at)
            .limit(limit)
        )
        return [to_record(x, r.LeaseRecord) for x in rows]

    async def active_counts_by_host(self) -> dict[UUID, int]:
        result = await self.s.execute(
            select(m.Lease.host_id, func.count())
            .where(m.Lease.state == "active")
            .group_by(m.Lease.host_id)
        )
        return {host: int(n) for host, n in result.all()}

    async def count_active(self) -> int:
        return int(await self.s.scalar(select(func.count()).where(m.Lease.state == "active")) or 0)

    async def list_reclaimed_since(self, since: datetime) -> list[r.LeaseRecord]:
        rows = await self._all(
            select(m.Lease).where(m.Lease.state == "expired", m.Lease.released_at >= since)
        )
        return [to_record(x, r.LeaseRecord) for x in rows]


class ExecutionRepo(_Base):
    async def add(self, record: r.ExecutionRecord) -> None:
        await self._insert(m.Execution, record)

    async def get(
        self, execution_id: UUID, *, for_update: bool = False
    ) -> r.ExecutionRecord | None:
        stmt = select(m.Execution).where(m.Execution.id == execution_id)
        row = await self._one(stmt.with_for_update() if for_update else stmt)
        return to_record(row, r.ExecutionRecord) if row else None

    async def get_by_lease(self, lease_id: UUID) -> r.ExecutionRecord | None:
        row = await self._one(select(m.Execution).where(m.Execution.lease_id == lease_id))
        return to_record(row, r.ExecutionRecord) if row else None

    async def update(self, execution_id: UUID, **changes: Any) -> None:
        await self._update(m.Execution, execution_id, changes)

    async def search(
        self,
        *,
        job_id: UUID | None = None,
        device_id: UUID | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> r.Page[r.ExecutionRecord]:
        where: list[Any] = []
        if job_id:
            where.append(m.Execution.job_id == job_id)
        if device_id:
            where.append(m.Execution.device_id == device_id)
        if state:
            where.append(m.Execution.state == state)
        return await self._page(
            m.Execution,
            r.ExecutionRecord,
            where,
            [m.Execution.created_at.desc(), m.Execution.id],
            limit,
            offset,
        )

    async def list_for_job(self, job_id: UUID) -> list[r.ExecutionRecord]:
        rows = await self._all(
            select(m.Execution).where(m.Execution.job_id == job_id).order_by(m.Execution.created_at)
        )
        return [to_record(x, r.ExecutionRecord) for x in rows]

    async def list_for_host(self, host_id: UUID, states: list[str]) -> list[r.ExecutionRecord]:
        rows = await self._all(
            select(m.Execution)
            .where(m.Execution.host_id == host_id, m.Execution.state.in_(states))
            .order_by(m.Execution.created_at)
        )
        return [to_record(x, r.ExecutionRecord) for x in rows]

    async def upsert_step(self, record: r.StepRecord) -> None:
        values = to_values(record)
        stmt = text(
            """
            INSERT INTO execution_steps (id, execution_id, index, step_id, type, name, state,
              attempts, started_at, finished_at, duration_ms, failure_class, error, output)
            VALUES (:id, :execution_id, :index, :step_id, :type, :name, :state, :attempts,
              :started_at, :finished_at, :duration_ms, :failure_class, :error,
              CAST(:output AS jsonb))
            ON CONFLICT (execution_id, index) DO UPDATE SET
              state = EXCLUDED.state, attempts = EXCLUDED.attempts,
              started_at = EXCLUDED.started_at, finished_at = EXCLUDED.finished_at,
              duration_ms = EXCLUDED.duration_ms, failure_class = EXCLUDED.failure_class,
              error = EXCLUDED.error, output = EXCLUDED.output
            """
        )
        values["output"] = json.dumps(values["output"])
        await self.s.execute(stmt, values)

    async def get_step(self, execution_id: UUID, index: int) -> r.StepRecord | None:
        row = await self._one(
            select(m.ExecutionStep).where(
                m.ExecutionStep.execution_id == execution_id, m.ExecutionStep.index == index
            )
        )
        return to_record(row, r.StepRecord) if row else None

    async def list_steps(self, execution_id: UUID) -> list[r.StepRecord]:
        rows = await self._all(
            select(m.ExecutionStep)
            .where(m.ExecutionStep.execution_id == execution_id)
            .order_by(m.ExecutionStep.index)
        )
        return [to_record(x, r.StepRecord) for x in rows]

    async def add_log(self, record: r.LogRecord) -> int:
        row = await self._insert(m.ExecutionLog, record)
        return int(row.id)

    async def list_logs(self, execution_id: UUID, after_id: int, limit: int) -> list[r.LogRecord]:
        rows = await self._all(
            select(m.ExecutionLog)
            .where(m.ExecutionLog.execution_id == execution_id, m.ExecutionLog.id > after_id)
            .order_by(m.ExecutionLog.id)
            .limit(limit)
        )
        return [to_record(x, r.LogRecord) for x in rows]


class ArtifactRepo(_Base):
    async def add(self, record: r.ArtifactRecord) -> None:
        await self._insert(m.Artifact, record)

    async def get(self, artifact_id: UUID) -> r.ArtifactRecord | None:
        row = await self._one(select(m.Artifact).where(m.Artifact.id == artifact_id))
        return to_record(row, r.ArtifactRecord) if row else None

    async def list_for_execution(self, execution_id: UUID) -> list[r.ArtifactRecord]:
        rows = await self._all(
            select(m.Artifact)
            .where(m.Artifact.execution_id == execution_id)
            .order_by(m.Artifact.created_at)
        )
        return [to_record(x, r.ArtifactRecord) for x in rows]

    async def list_expired(self, now: datetime, limit: int) -> list[r.ArtifactRecord]:
        rows = await self._all(select(m.Artifact).where(m.Artifact.expires_at <= now).limit(limit))
        return [to_record(x, r.ArtifactRecord) for x in rows]

    async def delete(self, artifact_id: UUID) -> None:
        await self.s.execute(delete(m.Artifact).where(m.Artifact.id == artifact_id))


class ScheduleRepo(_Base):
    async def add(self, record: r.ScheduleRecord) -> None:
        await self._insert(m.Schedule, record)

    async def get(self, schedule_id: UUID) -> r.ScheduleRecord | None:
        row = await self._one(select(m.Schedule).where(m.Schedule.id == schedule_id))
        return to_record(row, r.ScheduleRecord) if row else None

    async def update(self, schedule_id: UUID, **changes: Any) -> None:
        await self._update(m.Schedule, schedule_id, changes)

    async def delete(self, schedule_id: UUID) -> None:
        await self.s.execute(delete(m.Schedule).where(m.Schedule.id == schedule_id))

    async def list_all(self) -> list[r.ScheduleRecord]:
        rows = await self._all(select(m.Schedule).order_by(m.Schedule.name))
        return [to_record(x, r.ScheduleRecord) for x in rows]

    async def lock_due(self, now: datetime) -> list[r.ScheduleRecord]:
        rows = await self._all(
            select(m.Schedule)
            .where(m.Schedule.enabled.is_(True), m.Schedule.next_fire_at <= now)
            .order_by(m.Schedule.next_fire_at)
            .with_for_update(skip_locked=True)
        )
        return [to_record(x, r.ScheduleRecord) for x in rows]


class AlertRepo(_Base):
    async def add_rule(self, record: r.AlertRuleRecord) -> None:
        await self._insert(m.AlertRule, record)

    async def get_rule(self, rule_id: UUID) -> r.AlertRuleRecord | None:
        row = await self._one(select(m.AlertRule).where(m.AlertRule.id == rule_id))
        return to_record(row, r.AlertRuleRecord) if row else None

    async def update_rule(self, rule_id: UUID, **changes: Any) -> None:
        await self._update(m.AlertRule, rule_id, changes)

    async def list_rules(self) -> list[r.AlertRuleRecord]:
        rows = await self._all(
            select(m.AlertRule).order_by(m.AlertRule.created_at, m.AlertRule.name)
        )
        return [to_record(x, r.AlertRuleRecord) for x in rows]

    async def add_alert(self, record: r.AlertRecord) -> None:
        await self._insert(m.Alert, record)

    async def get_alert(self, alert_id: UUID) -> r.AlertRecord | None:
        row = await self._one(select(m.Alert).where(m.Alert.id == alert_id))
        return to_record(row, r.AlertRecord) if row else None

    async def get_open_by_key(self, dedupe_key: str) -> r.AlertRecord | None:
        row = await self._one(
            select(m.Alert).where(m.Alert.dedupe_key == dedupe_key, m.Alert.state == "open")
        )
        return to_record(row, r.AlertRecord) if row else None

    async def update_alert(self, alert_id: UUID, **changes: Any) -> None:
        await self._update(m.Alert, alert_id, changes)

    async def list_alerts(
        self, *, state: str | None = None, limit: int = 50, offset: int = 0
    ) -> r.Page[r.AlertRecord]:
        where: list[Any] = [m.Alert.state == state] if state else []
        return await self._page(
            m.Alert, r.AlertRecord, where, [m.Alert.last_seen.desc()], limit, offset
        )

    async def list_open(self) -> list[r.AlertRecord]:
        rows = await self._all(select(m.Alert).where(m.Alert.state == "open"))
        return [to_record(x, r.AlertRecord) for x in rows]

    async def add_webhook(self, record: r.WebhookRecord) -> None:
        await self._insert(m.Webhook, record)

    async def get_webhook(self, webhook_id: UUID) -> r.WebhookRecord | None:
        row = await self._one(select(m.Webhook).where(m.Webhook.id == webhook_id))
        return to_record(row, r.WebhookRecord) if row else None

    async def list_webhooks(self) -> list[r.WebhookRecord]:
        rows = await self._all(select(m.Webhook).order_by(m.Webhook.created_at))
        return [to_record(x, r.WebhookRecord) for x in rows]

    async def delete_webhook(self, webhook_id: UUID) -> None:
        await self.s.execute(delete(m.Webhook).where(m.Webhook.id == webhook_id))


class ApiKeyRepo(_Base):
    async def add(self, record: r.ApiKeyRecord) -> None:
        await self._insert(m.ApiKey, record)

    async def get(self, key_id: UUID) -> r.ApiKeyRecord | None:
        row = await self._one(select(m.ApiKey).where(m.ApiKey.id == key_id))
        return to_record(row, r.ApiKeyRecord) if row else None

    async def get_by_hash(self, key_hash: str) -> r.ApiKeyRecord | None:
        row = await self._one(select(m.ApiKey).where(m.ApiKey.key_hash == key_hash))
        return to_record(row, r.ApiKeyRecord) if row else None

    async def update(self, key_id: UUID, **changes: Any) -> None:
        await self._update(m.ApiKey, key_id, changes)

    async def list_all(self) -> list[r.ApiKeyRecord]:
        rows = await self._all(select(m.ApiKey).order_by(m.ApiKey.created_at))
        return [to_record(x, r.ApiKeyRecord) for x in rows]

    async def count(self) -> int:
        return int(await self.s.scalar(select(func.count()).select_from(m.ApiKey)) or 0)


class AuditRepo(_Base):
    async def add(self, record: r.AuditRecord) -> None:
        await self._insert(m.AuditLog, record)

    async def search(
        self,
        *,
        action: str | None = None,
        actor: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> r.Page[r.AuditRecord]:
        where: list[Any] = []
        if action:
            where.append(m.AuditLog.action == action)
        if actor:
            where.append(m.AuditLog.actor == actor)
        return await self._page(
            m.AuditLog, r.AuditRecord, where, [m.AuditLog.id.desc()], limit, offset
        )


class StatsRepo(_Base):
    _FINISHED = ("succeeded", "failed", "lost")

    async def finished_counts(self, since: datetime) -> tuple[int, int]:
        row = (
            await self.s.execute(
                select(
                    func.count(),
                    func.count().filter(m.Execution.state.in_(("failed", "lost"))),
                ).where(m.Execution.finished_at >= since, m.Execution.state.in_(self._FINISHED))
            )
        ).one()
        return int(row[0]), int(row[1])

    async def queue_waits(self, since: datetime, until: datetime) -> list[float]:
        result = await self.s.execute(
            select(m.Job.queue_wait_s).where(
                m.Job.created_at >= since, m.Job.created_at < until, m.Job.queue_wait_s.is_not(None)
            )
        )
        return [float(x) for (x,) in result.all() if x is not None]

    async def execution_rows(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        duration = func.extract("epoch", m.Execution.finished_at - m.Execution.started_at)
        result = await self.s.execute(
            select(
                m.Execution.id.label("execution_id"),
                m.Execution.job_id,
                m.Job.template_name.label("template"),
                m.Device.name.label("device"),
                m.Device.site,
                m.Execution.attempt,
                m.Execution.state,
                m.Execution.failure_class,
                m.Execution.started_at,
                m.Execution.finished_at,
                duration.label("duration_s"),
                m.Job.queue_wait_s,
            )
            .join(m.Job, m.Job.id == m.Execution.job_id)
            .join(m.Device, m.Device.id == m.Execution.device_id)
            .where(and_(m.Execution.created_at >= start, m.Execution.created_at < end))
            .order_by(m.Execution.created_at)
        )
        rows = [dict(row._mapping) for row in result.all()]
        for row in rows:
            row["execution_id"] = str(row["execution_id"])
            row["job_id"] = str(row["job_id"])
            if row["duration_s"] is not None:
                row["duration_s"] = round(float(row["duration_s"]), 3)
        return rows

    async def failure_classes(self, start: datetime, end: datetime) -> dict[str, int]:
        result = await self.s.execute(
            select(m.Execution.failure_class, func.count())
            .where(
                m.Execution.created_at >= start,
                m.Execution.created_at < end,
                m.Execution.failure_class.is_not(None),
            )
            .group_by(m.Execution.failure_class)
        )
        return {str(k): int(n) for k, n in result.all()}


class SqlUnitOfWork:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory
        self._callbacks: list[Callable[[], None]] = []

    async def __aenter__(self) -> Self:
        s = self._factory()
        self.session = s
        self.hosts = HostRepo(s)
        self.enrolments = EnrolmentRepo(s)
        self.devices = DeviceRepo(s)
        self.templates = TemplateRepo(s)
        self.jobs = JobRepo(s)
        self.leases = LeaseRepo(s)
        self.executions = ExecutionRepo(s)
        self.artifacts = ArtifactRepo(s)
        self.schedules = ScheduleRepo(s)
        self.alerts = AlertRepo(s)
        self.api_keys = ApiKeyRepo(s)
        self.audit = AuditRepo(s)
        self.stats = StatsRepo(s)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            await self.session.rollback()
        finally:
            await self.session.close()
            self._callbacks.clear()

    def on_commit(self, callback: Callable[[], None]) -> None:
        self._callbacks.append(callback)

    async def commit(self) -> None:
        await self.session.commit()
        callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                log.exception("post_commit_callback_failed")

    async def rollback(self) -> None:
        await self.session.rollback()
        self._callbacks.clear()


__all__ = ["SqlUnitOfWork"]
