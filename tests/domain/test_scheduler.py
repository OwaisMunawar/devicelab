from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devicelab.domain.matching import matches
from devicelab.domain.models import (
    Assignment,
    DeviceRequirement,
    DeviceState,
    DeviceView,
    JobView,
    SchedulerPolicy,
    Thermal,
)
from devicelab.domain.scheduler import effective_priority, is_schedulable, plan_assignments
from tests.domain.factories import NOW, device, job


def plan(
    jobs: Sequence[JobView],
    devices: Sequence[DeviceView],
    active: Mapping[UUID, int] | None = None,
    limits: Mapping[UUID, int] | None = None,
    policy: SchedulerPolicy | None = None,
) -> list[Assignment]:
    return plan_assignments(
        jobs, devices, host_active=active or {}, host_limits=limits or {}, now=NOW, policy=policy
    )


def test_higher_priority_wins_the_only_device() -> None:
    d = device()
    low, high = job(priority=1), job(priority=9)
    result = plan([low, high], [d])
    assert [a.job_id for a in result] == [high.id]


def test_aging_lets_an_old_low_priority_job_overtake() -> None:
    d = device()
    old = job(priority=0, queued_at=NOW - timedelta(minutes=30))
    fresh = job(priority=10, queued_at=NOW)
    result = plan([fresh, old], [d], policy=SchedulerPolicy(aging_per_minute=1.0))
    assert result[0].job_id == old.id


def test_effective_priority_never_negative_wait() -> None:
    assert effective_priority(5, NOW + timedelta(minutes=5), NOW, 1.0) == 5


def test_unsatisfiable_job_does_not_block_the_queue() -> None:
    d = device(ios="17.0")
    blocked = job(priority=10, requirement=DeviceRequirement(min_ios="18.0"))
    runnable = job(priority=1)
    result = plan([blocked, runnable], [d])
    assert [a.job_id for a in result] == [runnable.id]


def test_multi_device_job_is_all_or_nothing() -> None:
    j = job(requirement=DeviceRequirement(count=3))
    assert plan([j], [device(), device()]) == []
    assert len(plan([j], [device(), device(), device()])[0].device_ids) == 3


def test_multi_device_prefers_a_single_site() -> None:
    berlin = [device(site="berlin") for _ in range(2)]
    austin = [device(site="austin") for _ in range(3)]
    result = plan([job(requirement=DeviceRequirement(count=2))], berlin + austin)
    chosen = {d.site for d in berlin + austin if d.id in result[0].device_ids}
    assert len(chosen) == 1


def test_excluded_devices_are_skipped() -> None:
    bad, good = device(), device()
    result = plan([job(excluded=frozenset({bad.id}))], [bad, good])
    assert result[0].device_ids == (good.id,)


def test_host_concurrency_limit_is_respected() -> None:
    host = uuid4()
    devices = [device(host_id=host) for _ in range(4)]
    jobs = [job() for _ in range(4)]
    assert len(plan(jobs, devices, limits={host: 2})) == 2
    assert len(plan(jobs, devices, active={host: 1}, limits={host: 2})) == 1


def test_spreads_load_across_hosts() -> None:
    h1, h2 = uuid4(), uuid4()
    devices = [device(host_id=h1), device(host_id=h1), device(host_id=h2), device(host_id=h2)]
    result = plan([job(), job()], devices)
    by_host = Counter(d.host_id for d in devices for a in result if d.id in a.device_ids)
    assert by_host == {h1: 1, h2: 1}


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, True),
        ({"state": DeviceState.LEASED}, False),
        ({"state": DeviceState.QUARANTINED}, False),
        ({"battery": 5}, False),
        ({"battery": None}, True),
        ({"thermal": Thermal.SERIOUS}, False),
        ({"thermal": Thermal.FAIR}, True),
    ],
)
def test_schedulable_rules(kwargs: dict[str, Any], expected: bool) -> None:
    assert is_schedulable(device(**kwargs), SchedulerPolicy()) is expected


