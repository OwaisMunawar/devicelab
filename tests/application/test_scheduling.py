from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from devicelab.application.jobs import SubmitJob
from devicelab.domain.errors import NotFoundError, ValidationFailedError
from tests.support import ADMIN, make_env

pytestmark = pytest.mark.usefixtures("db")


async def test_tick_leases_devices_by_priority(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(1)
    await env.template()
    low = await env.submit(priority=1)
    high = await env.submit(priority=9)

    result = await env.svc.scheduler.tick()
    assert (result.jobs_assigned, result.devices_leased) == (1, 1)
    assert (await env.svc.jobs.get(high.id)).state == "leased"
    assert (await env.svc.jobs.get(low.id)).state == "queued"
    assert env.gateway.wakes == [host.id]

    # Nothing free: the next tick is a cheap no-op rather than an error.
    assert (await env.svc.scheduler.tick()).jobs_assigned == 0


async def test_requirements_filter_devices(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    await env.fleet(1, host="old", site="austin", ios="16.4")
    await env.fleet(1, host="new", site="berlin", ios="18.2", labels=["dark-lab"])
    await env.template()
    wants_new = await env.submit(requirement={"min_ios": "18.0"})
    wants_label = await env.submit(requirement={"labels": ["dark-lab"], "site": "berlin"})
    wants_none = await env.submit(requirement={"min_ios": "30"})
    await env.svc.scheduler.tick()
    assert (await env.svc.jobs.get(wants_none.id)).state == "queued"
    states = [(await env.svc.jobs.get(j.id)).state for j in (wants_new, wants_label)]
    assert sorted(states) == ["leased", "queued"]  # only one new-enough device exists


async def test_host_concurrency_limit(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host = await env.host("mac-1", max_concurrency=2)
    await env.svc.fleet.register_inventory(host, [env.report(f"u{i}") for i in range(4)])
    await env.template()
    for _ in range(4):
        await env.submit()
    assert (await env.svc.scheduler.tick()).jobs_assigned == 2
    assert (await env.svc.scheduler.tick()).jobs_assigned == 0


async def test_queue_wait_is_recorded_once(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(2)
    await env.template()
    job = await env.submit()
    env.clock.advance(42)
    await env.svc.scheduler.tick()
    leased = await env.svc.jobs.get(job.id)
    assert leased.queue_wait_s == pytest.approx(42)
    assert leased.attempts == 1

    item = (await env.dispatch(host))[0]
    await env.run_fail(item)  # requeue
    env.clock.advance(100)
    await env.svc.scheduler.tick()
    again = await env.svc.jobs.get(job.id)
    assert again.queue_wait_s == pytest.approx(42)
    assert again.attempts == 2


async def test_unrenderable_job_fails_instead_of_looping(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    await env.fleet(1)
    await env.template()
    job = await env.submit()
    async with uow_factory() as uow:
        broken = {
            **job.spec,
            "steps": [{"id": "x", "type": "screenshot", "artifact_name": "{{ device.nope }}"}],
        }
        await uow.jobs.update(job.id, spec=broken)
        await uow.commit()
    result = await env.svc.scheduler.tick()
    assert result.jobs_failed == 1
    failed = await env.svc.jobs.get(job.id)
    assert failed.state == "failed" and "could not be rendered" in (failed.error or "")


async def test_concurrent_ticks_never_double_lease(uow_factory) -> None:  # type: ignore[no-untyped-def]
    """Eight schedulers racing over the same queue and fleet. SKIP LOCKED plus the partial
    unique index must leave every device with at most one active lease."""
    env = make_env(uow_factory)
    host = await env.host("mac-1", max_concurrency=64)
    await env.svc.fleet.register_inventory(host, [env.report(f"u{i}") for i in range(12)])
    await env.template()
    for _ in range(40):
        await env.submit()

    await asyncio.gather(*(env.svc.scheduler.tick() for _ in range(8)))
    await asyncio.gather(*(env.svc.scheduler.tick() for _ in range(8)))

    async with uow_factory() as uow:
        rows = (
            await uow.session.execute(
                text("SELECT device_id, count(*) FROM leases WHERE state='active' GROUP BY 1")
            )
        ).all()
        leased_jobs = (await uow.jobs.count_by_state()).get("leased", 0)
    assert len(rows) == 12
    assert all(n == 1 for _, n in rows)
    assert leased_jobs == 12


async def test_unique_index_is_the_backstop(uow_factory) -> None:  # type: ignore[no-untyped-def]
    from datetime import timedelta

    from devicelab.application.records import LeaseRecord
    from devicelab.domain.errors import ConflictError

    env = make_env(uow_factory)
    _, devices = await env.fleet(1)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    async with uow_factory() as uow:
        existing = await uow.leases.get_active_for_device(devices[0].id)
        assert existing is not None
        clash = LeaseRecord(
            id=uuid4(),
            job_id=job.id,
            device_id=devices[0].id,
            host_id=existing.host_id,
            fencing_token=99,
            state="active",
            acquired_at=env.clock.now(),
            expires_at=env.clock.now() + timedelta(seconds=30),
        )
        with pytest.raises(ConflictError):
            await uow.leases.add(clash)


async def test_idempotent_submission_and_validation(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    await env.template()
    first, created = await env.svc.jobs.submit(ADMIN, SubmitJob("smoke", idempotency_key="k1"))
    second, created_again = await env.svc.jobs.submit(
        ADMIN, SubmitJob("smoke", idempotency_key="k1")
    )
    assert created and not created_again and first.id == second.id
    # The same key from a different caller is a different job.
    other = ADMIN.__class__("key:other", ADMIN.role)
    third, created_other = await env.svc.jobs.submit(
        other, SubmitJob("smoke", idempotency_key="k1")
    )
    assert created_other and third.id != first.id

    racing = await asyncio.gather(
        *(env.svc.jobs.submit(ADMIN, SubmitJob("smoke", idempotency_key="race")) for _ in range(6))
    )
    assert len({job.id for job, _ in racing}) == 1
    assert sum(created for _, created in racing) == 1

    with pytest.raises(NotFoundError):
        await env.svc.jobs.submit(ADMIN, SubmitJob("missing"))
    with pytest.raises(ValidationFailedError):
        await env.svc.jobs.submit(ADMIN, SubmitJob("smoke", priority=500))
    with pytest.raises(ValidationFailedError):
        await env.svc.jobs.submit(ADMIN, SubmitJob("smoke", max_attempts=0))
    with pytest.raises(ValidationFailedError):
        await env.svc.jobs.submit(ADMIN, SubmitJob("smoke", params={"nope": 1}))
    page = await env.svc.jobs.search(template_name="smoke")
    assert page.total == 3  # k1, other caller, race
    assert (await env.svc.jobs.counts())["queued"] == 3


async def test_cancel_queued_and_running_jobs(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    await env.template()
    queued = await env.submit()
    cancelled = await env.svc.jobs.cancel(ADMIN, queued.id)
    assert cancelled.state == "cancelled"
    assert (await env.svc.jobs.cancel(ADMIN, queued.id)).state == "cancelled"

    running = await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]
    await env.svc.jobs.cancel(ADMIN, running.id)
    assert (await env.device(devices[0].id)).state == "available"
    assert env.gateway.cancels[-1][1] == item.execution.id
    with pytest.raises(NotFoundError):
        await env.svc.jobs.cancel(ADMIN, uuid4())
    with pytest.raises(NotFoundError):
        await env.svc.jobs.get(uuid4())


async def test_multi_device_job_waits_for_all_devices(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(3)
    await env.template("pair", count=2)
    job = await env.submit("pair")
    await env.svc.scheduler.tick()
    items = await env.dispatch(host)
    assert len(items) == 2

    await env.run_ok(items[0])
    assert (await env.svc.jobs.get(job.id)).state != "succeeded"
    await env.run_ok(items[1])
    assert (await env.svc.jobs.get(job.id)).state == "succeeded"


async def test_multi_device_failure_cancels_siblings_and_requeues(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(4)
    await env.template("pair", count=2)
    job = await env.submit("pair")
    await env.svc.scheduler.tick()
    first, second = await env.dispatch(host)
    await env.run_fail(first)

    assert (await env.svc.jobs.get(job.id)).state == "queued"
    assert env.gateway.cancels[-1][1] == second.execution.id
    assert (await env.device(second.execution.device_id)).state == "available"
    await env.svc.scheduler.tick()
    new_items = await env.dispatch(host)
    assert len(new_items) == 2
    assert first.execution.device_id not in {i.execution.device_id for i in new_items}
