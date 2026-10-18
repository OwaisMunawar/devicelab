"""Hosts, devices, enrolment and health."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID

from devicelab.application.config import ControlPlaneConfig
from devicelab.application.lifecycle import IN_USE, Lifecycle
from devicelab.application.ports import Clock, EventPublisher, IdFactory, UnitOfWork, UowFactory
from devicelab.application.records import (
    DeviceRecord,
    EnrolmentTokenRecord,
    HealthSampleRecord,
    HostRecord,
    Page,
)
from devicelab.application.support import SYSTEM, Actor, audit, emit
from devicelab.application.tokens import generate_token, hash_secret
from devicelab.domain.errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationFailedError,
)
from devicelab.domain.failures import FailureClass
from devicelab.domain.health import HealthStatus, assess_health
from devicelab.domain.models import DeviceState
from devicelab.protocol.messages import ActiveExecution, DeviceReport, HealthReport


@dataclass(frozen=True, slots=True)
class HeartbeatResult:
    orphans: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SweepResult:
    devices_offline: int = 0
    hosts_disconnected: int = 0
    repaired: int = 0


class FleetService:
    def __init__(
        self,
        uow_factory: UowFactory,
        clock: Clock,
        ids: IdFactory,
        config: ControlPlaneConfig,
        lifecycle: Lifecycle,
        events: EventPublisher,
    ) -> None:
        self._uow = uow_factory
        self._clock = clock
        self._ids = ids
        self._cfg = config
        self._life = lifecycle
        self._events = events

    # -- enrolment --------------------------------------------------------------------

    async def create_enrolment_token(
        self,
        actor: Actor,
        *,
        host_name: str,
        site: str,
        region: str = "default",
        max_concurrency: int = 4,
        ttl_s: float | None = None,
    ) -> tuple[str, EnrolmentTokenRecord]:
        if not 1 <= max_concurrency <= 64:
            raise ValidationFailedError("max_concurrency must be 1..64", code="invalid_concurrency")
        now = self._clock.now()
        secret, _ = generate_token("e")
        record = EnrolmentTokenRecord(
            id=self._ids(),
            token_hash=hash_secret(secret),
            host_name=host_name,
            site=site,
            region=region,
            max_concurrency=max_concurrency,
            expires_at=now + timedelta(seconds=ttl_s or self._cfg.enrolment_ttl_s),
            created_at=now,
            created_by=actor.name,
        )
        async with self._uow() as uow:
            await uow.enrolments.add(record)
            await audit(uow, actor, "enrolment.created", "host", host_name, now, site=site)
            await uow.commit()
        return secret, record

    async def enrol(self, token: str, host_name: str) -> tuple[HostRecord, str]:
        now = self._clock.now()
        async with self._uow() as uow:
            record = await uow.enrolments.get_by_hash(hash_secret(token))
            if record is None:
                raise UnauthorizedError("unknown enrolment token", code="invalid_enrolment_token")
            if record.used_at is not None:
                raise UnauthorizedError("enrolment token already used", code="enrolment_used")
            if record.expires_at <= now:
                raise UnauthorizedError("enrolment token expired", code="enrolment_expired")
            if record.host_name != host_name:
                raise UnauthorizedError("token was issued for another host", code="enrolment_host")
            if not await uow.enrolments.consume(record.id, now):
                raise UnauthorizedError("enrolment token already used", code="enrolment_used")

            credential, _ = generate_token("a")
            existing = await uow.hosts.get_by_name(host_name)
            if existing is not None:
                if existing.credential_hash is not None:
                    raise ConflictError("host is already enrolled", code="host_enrolled")
                await uow.hosts.update(existing.id, credential_hash=hash_secret(credential))
                host = existing
            else:
                host = HostRecord(
                    id=self._ids(),
                    name=host_name,
                    site=record.site,
                    region=record.region,
                    max_concurrency=record.max_concurrency,
                    created_at=now,
                    credential_hash=hash_secret(credential),
                )
                await uow.hosts.add(host)
            await audit(uow, SYSTEM, "host.enrolled", "host", host.id, now, name=host_name)
            await uow.commit()
        return host, credential

    async def authenticate_host(self, credential: str) -> HostRecord:
        async with self._uow() as uow:
            host = await uow.hosts.get_by_credential_hash(hash_secret(credential))
        if host is None:
            raise UnauthorizedError("unknown agent credential", code="invalid_credential")
        return host

    async def host_connected(self, host_id: UUID, agent_version: str) -> None:
        async with self._uow() as uow:
            await uow.hosts.update(
                host_id,
                connected=True,
                last_seen=self._clock.now(),
                agent_version=agent_version,
            )
            emit(uow, self._events, "host.updated", id=str(host_id), connected=True)
            await uow.commit()

    async def host_disconnected(self, host_id: UUID) -> None:
        async with self._uow() as uow:
            await uow.hosts.update(host_id, connected=False)
            emit(uow, self._events, "host.updated", id=str(host_id), connected=False)
            await uow.commit()

    # -- inventory and health ---------------------------------------------------------

    async def register_inventory(self, host: HostRecord, reports: Sequence[DeviceReport]) -> None:
        now = self._clock.now()
        corrupt: list[tuple[UUID, Sequence[str]]] = []
        async with self._uow() as uow:
            known = {d.udid: d for d in await uow.devices.list_for_host(host.id)}
            reported: set[str] = set()
            # Device rows are always locked in id order (see Lifecycle.lock_execution).
            pairs = [
                (r, known.get(r.udid) or await uow.devices.get_by_udid(r.udid)) for r in reports
            ]
            pairs.sort(key=lambda p: (p[1] is None, p[1].id.int if p[1] else 0))
            for report, found in pairs:
                reported.add(report.udid)
                existing = found
                if existing is None:
                    connected = report.health.connectivity == "connected"
                    existing = DeviceRecord(
                        id=self._ids(),
                        host_id=host.id,
                        udid=report.udid,
                        name=report.name,
                        model=report.model,
                        model_family=report.model_family,
                        ios_version=report.ios_version,
                        kind=report.kind,
                        site=host.site,
                        region=host.region,
                        state=(DeviceState.AVAILABLE if connected else DeviceState.OFFLINE).value,
                        created_at=now,
                        labels=list(report.labels),
                        state_changed_at=now,
                    )
                    await uow.devices.add(existing)
                else:
                    await uow.devices.update(
                        existing.id,
                        host_id=host.id,
                        site=host.site,
                        region=host.region,
                        name=report.name,
                        model=report.model,
                        model_family=report.model_family,
                        ios_version=report.ios_version,
                    )
                assessment = assess_health(
                    report.health.battery_pct, report.health.thermal, report.health.storage_free_gb
                )
                if assessment.status is HealthStatus.CORRUPT:
                    corrupt.append((existing.id, assessment.reasons))
                else:
                    await self._apply_health(uow, existing, report.health, now)
            for udid, device in known.items():
                if udid not in reported and device.state == DeviceState.AVAILABLE.value:
                    await self._life.set_device_state(uow, device, DeviceState.OFFLINE)
            await uow.commit()
        await self._quarantine_corrupt(corrupt)

    async def heartbeat(
        self,
        host: HostRecord,
        health: Sequence[HealthReport],
        executions: Sequence[ActiveExecution],
    ) -> HeartbeatResult:
        """Three short transactions rather than one long one: lease renewals, then healthy
        devices in id order, then each corrupt device on its own. That keeps lock order
        uniform across the system (job, execution, lease, device) and each hold brief."""
        now = self._clock.now()
        orphans: list[str] = []
        async with self._uow() as uow:
            for ref in sorted(executions, key=lambda r: r.lease_id):
                if not await self._renew(uow, host.id, ref, now):
                    orphans.append(ref.execution_id)
            await uow.commit()

        corrupt: list[tuple[UUID, Sequence[str]]] = []
        async with self._uow() as uow:
            devices = await uow.devices.list_for_host(host.id)  # ordered by id
            by_udid = {d.udid: d for d in devices}
            wanted = {r.udid: r for r in health if r.udid in by_udid}
            for device in devices:
                report = wanted.get(device.udid)
                if report is None:
                    continue
                assessment = assess_health(
                    report.battery_pct, report.thermal, report.storage_free_gb
                )
                if assessment.status is HealthStatus.CORRUPT:
                    corrupt.append((device.id, assessment.reasons))
                else:
                    await self._apply_health(uow, device, report, now)
            await uow.hosts.update(host.id, last_seen=now, connected=True)
            await uow.commit()
        await self._quarantine_corrupt(corrupt)
        return HeartbeatResult(orphans)

    async def _renew(
        self, uow: UnitOfWork, host_id: UUID, ref: ActiveExecution, now: datetime
    ) -> bool:
        try:
            lease = await uow.leases.get(UUID(ref.lease_id), for_update=True)
        except ValueError:
            return False
        if (
            lease is None
            or lease.state != "active"
            or lease.host_id != host_id
            or lease.fencing_token != ref.fencing_token
        ):
            return False
        # Bounded to one TTL from now; a stuck agent cannot pre-pay a long lease.
        await uow.leases.update(
            lease.id,
            expires_at=max(lease.expires_at, now + timedelta(seconds=self._cfg.lease_ttl_s)),
        )
        return True

    async def _apply_health(
        self, uow: UnitOfWork, device: DeviceRecord, report: HealthReport, now: datetime
    ) -> None:
        changes: dict[str, object] = {
            "last_seen": now,
            "battery_pct": report.battery_pct,
            "thermal": report.thermal,
            "storage_free_gb": report.storage_free_gb,
            "connectivity": report.connectivity,
        }
        state = DeviceState(device.state)
        reachable = report.connectivity == "connected"
        if state is DeviceState.OFFLINE and reachable:
            await self._life.set_device_state(uow, device, DeviceState.AVAILABLE)
        elif state is DeviceState.AVAILABLE and not reachable:
            await self._life.set_device_state(uow, device, DeviceState.OFFLINE)

        due = device.last_sample_at is None or (
            (now - device.last_sample_at).total_seconds() >= self._cfg.sample_interval_s
        )
        moved = (
            device.battery_pct is not None
            and report.battery_pct is not None
            and abs(device.battery_pct - report.battery_pct) >= self._cfg.sample_battery_delta
        )
        if (
            due
            or moved
            or device.thermal != report.thermal
            or device.connectivity != report.connectivity
        ):
            changes["last_sample_at"] = now
            await uow.devices.add_sample(
                HealthSampleRecord(
                    device_id=device.id,
                    ts=now,
                    state=device.state,
                    thermal=report.thermal,
                    connectivity=report.connectivity,
                    battery_pct=report.battery_pct,
                )
            )
        await uow.devices.update(device.id, **changes)
        emit(uow, self._events, "device.health", id=str(device.id))

    async def _quarantine_corrupt(self, corrupt: Sequence[tuple[UUID, Sequence[str]]]) -> None:
        for device_id, reasons in corrupt:
            async with self._uow() as uow:
                device = await uow.devices.get(device_id)
                if device is None or device.state == DeviceState.QUARANTINED.value:
                    continue
                lease = await uow.leases.get_active_for_device(device_id)
                if lease is not None:
                    execution = await uow.executions.get_by_lease(lease.id)
                    if execution is not None:
                        await self._life.fail_execution(
                            uow,
                            execution.id,
                            failure_class=FailureClass.DEVICE_UNREACHABLE,
                            error="device reported corrupt health: " + "; ".join(reasons),
                            lost=True,
                        )
                fresh = await uow.devices.get(device_id, for_update=True)
                if fresh is not None and fresh.state != DeviceState.QUARANTINED.value:
                    await self._life.quarantine_device(
                        uow, fresh, "corrupt health report: " + "; ".join(reasons)
                    )
                await uow.commit()

    # -- background maintenance -------------------------------------------------------

    async def sweep(self) -> SweepResult:
        """Mark silent devices offline and repair device/lease disagreements."""
        now = self._clock.now()
        cutoff = now - timedelta(seconds=self._cfg.offline_after_s)
        offline = disconnected = repaired = 0
        async with self._uow() as uow:
            for host in await uow.hosts.list_all():
                if host.connected and (host.last_seen is None or host.last_seen < cutoff):
                    await uow.hosts.update(host.id, connected=False)
                    emit(uow, self._events, "host.updated", id=str(host.id), connected=False)
                    disconnected += 1

            states = [s.value for s in DeviceState if s is not DeviceState.QUARANTINED]
            for candidate in await uow.devices.list_in_states(states):
                # The listing above is only a hint. Other transactions grant and release
                # leases all the time, so decide on rows we hold locked and have re-read.
                device = await uow.devices.get(candidate.id, for_update=True)
                if device is None:
                    continue
                lease = await uow.leases.get_active_for_device(device.id)
                stale = device.last_seen is None or device.last_seen < cutoff
                state = DeviceState(device.state)
                if state is DeviceState.AVAILABLE and lease is None and stale:
                    await self._life.set_device_state(uow, device, DeviceState.OFFLINE)
                    offline += 1
                elif device.state in IN_USE and lease is None:
                    target = DeviceState.OFFLINE if stale else DeviceState.AVAILABLE
                    await self._life.set_device_state(uow, device, target)
                    repaired += 1
                elif lease is not None and state in (DeviceState.AVAILABLE, DeviceState.OFFLINE):
                    await self._life.set_device_state(uow, device, DeviceState.LEASED)
                    repaired += 1
            await uow.commit()
        return SweepResult(offline, disconnected, repaired)

    # -- operator actions -------------------------------------------------------------

    async def quarantine(self, actor: Actor, device_id: UUID, reason: str) -> DeviceRecord:
        async with self._uow() as uow:
            device = await self._get_for_update(uow, device_id)
            if device.state in IN_USE:
                raise ConflictError("device is running a job", code="device_in_use")
            await self._life.quarantine_device(uow, device, reason or "operator action", actor)
            await uow.commit()
        return await self.get_device(device_id)

    async def return_to_service(self, actor: Actor, device_id: UUID) -> DeviceRecord:
        now = self._clock.now()
        async with self._uow() as uow:
            device = await self._get_for_update(uow, device_id)
            if device.state not in (
                DeviceState.QUARANTINED.value,
                DeviceState.MAINTENANCE.value,
                DeviceState.OFFLINE.value,
            ):
                raise ConflictError(
                    "device is not out of service", code="device_not_out_of_service"
                )
            fresh = (
                device.last_seen is not None
                and (now - device.last_seen).total_seconds() <= self._cfg.offline_after_s
            )
            target = DeviceState.AVAILABLE if fresh else DeviceState.OFFLINE
            await self._life.set_device_state(
                uow, device, target, consecutive_infra_failures=0, quarantine_reason=None
            )
            await audit(uow, actor, "device.returned_to_service", "device", device_id, now)
            await uow.commit()
        return await self.get_device(device_id)

    async def set_maintenance(self, actor: Actor, device_id: UUID, enabled: bool) -> DeviceRecord:
        now = self._clock.now()
        async with self._uow() as uow:
            device = await self._get_for_update(uow, device_id)
            if enabled:
                if device.state in IN_USE:
                    raise ConflictError("device is running a job", code="device_in_use")
                await self._life.set_device_state(uow, device, DeviceState.MAINTENANCE)
            else:
                await self._life.set_device_state(uow, device, DeviceState.AVAILABLE)
            await audit(uow, actor, "device.maintenance", "device", device_id, now, enabled=enabled)
            await uow.commit()
        return await self.get_device(device_id)

    async def set_labels(self, actor: Actor, device_id: UUID, labels: list[str]) -> DeviceRecord:
        clean = sorted({label.strip() for label in labels if label.strip()})
        if len(clean) > 16 or any(len(label) > 32 for label in clean):
            raise ValidationFailedError("up to 16 labels of 32 chars", code="invalid_labels")
        async with self._uow() as uow:
            await self._get_for_update(uow, device_id)
            await uow.devices.update(device_id, labels=clean)
            await audit(
                uow, actor, "device.labels", "device", device_id, self._clock.now(), labels=clean
            )
            emit(uow, self._events, "device.updated", id=str(device_id))
            await uow.commit()
        return await self.get_device(device_id)

    async def _get_for_update(self, uow: UnitOfWork, device_id: UUID) -> DeviceRecord:
        device = await uow.devices.get(device_id, for_update=True)
        if device is None:
            raise NotFoundError("device not found", code="device_not_found")
        return device

    # -- reads ------------------------------------------------------------------------

    async def get_device(self, device_id: UUID) -> DeviceRecord:
        async with self._uow() as uow:
            device = await uow.devices.get(device_id)
        if device is None:
            raise NotFoundError("device not found", code="device_not_found")
        return device

    async def list_devices(
        self,
        *,
        state: str | None = None,
        site: str | None = None,
        label: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[DeviceRecord]:
        async with self._uow() as uow:
            return await uow.devices.search(
                state=state, site=site, label=label, limit=limit, offset=offset
            )

    async def list_hosts(self) -> list[HostRecord]:
        async with self._uow() as uow:
            return await uow.hosts.list_all()

    async def device_timeline(
        self, device_id: UUID, *, hours: int = 24, limit: int = 500
    ) -> list[HealthSampleRecord]:
        since = self._clock.now() - timedelta(hours=hours)
        async with self._uow() as uow:
            return await uow.devices.list_samples(device_id, since, limit)

    async def fleet_summary(self) -> dict[str, int]:
        async with self._uow() as uow:
            return await uow.devices.count_by_state()
