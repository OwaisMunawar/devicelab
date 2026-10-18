"""Alert rule evaluation. Pure: takes a snapshot, returns candidates. Persistence,
deduplication state and delivery live in the application layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any


class AlertKind(StrEnum):
    DEVICE_OFFLINE = "device_offline"
    FAILURE_RATE = "failure_rate"
    QUEUE_WAIT = "queue_wait"
    LEASE_RECLAIMED = "lease_reclaimed"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class OfflineDevice:
    device_id: str
    name: str
    site: str
    offline_since: datetime


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    now: datetime
    offline_devices: tuple[OfflineDevice, ...] = ()
    finished_in_window: int = 0
    failed_in_window: int = 0
    oldest_queued_wait_s: float = 0.0
    reclaimed_leases: tuple[tuple[str, str], ...] = field(
        default_factory=tuple
    )  # (lease_id, device)


@dataclass(frozen=True, slots=True)
class AlertCandidate:
    kind: AlertKind
    severity: Severity
    dedupe_key: str
    message: str
    payload: dict[str, Any]


def evaluate(
    kind: AlertKind, params: dict[str, Any], snap: MetricsSnapshot
) -> list[AlertCandidate]:
    match kind:
        case AlertKind.DEVICE_OFFLINE:
            threshold = timedelta(minutes=float(params.get("minutes", 5)))
            return [
                AlertCandidate(
                    kind,
                    Severity.WARNING,
                    f"device_offline:{d.device_id}",
                    f"{d.name} ({d.site}) has been offline for "
                    f"{int((snap.now - d.offline_since).total_seconds() // 60)} min",
                    {"device_id": d.device_id, "site": d.site},
                )
                for d in snap.offline_devices
                if snap.now - d.offline_since >= threshold
            ]
        case AlertKind.FAILURE_RATE:
            minimum = int(params.get("min_executions", 10))
            limit = float(params.get("threshold", 0.3))
            if snap.finished_in_window < minimum:
                return []
            rate = snap.failed_in_window / snap.finished_in_window
            if rate < limit:
                return []
            return [
                AlertCandidate(
                    kind,
                    Severity.CRITICAL if rate >= 2 * limit else Severity.WARNING,
                    "failure_rate",
                    f"{rate:.0%} of {snap.finished_in_window} recent executions failed",
                    {"rate": round(rate, 4), "window_s": params.get("window_s", 3600)},
                )
            ]
        case AlertKind.QUEUE_WAIT:
            sla = float(params.get("sla_s", 300))
            if snap.oldest_queued_wait_s <= sla:
                return []
            return [
                AlertCandidate(
                    kind,
                    Severity.WARNING,
                    "queue_wait",
                    f"oldest queued job has waited {int(snap.oldest_queued_wait_s)}s "
                    f"(SLA {int(sla)}s)",
                    {"wait_s": snap.oldest_queued_wait_s, "sla_s": sla},
                )
            ]
        case AlertKind.LEASE_RECLAIMED:
            return [
                AlertCandidate(
                    kind,
                    Severity.WARNING,
                    f"lease_reclaimed:{lease_id}",
                    f"lease on {device} was reclaimed from an unresponsive agent",
                    {"lease_id": lease_id, "device": device},
                )
                for lease_id, device in snap.reclaimed_leases
            ]


def should_notify(last_notified_at: datetime | None, now: datetime, cooldown_s: float) -> bool:
    """One notification per dedupe key per cooldown window; the feed still counts repeats."""
    return last_notified_at is None or (now - last_notified_at).total_seconds() >= cooldown_s
