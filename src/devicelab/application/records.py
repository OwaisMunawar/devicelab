"""Persistence-agnostic records returned by repositories.

Plain frozen dataclasses with the same field names as the stored columns, so the SQL
adapter maps them generically and the use cases never see an ORM object.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class HostRecord:
    id: UUID
    name: str
    site: str
    region: str
    max_concurrency: int
    created_at: datetime
    credential_hash: str | None = None
    connected: bool = False
    last_seen: datetime | None = None
    agent_version: str | None = None


@dataclass(frozen=True, slots=True)
class EnrolmentTokenRecord:
    id: UUID
    token_hash: str
    host_name: str
    site: str
    region: str
    max_concurrency: int
    expires_at: datetime
    created_at: datetime
    created_by: str
    used_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    id: UUID
    host_id: UUID
    udid: str
    name: str
    model: str
    model_family: str
    ios_version: str
    kind: str
    site: str
    region: str
    state: str
    created_at: datetime
    labels: list[str] = field(default_factory=list)
    battery_pct: int | None = None
    storage_free_gb: float | None = None
    thermal: str = "nominal"
    connectivity: str = "connected"
    last_seen: datetime | None = None
    consecutive_infra_failures: int = 0
    fence_counter: int = 0
    quarantine_reason: str | None = None
    state_changed_at: datetime | None = None
    last_sample_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class HealthSampleRecord:
    device_id: UUID
    ts: datetime
    state: str
    thermal: str
    connectivity: str
    battery_pct: int | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class TemplateRecord:
    id: UUID
    name: str
    version: int
    description: str
    spec: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    created_by: str


@dataclass(frozen=True, slots=True)
class JobRecord:
    id: UUID
    template_id: UUID | None
    template_name: str
    spec: dict[str, Any]
    params: dict[str, Any]
    requirement: dict[str, Any]
    priority: int
    state: str
    attempts: int
    max_attempts: int
    submitted_by: str
    created_at: datetime
    queued_at: datetime
    excluded_device_ids: list[UUID] = field(default_factory=list)
    idempotency_key: str | None = None
    schedule_id: UUID | None = None
    correlation_id: str | None = None
    queue_wait_s: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_class: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class LeaseRecord:
    id: UUID
    job_id: UUID
    device_id: UUID
    host_id: UUID
    fencing_token: int
    state: str
    acquired_at: datetime
    expires_at: datetime
    released_at: datetime | None = None
    release_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    id: UUID
    job_id: UUID
    device_id: UUID
    host_id: UUID
    lease_id: UUID
    attempt: int
    state: str
    fencing_token: int
    created_at: datetime
    steps: list[dict[str, Any]] = field(default_factory=list)
    dispatched_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    failure_class: str | None = None
    error: str | None = None
    session_restarts: int = 0
    reboots: int = 0


@dataclass(frozen=True, slots=True)
class StepRecord:
    id: UUID
    execution_id: UUID
    index: int
    step_id: str
    type: str
    name: str
    state: str = "pending"
    attempts: int = 0
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    failure_class: str | None = None
    error: str | None = None
    output: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LogRecord:
    execution_id: UUID
    ts: datetime
    level: str
    message: str
    step_index: int | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: UUID
    execution_id: UUID
    name: str
    kind: str
    content_type: str
    size_bytes: int
    storage_key: str
    created_at: datetime
    expires_at: datetime | None = None
    step_index: int | None = None


@dataclass(frozen=True, slots=True)
class ScheduleRecord:
    id: UUID
    name: str
    template_name: str
    params: dict[str, Any]
    requirement: dict[str, Any]
    priority: int
    cron: str
    timezone: str
    misfire_policy: str
    enabled: bool
    created_at: datetime
    checked_through: datetime
    next_fire_at: datetime | None = None
    last_fire_at: datetime | None = None
    created_by: str = "system"


@dataclass(frozen=True, slots=True)
class AlertRuleRecord:
    id: UUID
    name: str
    kind: str
    params: dict[str, Any]
    enabled: bool
    cooldown_s: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AlertRecord:
    id: UUID
    rule_id: UUID | None
    kind: str
    severity: str
    dedupe_key: str
    message: str
    payload: dict[str, Any]
    state: str
    first_seen: datetime
    last_seen: datetime
    occurrences: int = 1
    last_notified_at: datetime | None = None
    resolved_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WebhookRecord:
    id: UUID
    name: str
    url: str
    secret: str
    enabled: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ApiKeyRecord:
    id: UUID
    name: str
    prefix: str
    key_hash: str
    role: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AuditRecord:
    ts: datetime
    actor: str
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    id: int | None = None


@dataclass(frozen=True, slots=True)
class Page[T]:
    items: list[T]
    total: int
    limit: int
    offset: int
