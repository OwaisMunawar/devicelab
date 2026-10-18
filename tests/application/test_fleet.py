from __future__ import annotations

import pytest

from devicelab.application.support import Actor
from devicelab.domain.errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from devicelab.domain.models import DeviceState, Role
from devicelab.protocol.messages import HealthReport
from tests.support import ADMIN, make_env

pytestmark = pytest.mark.usefixtures("db")


async def test_enrolment_is_one_time_and_expires(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    fleet = env.svc.fleet
    token, _ = await fleet.create_enrolment_token(ADMIN, host_name="mac-1", site="berlin")
    host, credential = await fleet.enrol(token, "mac-1")
    assert (await fleet.authenticate_host(credential)).id == host.id

    with pytest.raises(UnauthorizedError) as used:
        await fleet.enrol(token, "mac-1")
    assert used.value.code == "enrolment_used"

    expiring, _ = await fleet.create_enrolment_token(ADMIN, host_name="mac-2", site="x", ttl_s=10)
    env.clock.advance(11)
    with pytest.raises(UnauthorizedError) as expired:
        await fleet.enrol(expiring, "mac-2")
    assert expired.value.code == "enrolment_expired"


async def test_enrolment_rejections(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    fleet = env.svc.fleet
    with pytest.raises(UnauthorizedError):
        await fleet.enrol("dle_nope", "mac-1")
    token, _ = await fleet.create_enrolment_token(ADMIN, host_name="mac-1", site="berlin")
    with pytest.raises(UnauthorizedError) as wrong:
        await fleet.enrol(token, "someone-else")
    assert wrong.value.code == "enrolment_host"
    with pytest.raises(ValidationFailedError):
        await fleet.create_enrolment_token(ADMIN, host_name="x", site="y", max_concurrency=0)
    with pytest.raises(UnauthorizedError):
        await fleet.authenticate_host("dla_unknown")

    await env.host("mac-9")
    again, _ = await fleet.create_enrolment_token(ADMIN, host_name="mac-9", site="berlin")
    with pytest.raises(ConflictError):
        await fleet.enrol(again, "mac-9")


async def test_inventory_creates_and_updates_devices(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(2)
    assert {d.state for d in devices} == {"available"}
    assert all(d.site == "berlin" and d.region == "eu" for d in devices)

    # A device that stops being reported goes offline; one that is back returns to service.
    await env.svc.fleet.register_inventory(host, [env.report("mac-1-u0", ios="18.3")])
    by_udid = {d.udid: d for d in (await env.svc.fleet.list_devices()).items}
    assert by_udid["mac-1-u1"].state == "offline"
    assert by_udid["mac-1-u0"].ios_version == "18.3"
    await env.svc.fleet.register_inventory(host, [env.report("mac-1-u1")])
    assert (await env.device(by_udid["mac-1-u1"].id)).state == "available"


async def test_unreachable_device_is_offline_then_recovers(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, _ = await env.fleet(0)
    await env.svc.fleet.register_inventory(host, [env.report("u1", connectivity="unreachable")])
    device = (await env.svc.fleet.list_devices()).items[0]
    assert device.state == "offline"
    await env.svc.fleet.heartbeat(
        host, [HealthReport(udid="u1", battery_pct=80, connectivity="connected")], []
    )
    assert (await env.device(device.id)).state == "available"
    await env.svc.fleet.heartbeat(
        host, [HealthReport(udid="u1", battery_pct=80, connectivity="unreachable")], []
    )
    assert (await env.device(device.id)).state == "offline"


async def test_heartbeat_samples_health_sparsely(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(1)
    device = devices[0]
    for _ in range(5):
        env.clock.advance(5)
        await env.beat(host, [device.udid])
    first = await env.svc.fleet.device_timeline(device.id)
    assert len(first) == 1  # only the inventory sample; steady heartbeats add nothing
    env.clock.advance(5)
    await env.beat(host, [device.udid], battery=60)
    assert len(await env.svc.fleet.device_timeline(device.id)) == 2
    env.clock.advance(env.cfg.sample_interval_s + 1)
    await env.beat(host, [device.udid], battery=60)
    assert len(await env.svc.fleet.device_timeline(device.id)) == 3
    assert (await env.svc.fleet.fleet_summary()) == {"available": 1}
    assert [h.name for h in await env.svc.fleet.list_hosts()] == ["mac-1"]


async def test_sweep_marks_silent_devices_offline_and_hosts_disconnected(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(2)
    await env.svc.fleet.host_connected(host.id, "0.1.0")
    env.clock.advance(env.cfg.offline_after_s + 1)
    await env.beat(host, [devices[0].udid])
    result = await env.svc.fleet.sweep()
    assert result.devices_offline == 1
    states = {d.udid: d.state for d in (await env.svc.fleet.list_devices()).items}
    assert states == {devices[0].udid: "available", devices[1].udid: "offline"}

    env.clock.advance(env.cfg.offline_after_s + 1)
    assert (await env.svc.fleet.sweep()).hosts_disconnected == 1
    await env.svc.fleet.host_disconnected(host.id)


async def test_sweep_repairs_state_that_disagrees_with_leases(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    _, devices = await env.fleet(2)
    await env.template()
    await env.submit()
    await env.svc.scheduler.tick()
    leased = [d for d in (await env.svc.fleet.list_devices()).items if d.state == "leased"]
    assert len(leased) == 1

    # Corrupt both directions: a lease whose device claims to be available, and a device that
    # claims to be leased with no lease behind it.
    other = next(d for d in devices if d.id != leased[0].id)
    async with uow_factory() as uow:
        await uow.devices.update(leased[0].id, state="available")
        await uow.devices.update(other.id, state="busy")
        await uow.commit()
    result = await env.svc.fleet.sweep()
    assert result.repaired == 2
    states = {d.id: d.state for d in (await env.svc.fleet.list_devices()).items}
    assert states[leased[0].id] == "leased"
    assert states[other.id] == "available"


async def test_corrupt_health_quarantines_and_requeues(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    host, devices = await env.fleet(2)
    await env.template()
    job = await env.submit()
    await env.svc.scheduler.tick()
    items = await env.dispatch(host)
    victim_id = items[0].execution.device_id

    udid = next(d.udid for d in devices if d.id == victim_id)
    await env.svc.fleet.heartbeat(
        host, [HealthReport(udid=udid, battery_pct=400, thermal="banana")], []
    )
    victim = await env.device(victim_id)
    assert victim.state == "quarantined"
    assert "corrupt health" in (victim.quarantine_reason or "")
    requeued = await env.svc.jobs.get(job.id)
    assert requeued.state == "queued"
    assert victim_id in requeued.excluded_device_ids

    # A quarantined device ignores further corrupt reports and is never scheduled.
    await env.svc.fleet.heartbeat(host, [HealthReport(udid=udid, battery_pct=-5)], [])
    await env.svc.scheduler.tick()
    assert (await env.svc.jobs.get(job.id)).state == "leased"
    new_item = (await env.dispatch(host))[0]
    assert new_item.execution.device_id != victim_id


async def test_operator_actions_and_audit(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    _, devices = await env.fleet(1)
    fleet = env.svc.fleet
    d = devices[0]

    assert (await fleet.set_maintenance(ADMIN, d.id, True)).state == "maintenance"
    assert (await fleet.set_maintenance(ADMIN, d.id, False)).state == "available"
    assert (await fleet.quarantine(ADMIN, d.id, "cracked screen")).state == "quarantined"
    back = await fleet.return_to_service(ADMIN, d.id)
    assert back.state == "available" and back.quarantine_reason is None
    with pytest.raises(ConflictError):
        await fleet.return_to_service(ADMIN, d.id)

    labelled = await fleet.set_labels(ADMIN, d.id, ["  wifi-only ", "wifi-only", "dark-lab"])
    assert labelled.labels == ["dark-lab", "wifi-only"]
    with pytest.raises(ValidationFailedError):
        await fleet.set_labels(ADMIN, d.id, ["x" * 40])
    assert (await fleet.list_devices(label="wifi-only")).total == 1
    assert (await fleet.list_devices(label="nope")).total == 0

    with pytest.raises(NotFoundError):
        await fleet.quarantine(ADMIN, __import__("uuid").uuid4(), "x")
    with pytest.raises(NotFoundError):
        await fleet.get_device(__import__("uuid").uuid4())

    async with uow_factory() as uow:
        actions = [a.action for a in (await uow.audit.search(limit=100)).items]
    assert {"device.quarantined", "device.returned_to_service", "device.maintenance"} <= set(
        actions
    )


async def test_busy_device_cannot_be_quarantined_or_parked(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    _, devices = await env.fleet(1)
    await env.template()
    await env.submit()
    await env.svc.scheduler.tick()
    for action in (
        env.svc.fleet.quarantine(Actor("op", Role.OPERATOR), devices[0].id, "x"),
        env.svc.fleet.set_maintenance(ADMIN, devices[0].id, True),
    ):
        with pytest.raises(ConflictError) as exc:
            await action
        assert exc.value.code == "device_in_use"


async def test_offline_device_moves_to_maintenance_then_back(uow_factory) -> None:  # type: ignore[no-untyped-def]
    env = make_env(uow_factory)
    _, devices = await env.fleet(1)
    env.clock.advance(env.cfg.offline_after_s + 1)
    await env.svc.fleet.sweep()
    assert (await env.device(devices[0].id)).state == DeviceState.OFFLINE.value
    returned = await env.svc.fleet.return_to_service(ADMIN, devices[0].id)
    assert returned.state == "offline"  # stale heartbeat: not made schedulable on faith
