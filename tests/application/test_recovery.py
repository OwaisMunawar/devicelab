from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text

from devicelab.domain.errors import NotFoundError, StaleFenceError, ValidationFailedError
from devicelab.protocol.messages import (
    ArtifactRef,
    ArtifactUpload,
    ExecutionFinished,
    StepStarted,
)
from tests.support import make_env

pytestmark = pytest.mark.usefixtures("db")


async def test_happy_path_records_steps_logs_and_artifacts(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]
    assert await env.dispatch(host) == []  # dispatched exactly once

    artifact = await env.artifact(item, b"\x89PNG")
    assert artifact.size_bytes == 4
    await env.run_ok(item)
    with pytest.raises(StaleFenceError):  # the lease is gone once the execution finished
        await env.artifact(item)

    ex, steps, artifacts = await env.svc.executions.get(item.execution.id)
    assert ex.state == "succeeded" and ex.started_at and ex.finished_at
    assert [s.state for s in steps] == ["succeeded", "succeeded"]
    assert len(artifacts) == 1
    logs = await env.svc.executions.logs(ex.id)
    assert [log.message for log in logs] == ["running open", "running shot"]
    assert (await env.svc.jobs.get(job.id)).state == "succeeded"
    assert (await env.device(devices[0].id)).state == "available"
    record, content = await env.svc.executions.artifact_content(artifact.id)
    assert content == b"\x89PNG" and record.content_type == "image/png"
    assert "execution.log" in env.bus.topics()
    page = await env.svc.executions.search(job_id=job.id)
    assert page.total == 1


async def test_device_is_busy_while_running(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]
    await env.svc.executions.step_started(
        StepStarted(
            execution_id=str(item.execution.id),
            fencing_token=item.execution.fencing_token,
            step_index=0,
            step_id="open",
        )
    )
    assert (await env.device(devices[0].id)).state == "busy"
    assert (await env.svc.jobs.get(job.id)).state == "running"


