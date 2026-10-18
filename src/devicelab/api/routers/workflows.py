from __future__ import annotations

from dataclasses import asdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from devicelab.api.deps import Operator, Paging, ServicesDep, Viewer
from devicelab.api.schemas import (
    ArtifactOut,
    ExecutionDetailOut,
    ExecutionOut,
    JobDetailOut,
    JobIn,
    JobOut,
    LogOut,
    PageOut,
    ScheduleIn,
    ScheduleOut,
    StepOut,
    TemplateBody,
    TemplateOut,
    TemplateReportOut,
)
from devicelab.api.sse import event_stream
from devicelab.application.jobs import SubmitJob, TemplateService
from devicelab.domain.errors import ValidationFailedError

router = APIRouter(prefix="/v1", tags=["workflows"])

_SAFE_INLINE = {"image/png", "image/jpeg", "text/plain", "application/json"}


# -- templates -------------------------------------------------------------------------


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(services: ServicesDep, _: Viewer) -> list[TemplateOut]:
    return [TemplateOut.model_validate(t) for t in await services.templates.list()]


@router.post("/templates/validate", response_model=TemplateReportOut)
async def validate_template(body: TemplateBody, _: Viewer) -> TemplateReportOut:
    report = TemplateService.validate(body.content)
    return TemplateReportOut(**asdict(report))


@router.get("/templates/{name}", response_model=TemplateOut)
async def get_template(name: str, services: ServicesDep, _: Viewer) -> TemplateOut:
    return TemplateOut.model_validate(await services.templates.get(name))


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(
    body: TemplateBody, services: ServicesDep, principal: Operator
) -> TemplateOut:
    return TemplateOut.model_validate(
        await services.templates.create(principal.actor, body.content)
    )


@router.put("/templates/{name}", response_model=TemplateOut)
async def update_template(
    name: str, body: TemplateBody, services: ServicesDep, principal: Operator
) -> TemplateOut:
    return TemplateOut.model_validate(
        await services.templates.update(principal.actor, name, body.content)
    )


@router.delete("/templates/{name}", status_code=204)
async def delete_template(name: str, services: ServicesDep, principal: Operator) -> Response:
    await services.templates.delete(principal.actor, name)
    return Response(status_code=204)


# -- jobs ------------------------------------------------------------------------------


@router.post("/jobs", response_model=JobOut, status_code=201)
async def submit_job(
    body: JobIn,
    request: Request,
    response: Response,
    services: ServicesDep,
    principal: Operator,
    idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
) -> JobOut:
    requirement = body.requirement.model_dump(exclude_none=True) if body.requirement else None
    job, created = await services.jobs.submit(
        principal.actor,
        SubmitJob(
            template=body.template,
            params=body.params,
            priority=body.priority,
            requirement=requirement,
            idempotency_key=idempotency_key,
            max_attempts=body.max_attempts,
            correlation_id=getattr(request.state, "correlation_id", None),
        ),
    )
    if not created:
        response.status_code = 200
        response.headers["Idempotent-Replay"] = "true"
    return JobOut.model_validate(job)


@router.get("/jobs", response_model=PageOut[JobOut])
async def list_jobs(
    services: ServicesDep,
    _: Viewer,
    paging: Paging,
    state: Annotated[str | None, Query()] = None,
    template: Annotated[str | None, Query()] = None,
) -> PageOut[JobOut]:
    page = await services.jobs.search(
        state=state, template_name=template, limit=paging.limit, offset=paging.offset
    )
    return PageOut.of(page, JobOut)


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
async def get_job(job_id: UUID, services: ServicesDep, _: Viewer) -> JobDetailOut:
    job = await services.jobs.get(job_id)
    executions = await services.executions.search(job_id=job_id, limit=200)
    return JobDetailOut(
        **JobOut.model_validate(job).model_dump(),
        executions=[ExecutionOut.model_validate(e) for e in executions.items],
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: UUID, services: ServicesDep, principal: Operator) -> JobOut:
    return JobOut.model_validate(await services.jobs.cancel(principal.actor, job_id))


# -- executions ------------------------------------------------------------------------


