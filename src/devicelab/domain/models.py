"""Core value types shared by the scheduler, the use cases and the adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DeviceState(StrEnum):
    AVAILABLE = "available"
    LEASED = "leased"
    BUSY = "busy"
    OFFLINE = "offline"
    QUARANTINED = "quarantined"
    MAINTENANCE = "maintenance"


class DeviceKind(StrEnum):
    SIMULATOR = "simulator"
    USB = "usb"
    FAKE = "fake"


class Thermal(StrEnum):
    NOMINAL = "nominal"
    FAIR = "fair"
    SERIOUS = "serious"
    CRITICAL = "critical"


class JobState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    LOST = "lost"


class LeaseState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


@dataclass(frozen=True, slots=True)
class DeviceRequirement:
    """What a job needs from the fleet. Empty fields mean "no constraint"."""

    count: int = 1
    min_ios: str | None = None
    max_ios: str | None = None
    model_family: str | None = None
    site: str | None = None
    region: str | None = None
    labels: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class DeviceView:
    """The slice of a device the scheduler is allowed to look at."""

    id: UUID
    host_id: UUID
    site: str
    region: str
    model: str
    model_family: str
    ios_version: str
    labels: frozenset[str]
    battery_pct: int | None
    thermal: Thermal
    state: DeviceState


@dataclass(frozen=True, slots=True)
class JobView:
    id: UUID
    priority: int
    queued_at: datetime
    requirement: DeviceRequirement
    excluded_device_ids: frozenset[UUID] = frozenset()


@dataclass(frozen=True, slots=True)
class Assignment:
    job_id: UUID
    device_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    aging_per_minute: float = 1.0
    min_battery_pct: int = 20
    blocked_thermal: frozenset[Thermal] = frozenset({Thermal.SERIOUS, Thermal.CRITICAL})


@dataclass(frozen=True, slots=True)
class LeaseView:
    id: UUID
    job_id: UUID
    device_id: UUID
    host_id: UUID
    fencing_token: int
    expires_at: datetime
    state: LeaseState = LeaseState.ACTIVE
    extra: dict[str, str] = field(default_factory=dict)