async def test_infrastructure_failure_requeues_on_another_device(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(2)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    first = (await env.dispatch(host))[0]
    await env.run_fail(first, "wda_crashed", "Could not proxy command")

    requeued = await env.svc.jobs.get(job.id)
    assert requeued.state == "queued" and requeued.failure_class == "wda_crashed"
    await env.svc.scheduler.tick()
    second = (await env.dispatch(host))[0]
    assert second.execution.device_id != first.execution.device_id
    assert second.execution.attempt == 2
    await env.run_ok(second)
    assert (await env.svc.jobs.get(job.id)).state == "succeeded"
    # Requeue is audit-logged with the reason.
    async with uow_factory() as uow:
        audit = (await uow.audit.search(action="job.requeued")).items
    assert len(audit) == 1 and audit[0].details["failure_class"] == "wda_crashed"


async def test_assertion_failures_are_not_retried_or_counted(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]
    await env.run_fail(item, "assertion_failed", "expected 15")
    failed = await env.svc.jobs.get(job.id)
    assert failed.state == "failed" and failed.failure_class == "assertion_failed"
    device = await env.device(devices[0].id)
    assert device.state == "available" and device.consecutive_infra_failures == 0


async def test_attempts_are_capped(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory, quarantine_threshold=99)
    host, _ = await env.fleet(1)
    await env.template()
    job = await env.submit(max_attempts=2)
    for _ in range(2):
        async with uow_factory() as uow:  # forget exclusions so the single device is reusable
            await uow.jobs.update(job.id, excluded_device_ids=[])
            await uow.commit()
        await env.svc.scheduler.tick()
        await env.run_fail((await env.dispatch(host))[0], "device_unreachable", "gone")
    final = await env.svc.jobs.get(job.id)
    assert final.state == "failed" and final.attempts == 2


async def test_repeated_infra_failures_quarantine_the_device(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    await env.fleet(3, host="mac-2", site="austin")
    await env.template()
    bad = devices[0]
    for _ in range(3):
        job = await env.submit(requirement={"site": "berlin"}, max_attempts=1)
        await env.svc.scheduler.tick()
        await env.run_fail((await env.dispatch(host))[0], "wda_crashed", "boom")
        assert (await env.svc.jobs.get(job.id)).state == "failed"
    quarantined = await env.device(bad.id)
    assert quarantined.state == "quarantined"
    assert "3 consecutive" in (quarantined.quarantine_reason or "")

    await env.submit(requirement={"site": "berlin"})
    assert (await env.svc.scheduler.tick()).jobs_assigned == 0  # nothing schedulable there now

    back = await env.svc.fleet.return_to_service(
        __import__("tests.support", fromlist=["ADMIN"]).ADMIN, bad.id
    )
    assert back.consecutive_infra_failures == 0


async def test_a_success_resets_the_failure_streak(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    await env.template()
    for outcome in ("fail", "fail", "ok", "fail", "fail"):
        await env.submit(max_attempts=1)
        await env.svc.scheduler.tick()
        item = (await env.dispatch(host))[0]
        await (env.run_ok(item) if outcome == "ok" else env.run_fail(item))
    device = await env.device(devices[0].id)
    assert device.state == "available" and device.consecutive_infra_failures == 2


async def test_expired_lease_is_reclaimed_and_requeued_exactly_once(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(2)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]

    env.clock.advance(env.cfg.lease_ttl_s + 1)
    counts = await asyncio.gather(*(env.svc.scheduler.reclaim_expired() for _ in range(5)))
    assert sum(counts) == 1

    requeued = await env.svc.jobs.get(job.id)
    assert requeued.state == "queued"
    assert requeued.excluded_device_ids == [item.execution.device_id]
    ex, _, _ = await env.svc.executions.get(item.execution.id)
    assert ex.state == "lost"
    assert (await env.device(item.execution.device_id)).state == "offline"

    # The partitioned agent wakes up and reports: rejected by the fencing token.
    with pytest.raises(StaleFenceError):
        await env.svc.executions.execution_finished(
            ExecutionFinished(
                execution_id=str(ex.id), fencing_token=ex.fencing_token, state="succeeded"
            )
        )
    assert (await env.svc.jobs.get(job.id)).state == "queued"

    await env.svc.scheduler.tick()
    replacement = (await env.dispatch(host))[0]
    assert replacement.execution.device_id != item.execution.device_id
    async with uow_factory() as uow:
        total = await uow.session.scalar(text("SELECT count(*) FROM executions"))
    assert total == 2


async def test_heartbeats_keep_a_lease_alive(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(1)
    await env.template()
    await env.submit()
    await env.svc.scheduler.tick()
    items = await env.dispatch(host)
    udids = [items[0].device_udid]
    for _ in range(6):
        env.clock.advance(env.cfg.heartbeat_interval_s)
        result = await env.beat(host, udids, executions=env.refs(items))
        assert result.orphans == []
    assert await env.svc.scheduler.reclaim_expired() == 0

    # Stale token or unknown execution: reported back as an orphan to cancel.
    from devicelab.protocol.messages import ActiveExecution

    bogus = [
        ActiveExecution(execution_id=str(uuid4()), lease_id=str(uuid4()), fencing_token=1),
        ActiveExecution(execution_id="x", lease_id="not-a-uuid", fencing_token=1),
        ActiveExecution(
            execution_id=str(items[0].execution.id),
            lease_id=str(items[0].execution.lease_id),
            fencing_token=999,
        ),
    ]
    assert len((await env.beat(host, udids, executions=bogus)).orphans) == 3


async def test_reconcile_after_agent_restart(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(3)
    await env.template()
    survivor_job, doomed_job = await env.submit(), await env.submit()
    await env.svc.scheduler.tick()
    by_job = {i.execution.job_id: i for i in await env.dispatch(host)}
    survivor, doomed = by_job[survivor_job.id], by_job[doomed_job.id]

    # The agent reconnects knowing only about one of them, plus one it should not be running.
    ghost = env.refs([survivor])[0].model_copy(update={"execution_id": str(uuid4())})
    result = await env.svc.executions.reconcile_host(host.id, [*env.refs([survivor]), ghost])
    assert result.lost == [doomed.execution.id]
    assert result.orphans == [ghost.execution_id]
    assert (await env.svc.jobs.get(doomed_job.id)).state == "queued"
    assert (await env.svc.jobs.get(survivor_job.id)).state == "leased"
    # Reconciling again changes nothing.
    again = await env.svc.executions.reconcile_host(host.id, env.refs([survivor]))
    assert again.lost == []


async def test_undispatched_work_survives_reconnect(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(1)
    await env.template()
    await env.submit()
    await env.svc.scheduler.tick()
    assert (await env.svc.executions.reconcile_host(host.id, [])).lost == []
    assert len(await env.dispatch(host)) == 1


async def test_report_validation(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(1)
    await env.template()
    await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]
    ex = item.execution
    base = {"execution_id": str(ex.id), "fencing_token": ex.fencing_token}

    with pytest.raises(NotFoundError):
        await env.svc.executions.step_started(
            StepStarted(execution_id="nope", fencing_token=1, step_index=0, step_id="a")
        )
    with pytest.raises(StaleFenceError):
        await env.svc.executions.step_started(
            StepStarted(
                execution_id=str(ex.id),
                fencing_token=ex.fencing_token + 1,
                step_index=0,
                step_id="open",
            )
        )
    with pytest.raises(NotFoundError):
        await env.svc.executions.step_started(StepStarted(**base, step_index=9, step_id="x"))

    bad = ArtifactUpload(
        **base,
        artifact=ArtifactRef(name="a", kind="log", content_type="text/plain"),
        data_b64="!!!not base64",
    )
    with pytest.raises(ValidationFailedError):
        await env.svc.executions.artifact_uploaded(bad)
    import base64

    huge = base64.b64encode(b"0" * (20 * 1024 * 1024 + 1)).decode()
    with pytest.raises(ValidationFailedError):
        await env.svc.executions.artifact_uploaded(bad.model_copy(update={"data_b64": huge}))
    with pytest.raises(NotFoundError):
        await env.svc.executions.get(uuid4())
    with pytest.raises(NotFoundError):
        await env.svc.executions.artifact_content(uuid4())

    await env.svc.executions.execution_finished(
        ExecutionFinished(**base, state="cancelled", error="operator")
    )
    assert (await env.svc.executions.get(ex.id))[0].state == "cancelled"


async def test_artifact_retention(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory, artifact_retention_days=1)
    host, _ = await env.fleet(1)
    await env.template()
    await env.submit()
    await env.svc.scheduler.tick()
    item = (await env.dispatch(host))[0]
    artifact = await env.artifact(item)
    assert await env.svc.executions.apply_retention() == 0
    env.clock.advance(86400 + 1)
    assert await env.svc.executions.apply_retention() == 1
    assert artifact.storage_key not in env.store.blobs