@router.get("/executions", response_model=PageOut[ExecutionOut])
async def list_executions(
    services: ServicesDep,
    _: Viewer,
    paging: Paging,
    state: Annotated[str | None, Query()] = None,
    job_id: Annotated[UUID | None, Query()] = None,
    device_id: Annotated[UUID | None, Query()] = None,
) -> PageOut[ExecutionOut]:
    page = await services.executions.search(
        job_id=job_id, device_id=device_id, state=state, limit=paging.limit, offset=paging.offset
    )
    return PageOut.of(page, ExecutionOut)


@router.get("/executions/{execution_id}", response_model=ExecutionDetailOut)
async def get_execution(execution_id: UUID, services: ServicesDep, _: Viewer) -> ExecutionDetailOut:
    execution, steps, artifacts = await services.executions.get(execution_id)
    return ExecutionDetailOut(
        **ExecutionOut.model_validate(execution).model_dump(),
        steps=[StepOut.model_validate(s) for s in steps],
        artifacts=[ArtifactOut.model_validate(a) for a in artifacts],
    )


@router.get("/executions/{execution_id}/logs", response_model=list[LogOut])
async def execution_logs(
    execution_id: UUID,
    services: ServicesDep,
    _: Viewer,
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> list[LogOut]:
    return [
        LogOut.model_validate(x)
        for x in await services.executions.logs(execution_id, after_id, limit)
    ]


@router.get("/executions/{execution_id}/logs/stream")
async def stream_execution_logs(
    execution_id: UUID, request: Request, services: ServicesDep, _: Viewer
) -> StreamingResponse:
    """Replay what is stored, then follow live. Ids let a reconnecting client resume."""
    await services.executions.get(execution_id)
    backlog = await services.executions.logs(execution_id, 0, 2000)
    return StreamingResponse(
        event_stream(
            request.app.state.bus,
            topics={"execution.log"},
            match=lambda data: data.get("execution_id") == str(execution_id),
            backlog=[
                (
                    "execution.log",
                    {
                        "id": x.id,
                        "level": x.level,
                        "message": x.message,
                        "ts": x.ts.isoformat(),
                        "step_index": x.step_index,
                    },
                )
                for x in backlog
            ],
            is_closed=request.is_disconnected,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/artifacts/{artifact_id}/content")
async def artifact_content(artifact_id: UUID, services: ServicesDep, _: Viewer) -> Response:
    record, data = await services.executions.artifact_content(artifact_id)
    media = (
        record.content_type if record.content_type in _SAFE_INLINE else "application/octet-stream"
    )
    return Response(
        data,
        media_type=media,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


# -- schedules -------------------------------------------------------------------------


@router.get("/schedules", response_model=list[ScheduleOut])
async def list_schedules(services: ServicesDep, _: Viewer) -> list[ScheduleOut]:
    return [ScheduleOut.model_validate(s) for s in await services.schedules.list()]


@router.post("/schedules", response_model=ScheduleOut, status_code=201)
async def create_schedule(
    body: ScheduleIn, services: ServicesDep, principal: Operator
) -> ScheduleOut:
    if body.cron.count(" ") != 4:
        raise ValidationFailedError("cron needs five fields", code="invalid_cron")
    record = await services.schedules.create(
        principal.actor,
        name=body.name,
        template_name=body.template,
        cron=body.cron,
        timezone=body.timezone,
        params=body.params,
        requirement=body.requirement.model_dump(exclude_none=True) if body.requirement else None,
        priority=body.priority,
        misfire_policy=body.misfire_policy,
        enabled=body.enabled,
    )
    return ScheduleOut.model_validate(record)


@router.post("/schedules/{schedule_id}/enable", response_model=ScheduleOut)
async def enable_schedule(
    schedule_id: UUID, services: ServicesDep, principal: Operator
) -> ScheduleOut:
    return ScheduleOut.model_validate(
        await services.schedules.set_enabled(principal.actor, schedule_id, True)
    )


@router.post("/schedules/{schedule_id}/disable", response_model=ScheduleOut)
async def disable_schedule(
    schedule_id: UUID, services: ServicesDep, principal: Operator
) -> ScheduleOut:
    return ScheduleOut.model_validate(
        await services.schedules.set_enabled(principal.actor, schedule_id, False)
    )


@router.delete("/schedules/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: UUID, services: ServicesDep, principal: Operator
) -> Response:
    await services.schedules.delete(principal.actor, schedule_id)
    return Response(status_code=204)
