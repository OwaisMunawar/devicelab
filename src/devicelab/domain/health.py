from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from devicelab.domain.models import Thermal


class HealthStatus(StrEnum):
    OK = "ok"
    DEGRADED = "degraded"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class HealthAssessment:
    status: HealthStatus
    reasons: tuple[str, ...]


def assess_health(
    battery_pct: int | None, thermal: str, storage_free_gb: float | None
) -> HealthAssessment:
    """Impossible readings mean the reporter is broken, which is worse than a low battery."""
    corrupt: list[str] = []
    degraded: list[str] = []
    if battery_pct is not None and not 0 <= battery_pct <= 100:
        corrupt.append(f"battery out of range: {battery_pct}")
    if thermal not in {t.value for t in Thermal}:
        corrupt.append(f"unknown thermal state: {thermal!r}")
    if storage_free_gb is not None and storage_free_gb < 0:
        corrupt.append(f"negative storage: {storage_free_gb}")
    if corrupt:
        return HealthAssessment(HealthStatus.CORRUPT, tuple(corrupt))
    if battery_pct is not None and battery_pct < 15:
        degraded.append("battery low")
    if thermal in (Thermal.SERIOUS, Thermal.CRITICAL):
        degraded.append(f"thermal {thermal}")
    if storage_free_gb is not None and storage_free_gb < 1.0:
        degraded.append("storage nearly full")
    status = HealthStatus.DEGRADED if degraded else HealthStatus.OK
    return HealthAssessment(status, tuple(degraded))
