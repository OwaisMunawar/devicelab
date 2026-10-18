"""SQLAlchemy table definitions. Column names match the application records one-to-one,
which is what lets the repositories map rows generically."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TS = DateTime(timezone=True)
UUID_T = PGUUID(as_uuid=True)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[dict[str, Any]]: JSONB}


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(UUID_T, primary_key=True)


class Host(Base):
    __tablename__ = "hosts"
    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64), unique=True)
    site: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64))
    max_concurrency: Mapped[int] = mapped_column(Integer)
    credential_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(TS)
    agent_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(TS)


class EnrolmentToken(Base):
    __tablename__ = "enrolment_tokens"
    id: Mapped[UUID] = _uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    host_name: Mapped[str] = mapped_column(String(64))
    site: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64))
    max_concurrency: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(TS)
    used_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(128))


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[UUID] = _uuid_pk()
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id"), index=True)
    udid: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    model: Mapped[str] = mapped_column(String(64))
    model_family: Mapped[str] = mapped_column(String(32))
    ios_version: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))
    site: Mapped[str] = mapped_column(String(64), index=True)
    region: Mapped[str] = mapped_column(String(64))
    labels: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    state: Mapped[str] = mapped_column(String(16), index=True)
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    storage_free_gb: Mapped[float | None] = mapped_column(Float)
    thermal: Mapped[str] = mapped_column(String(32), default="nominal")
    connectivity: Mapped[str] = mapped_column(String(32), default="connected")
    last_seen: Mapped[datetime | None] = mapped_column(TS)
    consecutive_infra_failures: Mapped[int] = mapped_column(Integer, default=0)
    fence_counter: Mapped[int] = mapped_column(BigInteger, default=0)
    quarantine_reason: Mapped[str | None] = mapped_column(Text)
    state_changed_at: Mapped[datetime | None] = mapped_column(TS)
    last_sample_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = mapped_column(TS)


class HealthSample(Base):
    __tablename__ = "device_health_samples"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(TS)
    state: Mapped[str] = mapped_column(String(16))
    thermal: Mapped[str] = mapped_column(String(32))
    connectivity: Mapped[str] = mapped_column(String(32))
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_health_device_ts", "device_id", "ts"),)


class Template(Base):
    __tablename__ = "workflow_templates"
    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64), unique=True)
    version: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(TS)
    updated_at: Mapped[datetime] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(128))


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[UUID] = _uuid_pk()
    template_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("workflow_templates.id", ondelete="SET NULL")
    )
    template_name: Mapped[str] = mapped_column(String(64))
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requirement: Mapped[dict[str, Any]] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    excluded_device_ids: Mapped[list[UUID]] = mapped_column(ARRAY(UUID_T), default=list)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    submitted_by: Mapped[str] = mapped_column(String(128))
    schedule_id: Mapped[UUID | None] = mapped_column(UUID_T)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    queue_wait_s: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(TS)
    queued_at: Mapped[datetime] = mapped_column(TS)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    failure_class: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (
        UniqueConstraint("submitted_by", "idempotency_key", name="uq_job_idempotency"),
        Index("ix_jobs_queue", "state", "priority", "queued_at"),
        Index("ix_jobs_created", "created_at"),
    )


class Lease(Base):
    __tablename__ = "leases"
    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id"))
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(16))
    acquired_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    released_at: Mapped[datetime | None] = mapped_column(TS)
    release_reason: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        # The database-level guarantee behind "no double assignment".
        Index(
            "uq_active_lease_per_device",
            "device_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_lease_expiry", "expires_at", postgresql_where=text("state = 'active'")),
    )


class Execution(Base):
    __tablename__ = "executions"
    id: Mapped[UUID] = _uuid_pk()
    job_id: Mapped[UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[UUID] = mapped_column(ForeignKey("devices.id"), index=True)
    host_id: Mapped[UUID] = mapped_column(ForeignKey("hosts.id"))
    lease_id: Mapped[UUID] = mapped_column(ForeignKey("leases.id"), unique=True)
    attempt: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16))
    fencing_token: Mapped[int] = mapped_column(BigInteger)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(TS)
    dispatched_at: Mapped[datetime | None] = mapped_column(TS)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    failure_class: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    session_restarts: Mapped[int] = mapped_column(Integer, default=0)
    reboots: Mapped[int] = mapped_column(Integer, default=0)
    __table_args__ = (
        Index("ix_exec_host_state", "host_id", "state"),
        Index("ix_exec_finished", "finished_at"),
    )


class ExecutionStep(Base):
    __tablename__ = "execution_steps"
    id: Mapped[UUID] = _uuid_pk()
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    step_id: Mapped[str] = mapped_column(String(64))
    type: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(TS)
    finished_at: Mapped[datetime | None] = mapped_column(TS)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    failure_class: Mapped[str | None] = mapped_column(String(32))
    error: Mapped[str | None] = mapped_column(Text)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    __table_args__ = (UniqueConstraint("execution_id", "index", name="uq_step_index"),)


class ExecutionLog(Base):
    __tablename__ = "execution_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(TS)
    level: Mapped[str] = mapped_column(String(8))
    message: Mapped[str] = mapped_column(Text)
    step_index: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_logs_execution", "execution_id", "id"),)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[UUID] = _uuid_pk()
    execution_id: Mapped[UUID] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(300))
    step_index: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)
    expires_at: Mapped[datetime | None] = mapped_column(TS, index=True)


class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64), unique=True)
    template_name: Mapped[str] = mapped_column(String(64))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    requirement: Mapped[dict[str, Any]] = mapped_column(JSONB)
    priority: Mapped[int] = mapped_column(Integer)
    cron: Mapped[str] = mapped_column(String(64))
    timezone: Mapped[str] = mapped_column(String(64))
    misfire_policy: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean)
    checked_through: Mapped[datetime] = mapped_column(TS)
    next_fire_at: Mapped[datetime | None] = mapped_column(TS)
    last_fire_at: Mapped[datetime | None] = mapped_column(TS)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(TS)


class AlertRule(Base):
    __tablename__ = "alert_rules"
    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean)
    cooldown_s: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TS)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[UUID] = _uuid_pk()
    rule_id: Mapped[UUID | None] = mapped_column(UUID_T)
    kind: Mapped[str] = mapped_column(String(32))
    severity: Mapped[str] = mapped_column(String(16))
    dedupe_key: Mapped[str] = mapped_column(String(160))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    state: Mapped[str] = mapped_column(String(16))
    occurrences: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(TS)
    last_seen: Mapped[datetime] = mapped_column(TS)
    last_notified_at: Mapped[datetime | None] = mapped_column(TS)
    resolved_at: Mapped[datetime | None] = mapped_column(TS)
    __table_args__ = (
        # At most one open alert per condition; repeats bump `occurrences` instead.
        Index(
            "uq_open_alert_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("state = 'open'"),
        ),
    )


class Webhook(Base):
    __tablename__ = "webhooks"
    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(128))
    enabled: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(TS)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(TS)
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(TS, index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64), index=True)
    target_type: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    correlation_id: Mapped[str | None] = mapped_column(String(64))
