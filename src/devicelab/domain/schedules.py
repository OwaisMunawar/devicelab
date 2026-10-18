from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError

from devicelab.domain.errors import ValidationFailedError


class MisfirePolicy(StrEnum):
    SKIP = "skip"
    RUN_ONCE = "run_once"


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        raise ValidationFailedError(f"unknown time zone: {name}", code="invalid_timezone") from None


def validate_cron(expression: str, timezone: str) -> None:
    _zone(timezone)
    try:
        CronSim(expression, datetime(2026, 1, 1, tzinfo=_zone(timezone)))
    except CronSimError as exc:
        raise ValidationFailedError(f"invalid cron: {exc}", code="invalid_cron") from exc


def next_fire(expression: str, timezone: str, after: datetime) -> datetime:
    zone = _zone(timezone)
    it = CronSim(expression, after.astimezone(zone))
    return next(it).astimezone(UTC)


def due_fire(
    expression: str,
    timezone: str,
    *,
    checked_through: datetime,
    now: datetime,
    policy: MisfirePolicy,
    grace: timedelta = timedelta(seconds=90),
    scan_limit: int = 10_000,
) -> datetime | None:
    """The single fire time to enqueue, if any, between `checked_through` and `now`.

    Several missed fires collapse into the latest one: enqueuing a backlog of identical
    runs after an outage is never what an operator wants. `skip` additionally drops a fire
    that is more than `grace` late; `run_once` still runs it.
    """
    zone = _zone(timezone)
    it = CronSim(expression, checked_through.astimezone(zone))
    latest: datetime | None = None
    for _ in range(scan_limit):
        candidate = next(it).astimezone(UTC)
        if candidate > now:
            break
        latest = candidate
    if latest is None:
        return None
    if policy is MisfirePolicy.RUN_ONCE or now - latest <= grace:
        return latest
    return None
