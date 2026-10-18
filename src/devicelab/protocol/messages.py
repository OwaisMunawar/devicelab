"""Agent <-> control plane wire protocol, version 1.

Every frame is one JSON object with `v`, `id` and a `type` discriminator. Adding an
optional field is compatible; anything else bumps `PROTOCOL_VERSION`, and the committed
JSON schema in docs/protocol/ makes an accidental break fail a test.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from devicelab.domain.workflow import Step

PROTOCOL_VERSION = 1


def new_message_id() -> str:
    return uuid4().hex


class Frame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: int = PROTOCOL_VERSION
    id: str = Field(default_factory=new_message_id)


class HealthReport(BaseModel):
    """Deliberately unconstrained: range checks are domain policy, so a device reporting
    nonsense becomes a quarantine decision instead of a dropped frame."""

    model_config = ConfigDict(extra="forbid")

    udid: str = Field(min_length=1, max_length=128)
    battery_pct: int | None = None
    thermal: str = "nominal"
    storage_free_gb: float | None = None
    connectivity: str = "connected"


class DeviceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    udid: str = Field(min_length=1, max_length=128)
    name: str = Field(max_length=128)
    model: str = Field(max_length=64)
    model_family: str = Field(max_length=32)
    ios_version: str = Field(max_length=32)
    kind: Literal["simulator", "usb", "fake"]
    labels: list[str] = Field(default_factory=list, max_length=16)
    health: HealthReport


class ActiveExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    lease_id: str
    fencing_token: int


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(max_length=128)
    kind: Literal["screenshot", "log", "result"]
    content_type: str = Field(max_length=64)


# agent -> control plane ------------------------------------------------------------


class Hello(Frame):
    type: Literal["hello"] = "hello"
    protocol_version: int = PROTOCOL_VERSION
    agent_version: str
    host_name: str = Field(min_length=1, max_length=64)
    # Exactly one of these is set: a one-time token on first contact, the stored credential after.
    enrolment_token: str | None = None
    credential: str | None = None
    active_executions: list[ActiveExecution] = Field(default_factory=list)


class Inventory(Frame):
    type: Literal["inventory"] = "inventory"
    devices: list[DeviceReport]


class Heartbeat(Frame):
    type: Literal["heartbeat"] = "heartbeat"
    devices: list[HealthReport]
    executions: list[ActiveExecution] = Field(default_factory=list)


class StepStarted(Frame):
    type: Literal["step_started"] = "step_started"
    execution_id: str
    fencing_token: int
    step_index: int
    step_id: str
    attempt: int = 1


class StepLog(Frame):
    type: Literal["step_log"] = "step_log"
    execution_id: str
    fencing_token: int
    step_index: int | None = None
    level: Literal["debug", "info", "warning", "error"] = "info"
    message: str = Field(max_length=4000)


class StepFinished(Frame):
    type: Literal["step_finished"] = "step_finished"
    execution_id: str
    fencing_token: int
    step_index: int
    state: Literal["succeeded", "failed", "skipped"]
    attempts: int = 1
    duration_ms: int = 0
    failure_code: str | None = None
    error: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)


class ArtifactUpload(Frame):
    type: Literal["artifact_upload"] = "artifact_upload"
    execution_id: str
    fencing_token: int
    step_index: int | None = None
    artifact: ArtifactRef
    data_b64: str


class ExecutionFinished(Frame):
    type: Literal["execution_finished"] = "execution_finished"
    execution_id: str
    fencing_token: int
    state: Literal["succeeded", "failed", "cancelled"]
    failure_code: str | None = None
    error: str | None = None
    session_restarts: int = 0
    reboots: int = 0


AgentMessage = Annotated[
    Hello
    | Inventory
    | Heartbeat
    | StepStarted
    | StepLog
    | StepFinished
    | ArtifactUpload
    | ExecutionFinished,
    Field(discriminator="type"),
]

# control plane -> agent ------------------------------------------------------------


class Welcome(Frame):
    type: Literal["welcome"] = "welcome"
    host_id: str
    site: str
    # Present only on enrolment; the agent persists it and never receives it again.
    credential: str | None = None
    heartbeat_interval_s: float
    lease_ttl_s: float


class Assign(Frame):
    type: Literal["assign"] = "assign"
    execution_id: str
    job_id: str
    lease_id: str
    fencing_token: int
    attempt: int
    device_udid: str
    failure_policy: Literal["abort", "continue"]
    steps: list[Step]
    correlation_id: str | None = None


class Cancel(Frame):
    type: Literal["cancel"] = "cancel"
    execution_id: str
    reason: str = ""


class Ack(Frame):
    type: Literal["ack"] = "ack"
    ref_id: str
    ok: bool = True
    code: str | None = None


class ServerError(Frame):
    type: Literal["error"] = "error"
    code: str
    message: str
    fatal: bool = False


ServerMessage = Annotated[
    Welcome | Assign | Cancel | Ack | ServerError, Field(discriminator="type")
]

_agent_adapter: TypeAdapter[Any] = TypeAdapter(AgentMessage)
_server_adapter: TypeAdapter[Any] = TypeAdapter(ServerMessage)


def parse_agent_message(
    raw: str | bytes,
) -> (
    Hello
    | Inventory
    | Heartbeat
    | StepStarted
    | StepLog
    | StepFinished
    | ArtifactUpload
    | ExecutionFinished
):
    return _agent_adapter.validate_json(raw)  # type: ignore[no-any-return]


def parse_server_message(raw: str | bytes) -> Welcome | Assign | Cancel | Ack | ServerError:
    return _server_adapter.validate_json(raw)  # type: ignore[no-any-return]


def encode(message: BaseModel) -> str:
    return message.model_dump_json()


def json_schema() -> dict[str, Any]:
    """One document describing both directions, written to docs/protocol/ by a script."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "agent_to_server": _agent_adapter.json_schema(),
        "server_to_agent": _server_adapter.json_schema(),
    }
