from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import websockets

from tests.live import Live

TEMPLATE = """
name: rest-flow
steps:
  - {id: open, type: launch_app, bundle_id: com.apple.Preferences}
  - {id: shot, type: screenshot}
"""


async def make_key(live: Live, name: str, role: str) -> httpx.AsyncClient:
    response = await live.client.post("/v1/api-keys", json={"name": name, "role": role})
    assert response.status_code == 201, response.text
    return httpx.AsyncClient(
        base_url=live.url, headers={"Authorization": f"Bearer {response.json()['secret']}"}
    )


async def test_requests_need_a_valid_key(live: Live) -> None:
    anonymous = httpx.AsyncClient(base_url=live.url)
    missing = await anonymous.get("/v1/devices")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    body = missing.json()["error"]
    assert body["code"] == "missing_api_key" and body["correlation_id"]

    wrong = await anonymous.get("/v1/devices", headers={"Authorization": "Bearer dlk_nope"})
    assert wrong.json()["error"]["code"] == "invalid_api_key"
    ok = await live.client.get("/v1/devices", headers={"X-Correlation-ID": "trace-123"})
    assert ok.status_code == 200 and ok.headers["x-correlation-id"] == "trace-123"
    assert (await anonymous.get("/healthz")).json() == {"status": "ok"}
    assert (await anonymous.get("/readyz")).status_code == 200


async def test_roles_are_enforced(live: Live) -> None:
    viewer = await make_key(live, "v1", "viewer")
    operator = await make_key(live, "op1", "operator")
    assert (await viewer.get("/v1/templates")).status_code == 200
    denied = await viewer.post("/v1/templates", json={"content": TEMPLATE})
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "forbidden"
    assert (await operator.post("/v1/templates", json={"content": TEMPLATE})).status_code == 201
    assert (await operator.get("/v1/api-keys")).status_code == 403
    assert (await viewer.get("/v1/audit")).status_code == 403
    assert (await live.client.get("/v1/audit")).status_code == 200


async def test_api_keys_are_hashed_and_secret_shown_once(live: Live) -> None:
    created = (await live.client.post("/v1/api-keys", json={"name": "ci", "role": "viewer"})).json()
    assert created["secret"].startswith("dlk_")
    listed = (await live.client.get("/v1/api-keys")).json()
    assert all("secret" not in k and "key_hash" not in k for k in listed)
    duplicate = await live.client.post("/v1/api-keys", json={"name": "ci", "role": "viewer"})
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "api_key_exists"
    invalid = await live.client.post("/v1/api-keys", json={"name": "x", "role": "root"})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "request_invalid"
    revoked = await live.client.delete(f"/v1/api-keys/{created['id']}")
    assert revoked.status_code == 204
    gone = httpx.AsyncClient(
        base_url=live.url, headers={"Authorization": f"Bearer {created['secret']}"}
    )
    assert (await gone.get("/v1/devices")).status_code == 401


async def test_templates_and_validation(live: Live) -> None:
    bad = await live.client.post("/v1/templates/validate", json={"content": "name: x\nsteps: []"})
    assert bad.status_code == 200 and not bad.json()["valid"] and bad.json()["errors"]
    good = (await live.client.post("/v1/templates/validate", json={"content": TEMPLATE})).json()
    assert good["valid"] and good["step_count"] == 2

    assert (await live.client.post("/v1/templates", json={"content": TEMPLATE})).status_code == 201
    clash = await live.client.post("/v1/templates", json={"content": TEMPLATE})
    assert clash.status_code == 409
    rejected = await live.client.post("/v1/templates", json={"content": "name: broken"})
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "template_invalid"
    updated = await live.client.put("/v1/templates/rest-flow", json={"content": TEMPLATE})
    assert updated.json()["version"] == 2
    assert (await live.client.get("/v1/templates/nope")).status_code == 404
    assert (await live.client.delete("/v1/templates/rest-flow")).status_code == 204


async def test_job_submission_is_idempotent_and_paginated(live: Live) -> None:
    await live.client.post("/v1/templates", json={"content": TEMPLATE})
    headers = {"Idempotency-Key": "abc-123"}
    first = await live.client.post("/v1/jobs", json={"template": "rest-flow"}, headers=headers)
    again = await live.client.post("/v1/jobs", json={"template": "rest-flow"}, headers=headers)
    assert first.status_code == 201 and again.status_code == 200
    assert again.headers["idempotent-replay"] == "true"
    assert first.json()["id"] == again.json()["id"]

    for priority in (5, 9, 1):
        await live.client.post("/v1/jobs", json={"template": "rest-flow", "priority": priority})
    page = (await live.client.get("/v1/jobs", params={"limit": 2, "offset": 1})).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["limit"] == 2
    filtered = (await live.client.get("/v1/jobs", params={"state": "queued"})).json()
    assert filtered["total"] == 4
    assert (await live.client.get("/v1/jobs", params={"limit": 999})).status_code == 422

    job = (await live.client.get(f"/v1/jobs/{first.json()['id']}")).json()
    assert job["executions"] == [] and job["state"] == "queued"
    cancelled = (await live.client.post(f"/v1/jobs/{first.json()['id']}/cancel")).json()
    assert cancelled["state"] == "cancelled"
    unknown = await live.client.post("/v1/jobs", json={"template": "ghost"})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "template_not_found"
    extra = await live.client.post("/v1/jobs", json={"template": "rest-flow", "surprise": 1})
    assert extra.status_code == 422


