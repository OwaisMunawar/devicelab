from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request

from devicelab.api.deps import Admin, Operator, Paging, ServicesDep, Viewer
from devicelab.api.schemas import (
    DeviceOut,
    EnrolmentIn,
    EnrolmentOut,
    FleetSummaryOut,
    HealthSampleOut,
    HostOut,
    LabelsIn,
    MaintenanceIn,
    PageOut,
    ReasonIn,
)

router = APIRouter(prefix="/v1", tags=["fleet"])


@router.get("/fleet/summary", response_model=FleetSummaryOut)
async def fleet_summary(services: ServicesDep, _: Viewer) -> FleetSummaryOut:
    hosts = await services.fleet.list_hosts()
    jobs = await services.jobs.counts()
    return FleetSummaryOut(
        devices=await services.fleet.fleet_summary(),
        jobs=jobs,
        hosts_total=len(hosts),
        hosts_connected=sum(1 for h in hosts if h.connected),
        leases_active=await services.scheduler.active_leases(),
        queue_depth=jobs.get("queued", 0),
        sites=sorted({h.site for h in hosts}),
    )


@router.get("/hosts", response_model=list[HostOut])
async def list_hosts(services: ServicesDep, _: Viewer) -> list[HostOut]:
    return [HostOut.model_validate(h) for h in await services.fleet.list_hosts()]


@router.post("/enrolment-tokens", response_model=EnrolmentOut, status_code=201)
async def create_enrolment_token(
    body: EnrolmentIn, request: Request, services: ServicesDep, principal: Admin
) -> EnrolmentOut:
    token, record = await services.fleet.create_enrolment_token(
        principal.actor,
        host_name=body.host_name,
        site=body.site,
        region=body.region,
        max_concurrency=body.max_concurrency,
    )
    base = str(request.base_url).rstrip("/").replace("http", "ws", 1)
    return EnrolmentOut(
        token=token,
        host_name=record.host_name,
        expires_at=record.expires_at,
        command=(
            f"devicelab-agent run --server {base}/v1/agent/ws "
            f"--host-name {record.host_name} --enrol-token {token}"
        ),
    )


@router.get("/devices", response_model=PageOut[DeviceOut])
async def list_devices(
    services: ServicesDep,
    _: Viewer,
    paging: Paging,
    state: Annotated[str | None, Query()] = None,
    site: Annotated[str | None, Query()] = None,
    label: Annotated[str | None, Query()] = None,
) -> PageOut[DeviceOut]:
    page = await services.fleet.list_devices(
        state=state, site=site, label=label, limit=paging.limit, offset=paging.offset
    )
    return PageOut.of(page, DeviceOut)


@router.get("/devices/{device_id}", response_model=DeviceOut)
async def get_device(device_id: UUID, services: ServicesDep, _: Viewer) -> DeviceOut:
    return DeviceOut.model_validate(await services.fleet.get_device(device_id))


@router.get("/devices/{device_id}/timeline", response_model=list[HealthSampleOut])
async def device_timeline(
    device_id: UUID,
    services: ServicesDep,
    _: Viewer,
    hours: Annotated[int, Query(ge=1, le=24 * 14)] = 24,
) -> list[HealthSampleOut]:
    samples = await services.fleet.device_timeline(device_id, hours=hours)
    return [HealthSampleOut.model_validate(s) for s in samples]


@router.post("/devices/{device_id}/quarantine", response_model=DeviceOut)
async def quarantine(
    device_id: UUID, body: ReasonIn, services: ServicesDep, principal: Operator
) -> DeviceOut:
    return DeviceOut.model_validate(
        await services.fleet.quarantine(principal.actor, device_id, body.reason)
    )


@router.post("/devices/{device_id}/return-to-service", response_model=DeviceOut)
async def return_to_service(
    device_id: UUID, services: ServicesDep, principal: Operator
) -> DeviceOut:
    return DeviceOut.model_validate(
        await services.fleet.return_to_service(principal.actor, device_id)
    )


@router.post("/devices/{device_id}/maintenance", response_model=DeviceOut)
async def maintenance(
    device_id: UUID, body: MaintenanceIn, services: ServicesDep, principal: Operator
) -> DeviceOut:
    return DeviceOut.model_validate(
        await services.fleet.set_maintenance(principal.actor, device_id, body.enabled)
    )


@router.put("/devices/{device_id}/labels", response_model=DeviceOut)
async def set_labels(
    device_id: UUID, body: LabelsIn, services: ServicesDep, principal: Operator
) -> DeviceOut:
    return DeviceOut.model_validate(
        await services.fleet.set_labels(principal.actor, device_id, body.labels)
    )
