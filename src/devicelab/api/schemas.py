"""Wire models for the REST API. Records are never serialised directly, so a new column
cannot leak (credential hashes, webhook secrets) by accident."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from devicelab.application.records import Page


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PageOut[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def of[R](cls, page: Page[R], model: type[Out]) -> PageOut[Any]:
        return PageOut(
            items=[model.model_validate(i) for i in page.items],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = None


class ErrorEnvelope(BaseModel):
    error: ErrorBody


class HostOut(Out):
    id: UUID
    name: str
    site: str
    region: str
    max_concurrency: int
    connected: bool
    last_seen: datetime | None
    agent_version: str | None


class DeviceOut(Out):
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
    labels: list[str]
    state: str
    battery_pct: int | None
    storage_free_gb: float | None
    thermal: str
    connectivity: str
    last_seen: datetime | None
    consecutive_infra_failures: int
    quarantine_reason: str | None
    state_changed_at: datetime | None


class HealthSampleOut(Out):
    ts: datetime
    state: str
    thermal: str
    connectivity: str
    battery_pct: int | None


class TemplateOut(Out):
    id: UUID
    name: str
    version: int
    description: str
    spec: dict[str, Any]
    updated_at: datetime
    created_by: str


class TemplateBody(BaseModel):
    content: str = Field(min_length=1, max_length=200_000, description="YAML or JSON template")


class TemplateReportOut(BaseModel):
    valid: bool
    name: str | None = None
    step_count: int = 0
    step_types: list[str] = Field(default_factory=list)
    errors: list[dict[str, str]] = Field(default_factory=list)


class RequirementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int | None = Field(default=None, ge=1, le=50)
    min_ios: str | None = None
    max_ios: str | None = None
    model_family: str | None = None
    site: str | None = None
    region: str | None = None
    labels: list[str] | None = None


class JobIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: str
    params: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=0, ge=0, le=100)
    requirement: RequirementIn | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)


class JobOut(Out):
    id: UUID
    template_name: str
    params: dict[str, Any]
    requirement: dict[str, Any]
    priority: int
    state: str
    attempts: int
    max_attempts: int
    submitted_by: str
    schedule_id: UUID | None
    queue_wait_s: float | None
    created_at: datetime
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failure_class: str | None
    error: str | None


class ExecutionOut(Out):
    id: UUID
    job_id: UUID
    device_id: UUID
    host_id: UUID
    attempt: int
    state: str
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failure_class: str | None
    error: str | None
    session_restarts: int
    reboots: int


class StepOut(Out):
    index: int
    step_id: str
    type: str
    name: str
    state: str
    attempts: int
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    failure_class: str | None
    error: str | None
    output: dict[str, Any]


class ArtifactOut(Out):
    id: UUID
    execution_id: UUID
    name: str
    kind: str
    content_type: str
    size_bytes: int
    step_index: int | None
    created_at: datetime


class ExecutionDetailOut(ExecutionOut):
    steps: list[StepOut]
    artifacts: list[ArtifactOut]


class JobDetailOut(JobOut):
    executions: list[ExecutionOut]


class LogOut(Out):
    id: int | None
    ts: datetime
    level: str
    message: str
    step_index: int | None


class ScheduleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,62}$")
    template: str
    cron: str = Field(min_length=9, max_length=64)
    timezone: str = "UTC"
    params: dict[str, Any] = Field(default_factory=dict)
    requirement: RequirementIn | None = None
    priority: int = Field(default=0, ge=0, le=100)
    misfire_policy: Literal["skip", "run_once"] = "skip"
    enabled: bool = True


class ScheduleOut(Out):
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
    next_fire_at: datetime | None
    last_fire_at: datetime | None


class AlertRuleOut(Out):
    id: UUID
    name: str
    kind: str
    params: dict[str, Any]
    enabled: bool
    cooldown_s: int


class AlertRulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    params: dict[str, Any] | None = None
    cooldown_s: int | None = Field(default=None, ge=0)


class AlertOut(Out):
    id: UUID
    kind: str
    severity: str
    dedupe_key: str
    message: str
    payload: dict[str, Any]
    state: str
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    last_notified_at: datetime | None
    resolved_at: datetime | None


class WebhookIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    url: str = Field(max_length=500)


class WebhookOut(Out):
    id: UUID
    name: str
    url: str
    enabled: bool
    created_at: datetime


class WebhookCreatedOut(WebhookOut):
    secret: str


class ApiKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_\-]{1,62}$")
    role: Literal["admin", "operator", "viewer"]


class ApiKeyOut(Out):
    id: UUID
    name: str
    prefix: str
    role: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreatedOut(ApiKeyOut):
    secret: str


class EnrolmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host_name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,62}$")
    site: str = Field(min_length=1, max_length=64)
    region: str = Field(default="default", max_length=64)
    max_concurrency: int = Field(default=4, ge=1, le=64)


class EnrolmentOut(BaseModel):
    token: str
    host_name: str
    expires_at: datetime
    command: str


class AuditOut(Out):
    id: int | None
    ts: datetime
    actor: str
    action: str
    target_type: str
    target_id: str
    details: dict[str, Any]
    correlation_id: str | None


class LabelsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(max_length=16)


class ReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=500)


class MaintenanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class FleetSummaryOut(BaseModel):
    devices: dict[str, int]
    jobs: dict[str, int]
    hosts_total: int
    hosts_connected: int
    leases_active: int
    queue_depth: int
    sites: list[str]
