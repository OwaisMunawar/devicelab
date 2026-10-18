"""Pure scheduling. Given a snapshot of queued jobs and free devices, decide who gets what.

No I/O happens here: the application layer loads rows (locked with SKIP LOCKED), calls
`plan_assignments`, and persists the result. That split is what lets the invariants be
property-tested without a database.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from uuid import UUID

from devicelab.domain.matching import matches
from devicelab.domain.models import (
    Assignment,
    DeviceState,
    DeviceView,
    JobView,
    SchedulerPolicy,
)


def effective_priority(
    priority: int, queued_at: datetime, now: datetime, aging_per_minute: float
) -> float:
    """Priority plus a wait bonus, so a flood of high-priority work cannot starve old jobs."""
    waited_min = max((now - queued_at).total_seconds(), 0.0) / 60.0
    return priority + aging_per_minute * waited_min


def is_schedulable(device: DeviceView, policy: SchedulerPolicy) -> bool:
    if device.state is not DeviceState.AVAILABLE:
        return False
    if device.thermal in policy.blocked_thermal:
        return False
    return device.battery_pct is None or device.battery_pct >= policy.min_battery_pct


def plan_assignments(
    jobs: Iterable[JobView],
    devices: Iterable[DeviceView],
    *,
    host_active: Mapping[UUID, int],
    host_limits: Mapping[UUID, int],
    now: datetime,
    policy: SchedulerPolicy | None = None,
) -> list[Assignment]:
    """Greedy, priority-ordered, all-or-nothing per job.

    A job that cannot be satisfied is skipped rather than blocking the queue (backfill).
    The cost is that a large multi-device job can wait behind small ones; aging bounds that.
    """
    policy = policy or SchedulerPolicy()
    free = {d.id: d for d in devices if is_schedulable(d, policy)}
    load: dict[UUID, int] = defaultdict(int, host_active)

    ordered = sorted(
        jobs,
        key=lambda j: (
            -effective_priority(j.priority, j.queued_at, now, policy.aging_per_minute),
            j.queued_at,
            str(j.id),
        ),
    )

    plan: list[Assignment] = []
    for job in ordered:
        chosen = _select_devices(job, free.values(), load, host_limits)
        if chosen is None:
            continue
        for device in chosen:
            del free[device.id]
            load[device.host_id] += 1
        plan.append(Assignment(job.id, tuple(d.id for d in chosen)))
    return plan


def _select_devices(
    job: JobView,
    free: Iterable[DeviceView],
    load: Mapping[UUID, int],
    host_limits: Mapping[UUID, int],
) -> list[DeviceView] | None:
    need = job.requirement.count
    candidates = [
        d for d in free if d.id not in job.excluded_device_ids and matches(job.requirement, d)
    ]
    if len(candidates) < need:
        return None

    # Least-loaded host first, then healthiest battery; id keeps the result deterministic.
    def rank(d: DeviceView) -> tuple[int, int, str]:
        return (load.get(d.host_id, 0), -(d.battery_pct or 0), str(d.id))

    candidates.sort(key=rank)

    pools: Sequence[Sequence[DeviceView]]
    if need > 1 and not job.requirement.site:
        # Multi-device jobs are usually comparing like with like; keep them in one site if we can.
        by_site: dict[str, list[DeviceView]] = defaultdict(list)
        for d in candidates:
            by_site[d.site].append(d)
        pools = [*sorted(by_site.values(), key=lambda p: (-len(p), p[0].site)), candidates]
    else:
        pools = [candidates]

    for pool in pools:
        picked = _take(pool, need, load, host_limits)
        if picked is not None:
            return picked
    return None


def _take(
    pool: Sequence[DeviceView],
    need: int,
    load: Mapping[UUID, int],
    host_limits: Mapping[UUID, int],
) -> list[DeviceView] | None:
    picked: list[DeviceView] = []
    taken_per_host: dict[UUID, int] = defaultdict(int)
    for d in pool:
        limit = host_limits.get(d.host_id)
        if limit is not None and load.get(d.host_id, 0) + taken_per_host[d.host_id] >= limit:
            continue
        picked.append(d)
        taken_per_host[d.host_id] += 1
        if len(picked) == need:
            return picked
    return None
