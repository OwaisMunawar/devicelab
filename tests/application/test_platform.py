from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from devicelab.application.alerts import AlertService
from devicelab.application.support import Actor
from devicelab.domain.errors import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from devicelab.domain.models import Role
from tests.support import ADMIN, TEMPLATE, make_env

pytestmark = pytest.mark.usefixtures("db")


async def test_template_lifecycle(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    t = env.svc.templates
    created = await t.create(ADMIN, TEMPLATE.format(name="flow", count=1))
    assert created.version == 1
    with pytest.raises(ConflictError):
        await t.create(ADMIN, TEMPLATE.format(name="flow", count=1))
    with pytest.raises(ValidationFailedError):
        await t.create(ADMIN, "name: broken")

    updated = await t.update(ADMIN, "flow", TEMPLATE.format(name="flow", count=2))
    assert updated.version == 2 and updated.spec["requirements"]["count"] == 2
    with pytest.raises(ValidationFailedError):
        await t.update(ADMIN, "flow", TEMPLATE.format(name="other", count=1))
    with pytest.raises(NotFoundError):
        await t.update(ADMIN, "ghost", TEMPLATE.format(name="ghost", count=1))
    assert (await t.get("flow")).version == 2
    assert [x.name for x in await t.list()] == ["flow"]
    await t.delete(ADMIN, "flow")
    with pytest.raises(NotFoundError):
        await t.get("flow")
    with pytest.raises(NotFoundError):
        await t.delete(ADMIN, "flow")


def test_template_validation_reports_every_problem() -> None:
    from devicelab.application.jobs import TemplateService

    ok = TemplateService.validate(TEMPLATE.format(name="fine", count=1))
    assert ok.valid and ok.step_types == ["launch_app", "screenshot"]
    bad = TemplateService.validate("name: x\nsteps: [{id: A, type: nope}]")
    assert not bad.valid and bad.errors
    broken = TemplateService.validate("name: [")
    assert not broken.valid
    assert "reboot_device" in TemplateService.step_types()


async def test_schedule_fires_idempotently_and_honours_misfire(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    await env.template()
    s = env.svc.schedules
    with pytest.raises(ValidationFailedError):
        await s.create(ADMIN, name="bad", template_name="smoke", cron="nope")
    with pytest.raises(ValidationFailedError):
        await s.create(
            ADMIN, name="bad", template_name="smoke", cron="* * * * *", misfire_policy="x"
        )
    with pytest.raises(NotFoundError):
        await s.create(ADMIN, name="bad", template_name="ghost", cron="* * * * *")

    nightly = await s.create(
        ADMIN,
        name="hourly",
        template_name="smoke",
        cron="0 * * * *",
        timezone="UTC",
        misfire_policy="skip",
        priority=3,
    )
    assert nightly.next_fire_at == datetime(2026, 3, 1, 13, 0, tzinfo=UTC)
    assert await s.tick() == 0

    env.clock.advance(3600 + 5)  # 13:00:05, on time
    assert await s.tick() == 1
    assert await s.tick() == 0  # nothing new until the next boundary
    jobs = await env.svc.jobs.search()
    assert jobs.total == 1 and jobs.items[0].priority == 3
    assert jobs.items[0].submitted_by == "schedule:hourly"

    # Control plane down for 3 hours; `skip` drops the fires that are now too late.
    env.clock.advance(3 * 3600 + 1800)
    assert await s.tick() == 0
    assert (await env.svc.jobs.search()).total == 1

    # `run_once` collapses the backlog into a single job.
    once = await s.create(
        ADMIN, name="catchup", template_name="smoke", cron="0 * * * *", misfire_policy="run_once"
    )
    env.clock.advance(3 * 3600)
    assert await s.tick() == 1  # only the run_once schedule; "hourly" is still too late
    jobs = await env.svc.jobs.search()
    assert jobs.total == 2
    assert {j.submitted_by for j in jobs.items} == {"schedule:hourly", "schedule:catchup"}
    assert once.name == "catchup"


async def test_schedule_toggle_delete_and_list(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    await env.template()
    s = env.svc.schedules
    sched = await s.create(ADMIN, name="n", template_name="smoke", cron="*/5 * * * *")
    off = await s.set_enabled(ADMIN, sched.id, False)
    assert not off.enabled and off.next_fire_at is None
    env.clock.advance(3600)
    assert await s.tick() == 0
    on = await s.set_enabled(ADMIN, sched.id, True)
    assert on.next_fire_at is not None and on.checked_through == env.clock.now()
    assert len(await s.list()) == 1
    await s.delete(ADMIN, sched.id)
    with pytest.raises(NotFoundError):
        await s.delete(ADMIN, sched.id)
    with pytest.raises(NotFoundError):
        await s.set_enabled(ADMIN, sched.id, True)


async def test_alerts_dedupe_cooldown_and_resolve(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    alerts: AlertService = env.svc.alerts
    await alerts.ensure_default_rules()
    await alerts.ensure_default_rules()  # idempotent
    assert len(await alerts.list_rules()) == 4
    hook = await alerts.create_webhook(ADMIN, "slack", "https://hooks.example.test/x")
    with pytest.raises(ValidationFailedError):
        await alerts.create_webhook(ADMIN, "bad", "ftp://nope")
    assert len(await alerts.list_webhooks()) == 1

    host, devices = await env.fleet(1)
    env.clock.advance(env.cfg.offline_after_s + 1)
    await env.svc.fleet.sweep()
    assert await alerts.evaluate() == 0  # offline for seconds, rule needs 5 minutes

    env.clock.advance(6 * 60)
    assert await alerts.evaluate() == 1
    assert env.notifier.sent[0][1][0].id == hook.id
    first = (await alerts.list_alerts(state="open")).items
    assert len(first) == 1 and first[0].kind == "device_offline"

    env.clock.advance(60)  # inside the cooldown: counted, not re-sent
    assert await alerts.evaluate() == 0
    repeated = (await alerts.list_alerts(state="open")).items[0]
    assert repeated.occurrences == 2

    env.clock.advance(1000)  # past the 900s cooldown
    assert await alerts.evaluate() == 1

    await env.svc.fleet.heartbeat(host, [env.report(devices[0].udid).health], [])
    await alerts.evaluate()
    assert (await alerts.list_alerts(state="open")).total == 0
    assert (await alerts.list_alerts(state="resolved")).total == 1


async def test_failure_rate_queue_wait_and_reclaim_alerts(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    alerts = env.svc.alerts
    await alerts.ensure_default_rules()
    host, _ = await env.fleet(1)
    await env.template()
    rules = {r.kind: r for r in await alerts.list_rules()}
    tuned = await alerts.update_rule(
        ADMIN,
        rules["failure_rate"].id,
        enabled=True,
        params={"window_s": 3600, "threshold": 0.5, "min_executions": 2},
        cooldown_s=10,
    )
    assert tuned.cooldown_s == 10
    with pytest.raises(ValidationFailedError):
        await alerts.update_rule(ADMIN, tuned.id, enabled=None, params=None, cooldown_s=-1)
    with pytest.raises(NotFoundError):
        await alerts.update_rule(ADMIN, uuid4(), enabled=None, params=None, cooldown_s=None)

    for _ in range(2):
        await env.submit(max_attempts=1)
        await env.svc.scheduler.tick()
        await env.run_fail((await env.dispatch(host))[0], "assertion_failed", "x")
    await env.submit()
    await env.svc.scheduler.tick()
    (await env.dispatch(host))
    env.clock.advance(env.cfg.lease_ttl_s + 1)
    await env.svc.scheduler.reclaim_expired()
    await env.submit()
    env.clock.advance(400)
    await alerts.evaluate()
    kinds = {a.kind for a in (await alerts.list_alerts(state="open")).items}
    assert {"failure_rate", "queue_wait", "lease_reclaimed"} <= kinds

    reclaimed = next(a for a in (await alerts.list_alerts()).items if a.kind == "lease_reclaimed")
    done = await alerts.acknowledge(ADMIN, reclaimed.id)
    assert done.state == "resolved"
    with pytest.raises(NotFoundError):
        await alerts.acknowledge(ADMIN, uuid4())

    disabled = await alerts.update_rule(
        ADMIN, rules["queue_wait"].id, enabled=False, params=None, cooldown_s=None
    )
    assert not disabled.enabled
    await alerts.delete_webhook(
        ADMIN, (await alerts.create_webhook(ADMIN, "h", "http://x.test")).id
    )
    with pytest.raises(NotFoundError):
        await alerts.delete_webhook(ADMIN, uuid4())


async def test_daily_report_and_csv(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(2)
    await env.template()
    await env.submit()
    await env.submit(max_attempts=1)
    env.clock.advance(5)
    await env.svc.scheduler.tick()
    ok, bad = await env.dispatch(host)
    await env.run_ok(ok)
    await env.run_fail(bad, "assertion_failed", "=HYPERLINK(evil)")

    summary = await env.svc.reports.daily_summary()
    assert summary["executions"]["by_state"] == {"succeeded": 1, "failed": 1}
    assert summary["success_rate"] == 0.5
    assert summary["failure_classes"] == {"assertion_failed": 1}
    assert summary["queue_wait_s"]["p50"] == 5.0
    assert (await env.svc.reports.daily_summary(datetime(2020, 1, 1, tzinfo=UTC).date()))[
        "success_rate"
    ] is None

    start = env.clock.now() - timedelta(hours=1)
    csv_text = await env.svc.reports.export_csv(start, start + timedelta(hours=2))
    lines = csv_text.strip().splitlines()
    assert lines[0].startswith("execution_id,job_id,template") and len(lines) == 3

    from devicelab.application.reports import _csv_safe

    assert _csv_safe("=1+1") == "'=1+1" and _csv_safe("fine") == "fine" and _csv_safe(3) == 3


async def test_api_keys_and_roles(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    auth = env.svc.auth
    assert await auth.bootstrap("dlk_bootstrap_secret_value")
    assert not await auth.bootstrap("another")
    principal = await auth.authenticate("dlk_bootstrap_secret_value")
    assert principal.role is Role.ADMIN
    principal.require(Role.OPERATOR)

    secret, record = await auth.create_key(principal.actor, "viewer-key", Role.VIEWER)
    assert secret.startswith("dlk_") and record.key_hash != secret
    viewer = await auth.authenticate(secret)
    viewer.require(Role.VIEWER)
    with pytest.raises(ForbiddenError):
        viewer.require(Role.OPERATOR)
    with pytest.raises(ConflictError):
        await auth.create_key(principal.actor, "viewer-key", Role.VIEWER)
    assert viewer.actor == Actor("key:viewer-key", Role.VIEWER)

    env.clock.advance(120)
    await auth.authenticate(secret)  # refreshes last_used_at
    listed = {k.name: k for k in await auth.list()}
    assert listed["viewer-key"].last_used_at is not None

    await auth.revoke(principal.actor, record.id)
    with pytest.raises(UnauthorizedError):
        await auth.authenticate(secret)
    with pytest.raises(UnauthorizedError):
        await auth.authenticate("dlk_made_up")
    with pytest.raises(NotFoundError):
        await auth.revoke(principal.actor, uuid4())


async def test_audit_log_is_append_only(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    await env.template()
    async with uow_factory() as uow:
        session = uow.session
        for statement in (
            "UPDATE audit_log SET actor='x'",
            "DELETE FROM audit_log",
            "TRUNCATE audit_log",
        ):
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(text(statement))
            await session.rollback()
        page = await uow.audit.search(actor="key:test")
        assert page.total >= 1
