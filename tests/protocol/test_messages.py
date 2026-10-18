from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from devicelab.domain.workflow import Screenshot
from devicelab.protocol.messages import (
    PROTOCOL_VERSION,
    Assign,
    DeviceReport,
    ExecutionFinished,
    HealthReport,
    Heartbeat,
    Hello,
    Inventory,
    StepLog,
    Welcome,
    encode,
    json_schema,
    parse_agent_message,
    parse_server_message,
)

SCHEMA = Path(__file__).resolve().parents[2] / "docs" / "protocol" / "v1.schema.json"


def test_committed_schema_matches_models() -> None:
    """Fails when a message changes without regenerating the schema (and thinking about v)."""
    committed = json.loads(SCHEMA.read_text())
    assert committed == json.loads(json.dumps(json_schema(), sort_keys=True))
    assert committed["protocol_version"] == PROTOCOL_VERSION


def test_agent_messages_round_trip() -> None:
    report = DeviceReport(
        udid="U1",
        name="iPhone",
        model="iPhone 15",
        model_family="iPhone",
        ios_version="18.2",
        kind="fake",
        health=HealthReport(udid="U1", battery_pct=80),
    )
    for msg in (
        Hello(agent_version="0.1.0", host_name="mac-1", enrolment_token="t"),
        Inventory(devices=[report]),
        Heartbeat(devices=[report.health]),
        StepLog(execution_id="e", fencing_token=1, message="hi"),
        ExecutionFinished(execution_id="e", fencing_token=1, state="failed", failure_code="x"),
    ):
        assert parse_agent_message(encode(msg)) == msg


def test_server_messages_round_trip() -> None:
    assign = Assign(
        execution_id="e",
        job_id="j",
        lease_id="l",
        fencing_token=3,
        attempt=1,
        device_udid="U1",
        failure_policy="abort",
        steps=[Screenshot(id="shot", type="screenshot")],
    )
    parsed = parse_server_message(encode(assign))
    assert parsed == assign
    welcome = Welcome(host_id="h", site="berlin", heartbeat_interval_s=5, lease_ttl_s=30)
    assert parse_server_message(encode(welcome)) == welcome


@pytest.mark.parametrize(
    "raw",
    [
        '{"type": "teleport"}',
        '{"type": "hello", "agent_version": "1", "host_name": "", "v": 1}',
        '{"type": "step_log", "execution_id": "e", "fencing_token": 1, "message": "x", "extra": 1}',
        "not json",
    ],
)
def test_malformed_frames_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        parse_agent_message(raw)


def test_assign_rejects_steps_outside_the_allow_list() -> None:
    bad = {
        "type": "assign",
        "execution_id": "e",
        "job_id": "j",
        "lease_id": "l",
        "fencing_token": 1,
        "attempt": 1,
        "device_udid": "U",
        "failure_policy": "abort",
        "steps": [{"id": "sh", "type": "run_shell", "command": "rm -rf /"}],
    }
    with pytest.raises(ValidationError):
        parse_server_message(json.dumps(bad))


def test_health_is_not_range_checked_on_the_wire() -> None:
    assert HealthReport(udid="U", battery_pct=400, thermal="banana").battery_pct == 400