async def test_schedules_alerts_webhooks_and_reports(live: Live) -> None:
    await live.client.post("/v1/templates", json={"content": TEMPLATE})
    created = await live.client.post(
        "/v1/schedules",
        json={
            "name": "nightly",
            "template": "rest-flow",
            "cron": "0 3 * * *",
            "timezone": "Europe/Berlin",
        },
    )
    assert created.status_code == 201
    sid = created.json()["id"]
    assert created.json()["next_fire_at"]
    off = (await live.client.post(f"/v1/schedules/{sid}/disable")).json()
    assert off["enabled"] is False
    assert (await live.client.post(f"/v1/schedules/{sid}/enable")).json()["enabled"] is True
    bad = await live.client.post(
        "/v1/schedules", json={"name": "bad", "template": "rest-flow", "cron": "61 * * * *"}
    )
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_cron"
    assert (await live.client.delete(f"/v1/schedules/{sid}")).status_code == 204

    rules = (await live.client.get("/v1/alert-rules")).json()
    assert {r["kind"] for r in rules} == {
        "device_offline",
        "failure_rate",
        "queue_wait",
        "lease_reclaimed",
    }
    patched = await live.client.patch(f"/v1/alert-rules/{rules[0]['id']}", json={"cooldown_s": 5})
    assert patched.json()["cooldown_s"] == 5

    hook = (
        await live.client.post("/v1/webhooks", json={"name": "slack", "url": "https://x.test/h"})
    ).json()
    assert hook["secret"].startswith("dlw_")
    assert "secret" not in (await live.client.get("/v1/webhooks")).json()[0]
    assert (await live.client.delete(f"/v1/webhooks/{hook['id']}")).status_code == 204

    daily = (await live.client.get("/v1/reports/daily")).json()
    assert daily["executions"]["total"] == 0
    csv_response = await live.client.get("/v1/reports/executions.csv")
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.text.startswith("execution_id,")


async def test_enrolment_over_websocket(live: Live) -> None:
    token = (
        await live.client.post(
            "/v1/enrolment-tokens", json={"host_name": "mac-ws", "site": "berlin", "region": "eu"}
        )
    ).json()
    assert token["command"].startswith("devicelab-agent run --server ws://")
    hello = {
        "type": "hello",
        "agent_version": "test",
        "host_name": "mac-ws",
        "enrolment_token": token["token"],
    }
    async with websockets.connect(live.ws_url) as ws:
        await ws.send(json.dumps(hello))
        welcome = json.loads(await ws.recv())
        assert welcome["type"] == "welcome" and welcome["credential"].startswith("dla_")
        credential = welcome["credential"]
    hosts = (await live.client.get("/v1/hosts")).json()
    assert hosts[0]["name"] == "mac-ws"

    # Reuse of the one-time token is refused with a typed, fatal error.
    async with websockets.connect(live.ws_url) as ws:
        await ws.send(json.dumps(hello))
        error = json.loads(await ws.recv())
        assert error["type"] == "error" and error["fatal"] and error["code"] == "enrolment_used"

    # The stored credential works instead, and does not get a new credential back.
    async with websockets.connect(live.ws_url) as ws:
        again = {**hello, "enrolment_token": None, "credential": credential}
        await ws.send(json.dumps(again))
        assert json.loads(await ws.recv())["credential"] is None


async def test_websocket_refuses_browsers_and_garbage(live: Live) -> None:
    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with websockets.connect(
            live.ws_url, additional_headers={"Origin": "https://evil.test"}
        ) as ws:
            await ws.recv()
    async with websockets.connect(live.ws_url) as ws:
        await ws.send("not json")
        assert json.loads(await ws.recv())["code"] == "bad_hello"
    async with websockets.connect(live.ws_url) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "agent_version": "t",
                    "host_name": "x",
                    "protocol_version": 99,
                    "credential": "dla_x",
                }
            )
        )
        assert json.loads(await ws.recv())["code"] == "unsupported_protocol"
    async with websockets.connect(live.ws_url) as ws:
        await ws.send(json.dumps({"type": "hello", "agent_version": "t", "host_name": "x"}))
        assert json.loads(await ws.recv())["code"] == "no_credentials"
    async with websockets.connect(live.ws_url) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "agent_version": "t",
                    "host_name": "x",
                    "credential": "dla_forged",
                }
            )
        )
        assert json.loads(await ws.recv())["code"] == "invalid_credential"


async def test_metrics_openapi_and_cors(live: Live) -> None:
    metrics = (await live.client.get("/metrics")).text
    assert "devicelab_http_request_duration_seconds" in metrics
    await live.client.get("/v1/devices")
    assert 'route="/v1/devices"' in (await live.client.get("/metrics")).text
    spec = (await live.client.get("/openapi.json")).json()
    assert "/v1/jobs" in spec["paths"] and spec["info"]["title"] == "DeviceLab"

    allowed = await live.client.options(
        "/v1/jobs",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = await live.client.options(
        "/v1/jobs",
        headers={"Origin": "https://evil.test", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in denied.headers


async def test_event_stream_delivers_job_events(live: Live) -> None:
    await live.client.post("/v1/templates", json={"content": TEMPLATE})
    lines: list[str] = []

    async def listen() -> None:
        async with live.client.stream("GET", "/v1/events", timeout=10) as response:
            async for line in response.aiter_lines():
                lines.append(line)
                if any(x.startswith("event: job.updated") for x in lines):
                    return

    task = asyncio.create_task(listen())
    for _ in range(50):
        if live.app and live.app.state.bus.subscriber_count:
            break
        await asyncio.sleep(0.05)
    await live.client.post("/v1/jobs", json={"template": "rest-flow"})
    await asyncio.wait_for(task, timeout=10)
    assert "event: job.updated" in lines