@pytest.mark.parametrize(
    ("req", "dev", "expected"),
    [
        (DeviceRequirement(site="berlin"), {"site": "austin"}, False),
        (DeviceRequirement(region="eu"), {"region": "us"}, False),
        (DeviceRequirement(model_family="iPad"), {}, False),
        (DeviceRequirement(max_ios="17.9"), {"ios": "18.0"}, False),
        (DeviceRequirement(min_ios="18"), {"ios": "18.0.0"}, True),
        (DeviceRequirement(labels=frozenset({"wifi-only"})), {}, False),
        (DeviceRequirement(labels=frozenset({"a"})), {"labels": frozenset({"a", "b"})}, True),
    ],
)
def test_matching(req: DeviceRequirement, dev: dict[str, Any], expected: bool) -> None:
    assert matches(req, device(**dev)) is expected


_sites = st.sampled_from(["berlin", "austin", "tokyo"])
_ios = st.sampled_from(["16.4", "17.5", "18.2", "26.0"])
_labels = st.lists(st.sampled_from(["a", "b"]), max_size=2).map(frozenset)


@st.composite
def fleets(
    draw: st.DrawFn,
) -> tuple[list[DeviceView], list[JobView], dict[UUID, int]]:
    hosts = [uuid4() for _ in range(draw(st.integers(1, 4)))]
    devices = [
        device(
            host_id=draw(st.sampled_from(hosts)),
            site=draw(_sites),
            ios=draw(_ios),
            state=draw(st.sampled_from(list(DeviceState))),
            battery=draw(st.one_of(st.none(), st.integers(0, 100))),
            thermal=draw(st.sampled_from(list(Thermal))),
            labels=draw(_labels),
        )
        for _ in range(draw(st.integers(0, 12)))
    ]
    jobs = [
        job(
            priority=draw(st.integers(0, 10)),
            queued_at=NOW - timedelta(seconds=draw(st.integers(0, 7200))),
            requirement=DeviceRequirement(
                count=draw(st.integers(1, 3)),
                min_ios=draw(st.one_of(st.none(), _ios)),
                site=draw(st.one_of(st.none(), _sites)),
                labels=draw(_labels.map(lambda s: frozenset(list(s)[:1]))),
            ),
        )
        for _ in range(draw(st.integers(0, 12)))
    ]
    limits = {h: draw(st.integers(1, 3)) for h in hosts if draw(st.booleans())}
    return devices, jobs, limits


@settings(max_examples=300, deadline=None)
@given(fleets())
def test_plan_invariants(
    data: tuple[list[DeviceView], list[JobView], dict[UUID, int]],
) -> None:
    devices, jobs, limits = data
    by_id = {d.id: d for d in devices}
    result = plan(jobs, devices, limits=limits)

    assigned = [i for a in result for i in a.device_ids]
    assert len(assigned) == len(set(assigned)), "a device was assigned twice"
    assert len({a.job_id for a in result}) == len(result), "a job was assigned twice"

    jobs_by_id = {j.id: j for j in jobs}
    for a in result:
        j = jobs_by_id[a.job_id]
        assert len(a.device_ids) == j.requirement.count
        for did in a.device_ids:
            assert is_schedulable(by_id[did], SchedulerPolicy())
            assert matches(j.requirement, by_id[did])

    for host, n in Counter(by_id[i].host_id for i in assigned).items():
        if host in limits:
            assert n <= limits[host]


@settings(max_examples=100, deadline=None)
@given(fleets())
def test_plan_ignores_input_order(
    data: tuple[list[DeviceView], list[JobView], dict[UUID, int]],
) -> None:
    devices, jobs, limits = data
    assert plan(jobs, devices, limits=limits) == plan(
        list(reversed(jobs)), list(reversed(devices)), limits=limits
    )
