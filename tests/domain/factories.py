from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from devicelab.domain.models import (
    DeviceRequirement,
    DeviceState,
    DeviceView,
    JobView,
    Thermal,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def device(
    *,
    host_id: UUID | None = None,
    site: str = "berlin",
    region: str = "eu",
    model: str = "iPhone 15",
    family: str = "iPhone",
    ios: str = "18.2",
    labels: frozenset[str] = frozenset(),
    battery: int | None = 80,
    thermal: Thermal = Thermal.NOMINAL,
    state: DeviceState = DeviceState.AVAILABLE,
    id_: UUID | None = None,
) -> DeviceView:
    return DeviceView(
        id=id_ or uuid4(),
        host_id=host_id or uuid4(),
        site=site,
        region=region,
        model=model,
        model_family=family,
        ios_version=ios,
        labels=labels,
        battery_pct=battery,
        thermal=thermal,
        state=state,
    )


def job(
    *,
    priority: int = 0,
    queued_at: datetime = NOW,
    requirement: DeviceRequirement | None = None,
    excluded: frozenset[UUID] = frozenset(),
    id_: UUID | None = None,
) -> JobView:
    return JobView(
        id=id_ or uuid4(),
        priority=priority,
        queued_at=queued_at,
        requirement=requirement or DeviceRequirement(),
        excluded_device_ids=excluded,
    )
