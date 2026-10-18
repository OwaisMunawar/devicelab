from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from devicelab.api.deps import Admin, Operator, Paging, ServicesDep, Viewer
from devicelab.api.schemas import (
    AlertOut,
    AlertRuleOut,
    AlertRulePatch,
    ApiKeyCreatedOut,
    ApiKeyIn,
    ApiKeyOut,
    AuditOut,
    PageOut,
    WebhookCreatedOut,
    WebhookIn,
    WebhookOut,
)
from devicelab.api.sse import event_stream
from devicelab.domain.models import Role

router = APIRouter(tags=["operations"])


# -- alerts ----------------------------------------------------------------------------


@router.get("/v1/alerts", response_model=PageOut[AlertOut])
async def list_alerts(
    services: ServicesDep,
    _: Viewer,
    paging: Paging,
    state: Annotated[str | None, Query(pattern="^(open|resolved)$")] = None,
) -> PageOut[AlertOut]:
    page = await services.alerts.list_alerts(state=state, limit=paging.limit, offset=paging.offset)
    return PageOut.of(page, AlertOut)


@router.post("/v1/alerts/{alert_id}/acknowledge", response_model=AlertOut)
async def acknowledge_alert(alert_id: UUID, services: ServicesDep, principal: Operator) -> AlertOut:
    return AlertOut.model_validate(await services.alerts.acknowledge(principal.actor, alert_id))


@router.get("/v1/alert-rules", response_model=list[AlertRuleOut])
async def list_rules(services: ServicesDep, _: Viewer) -> list[AlertRuleOut]:
    return [AlertRuleOut.model_validate(r) for r in await services.alerts.list_rules()]


@router.patch("/v1/alert-rules/{rule_id}", response_model=AlertRuleOut)
async def patch_rule(
    rule_id: UUID, body: AlertRulePatch, services: ServicesDep, principal: Admin
) -> AlertRuleOut:
    rule = await services.alerts.update_rule(
        principal.actor,
        rule_id,
        enabled=body.enabled,
        params=body.params,
        cooldown_s=body.cooldown_s,
    )
    return AlertRuleOut.model_validate(rule)


@router.get("/v1/webhooks", response_model=list[WebhookOut])
async def list_webhooks(services: ServicesDep, _: Admin) -> list[WebhookOut]:
    return [WebhookOut.model_validate(w) for w in await services.alerts.list_webhooks()]


@router.post("/v1/webhooks", response_model=WebhookCreatedOut, status_code=201)
async def create_webhook(
    body: WebhookIn, services: ServicesDep, principal: Admin
) -> WebhookCreatedOut:
    return WebhookCreatedOut.model_validate(
        await services.alerts.create_webhook(principal.actor, body.name, body.url)
    )


@router.delete("/v1/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(webhook_id: UUID, services: ServicesDep, principal: Admin) -> Response:
    await services.alerts.delete_webhook(principal.actor, webhook_id)
    return Response(status_code=204)


# -- reports ---------------------------------------------------------------------------


@router.get("/v1/reports/daily")
async def daily_report(
    services: ServicesDep, _: Viewer, day: Annotated[date | None, Query(alias="date")] = None
) -> dict[str, Any]:
    return await services.reports.daily_summary(day)


@router.get("/v1/reports/executions.csv")
async def executions_csv(
    services: ServicesDep,
    _: Viewer,
    start: Annotated[datetime | None, Query()] = None,
    end: Annotated[datetime | None, Query()] = None,
) -> Response:
    end = end or datetime.now(UTC)
    start = start or end - timedelta(days=1)
    body = await services.reports.export_csv(start, end)
    return Response(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="executions.csv"'},
    )


# -- keys and audit --------------------------------------------------------------------


@router.get("/v1/api-keys", response_model=list[ApiKeyOut])
async def list_keys(services: ServicesDep, _: Admin) -> list[ApiKeyOut]:
    return [ApiKeyOut.model_validate(k) for k in await services.auth.list()]


@router.post("/v1/api-keys", response_model=ApiKeyCreatedOut, status_code=201)
async def create_key(body: ApiKeyIn, services: ServicesDep, principal: Admin) -> ApiKeyCreatedOut:
    secret, record = await services.auth.create_key(principal.actor, body.name, Role(body.role))
    return ApiKeyCreatedOut(**ApiKeyOut.model_validate(record).model_dump(), secret=secret)


@router.delete("/v1/api-keys/{key_id}", status_code=204)
async def revoke_key(key_id: UUID, services: ServicesDep, principal: Admin) -> Response:
    await services.auth.revoke(principal.actor, key_id)
    return Response(status_code=204)


@router.get("/v1/audit", response_model=PageOut[AuditOut])
async def audit_log(
    services: ServicesDep,
    _: Admin,
    paging: Paging,
    action: Annotated[str | None, Query()] = None,
    actor: Annotated[str | None, Query()] = None,
) -> PageOut[AuditOut]:
    page = await services.reports.audit(
        action=action, actor=actor, limit=paging.limit, offset=paging.offset
    )
    return PageOut.of(page, AuditOut)


# -- live events -----------------------------------------------------------------------


@router.get("/v1/events")
async def events(request: Request, _: Viewer) -> StreamingResponse:
    return StreamingResponse(
        event_stream(request.app.state.bus, is_closed=request.is_disconnected),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# -- health and metrics ----------------------------------------------------------------


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request, services: ServicesDep) -> dict[str, str]:
    await services.fleet.fleet_summary()
    return {"status": "ready"}


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    return Response(
        generate_latest(request.app.state.metrics.registry), media_type=CONTENT_TYPE_LATEST
    )
