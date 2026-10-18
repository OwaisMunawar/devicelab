"""Server side of the agent protocol: one WebSocket per host, all of it outbound-initiated
by the agent. The hub owns connections and nothing else; every decision is a use case."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from uuid import UUID

import structlog
from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from devicelab.api.observability import Metrics
from devicelab.application.records import HostRecord
from devicelab.container import Services
from devicelab.domain.errors import DomainError, NotFoundError, StaleFenceError
from devicelab.domain.failures import classify
from devicelab.protocol.messages import (
    PROTOCOL_VERSION,
    Ack,
    ArtifactUpload,
    Assign,
    Cancel,
    ExecutionFinished,
    Heartbeat,
    Hello,
    Inventory,
    ServerError,
    StepFinished,
    StepLog,
    StepStarted,
    Welcome,
    encode,
    parse_agent_message,
)

log = structlog.get_logger("agent_hub")

HELLO_TIMEOUT_S = 10.0
MAX_BAD_FRAMES = 5
CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORISED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_REPLACED = 4409


@dataclass(eq=False)
class AgentConnection:
    host: HostRecord
    ws: WebSocket
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    outbox: asyncio.Queue[BaseModel] = field(default_factory=asyncio.Queue)
    step_types: dict[str, list[str]] = field(default_factory=dict)
    replaced: bool = False


class AgentHub:
    def __init__(self, metrics: Metrics, heartbeat_interval_s: float, lease_ttl_s: float) -> None:
        self._conns: dict[UUID, AgentConnection] = {}
        self._metrics = metrics
        self._heartbeat_s = heartbeat_interval_s
        self._lease_ttl_s = lease_ttl_s
        self._services: Services | None = None

    def bind(self, services: Services) -> None:
        self._services = services

    @property
    def services(self) -> Services:
        assert self._services is not None, "hub used before bind()"
        return self._services

    @property
    def connected_count(self) -> int:
        return len(self._conns)

    # AgentGateway ----------------------------------------------------------------

    def wake(self, host_id: UUID) -> None:
        conn = self._conns.get(host_id)
        if conn is not None:
            conn.wake.set()

    def cancel(self, host_id: UUID, execution_id: UUID, reason: str) -> None:
        conn = self._conns.get(host_id)
        if conn is not None:
            conn.outbox.put_nowait(Cancel(execution_id=str(execution_id), reason=reason))

    async def drop(self, host_id: UUID) -> None:
        """Close a host's socket without ceremony. Used by chaos tests."""
        conn = self._conns.get(host_id)
        if conn is not None:
            with contextlib.suppress(RuntimeError):
                await conn.ws.close(code=1011)

    # connection lifecycle ----------------------------------------------------------

    async def serve(self, ws: WebSocket) -> None:
        # Browsers always send Origin; the agent never does. Refusing it closes the door on
        # cross-site WebSocket hijacking without needing a cookie or token check.
        if ws.headers.get("origin"):
            await ws.close(code=CLOSE_FORBIDDEN)
            return
        await ws.accept()
        conn = await self._handshake(ws)
        if conn is None:
            return
        structlog.contextvars.bind_contextvars(host=conn.host.name)
        tasks = [
            asyncio.create_task(self._reader(conn)),
            asyncio.create_task(self._writer(conn)),
            asyncio.create_task(self._dispatcher(conn)),
        ]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self._conns.get(conn.host.id) is conn:
                del self._conns[conn.host.id]
                self._metrics.agents.set(len(self._conns))
                await self.services.fleet.host_disconnected(conn.host.id)
            log.info("agent_disconnected")
            structlog.contextvars.unbind_contextvars("host")

    async def _handshake(self, ws: WebSocket) -> AgentConnection | None:
        try:
            hello = parse_agent_message(await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S))
            if not isinstance(hello, Hello):
                raise ValueError("first frame must be hello")
        except (TimeoutError, ValueError, ValidationError, WebSocketDisconnect):
            await self._refuse(ws, "bad_hello", "expected a hello frame", CLOSE_BAD_REQUEST)
            return None
        if hello.protocol_version != PROTOCOL_VERSION:
            await self._refuse(
                ws, "unsupported_protocol", f"server speaks v{PROTOCOL_VERSION}", CLOSE_BAD_REQUEST
            )
            return None

        fleet = self.services.fleet
        credential: str | None = None
        try:
            if hello.enrolment_token:
                host, credential = await fleet.enrol(hello.enrolment_token, hello.host_name)
            elif hello.credential:
                host = await fleet.authenticate_host(hello.credential)
                if host.name != hello.host_name:
                    await self._refuse(
                        ws,
                        "host_mismatch",
                        "credential belongs to another host",
                        CLOSE_UNAUTHORISED,
                    )
                    return None
            else:
                await self._refuse(
                    ws, "no_credentials", "hello needs a token or credential", CLOSE_UNAUTHORISED
                )
                return None
        except DomainError as exc:
            await self._refuse(ws, exc.code, exc.message, CLOSE_UNAUTHORISED)
            return None

        conn = AgentConnection(host=host, ws=ws)
        previous = self._conns.get(host.id)
        self._conns[host.id] = conn
        if previous is not None:
            previous.replaced = True
            with contextlib.suppress(RuntimeError):
                await previous.ws.close(code=CLOSE_REPLACED)
        self._metrics.agents.set(len(self._conns))

        await ws.send_text(
            encode(
                Welcome(
                    host_id=str(host.id),
                    site=host.site,
                    credential=credential,
                    heartbeat_interval_s=self._heartbeat_s,
                    lease_ttl_s=self._lease_ttl_s,
                )
            )
        )
        await fleet.host_connected(host.id, hello.agent_version)
        result = await self.services.executions.reconcile_host(host.id, hello.active_executions)
        for orphan in result.orphans:
            conn.outbox.put_nowait(Cancel(execution_id=orphan, reason="not assigned to this host"))
        log.info("agent_connected", lost=len(result.lost), orphans=len(result.orphans))
        return conn

    @staticmethod
    async def _refuse(ws: WebSocket, code: str, message: str, close_code: int) -> None:
        with contextlib.suppress(RuntimeError, WebSocketDisconnect):
            await ws.send_text(encode(ServerError(code=code, message=message, fatal=True)))
            await ws.close(code=close_code)

    # loops -------------------------------------------------------------------------

    async def _writer(self, conn: AgentConnection) -> None:
        while True:
            message = await conn.outbox.get()
            await conn.ws.send_text(encode(message))

    async def _dispatcher(self, conn: AgentConnection) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(conn.wake.wait(), timeout=1.0)
            conn.wake.clear()
            for item in await self.services.executions.take_dispatchable(conn.host.id):
                ex = item.execution
                conn.step_types[str(ex.id)] = [s["type"] for s in item.steps]
                conn.outbox.put_nowait(
                    Assign.model_validate(
                        {
                            "execution_id": str(ex.id),
                            "job_id": str(ex.job_id),
                            "lease_id": str(ex.lease_id),
                            "fencing_token": ex.fencing_token,
                            "attempt": ex.attempt,
                            "device_udid": item.device_udid,
                            "failure_policy": item.failure_policy,
                            "steps": item.steps,
                            "correlation_id": item.correlation_id,
                        }
                    )
                )

    async def _reader(self, conn: AgentConnection) -> None:
        bad_frames = 0
        while True:
            try:
                raw = await conn.ws.receive_text()
            except WebSocketDisconnect:
                return
            try:
                message = parse_agent_message(raw)
            except ValidationError as exc:
                bad_frames += 1
                conn.outbox.put_nowait(
                    ServerError(code="bad_frame", message=str(exc.errors()[0]["msg"]))
                )
                if bad_frames >= MAX_BAD_FRAMES:
                    await conn.ws.close(code=CLOSE_BAD_REQUEST)
                    return
                continue
            try:
                await self._handle(conn, message)
            except (StaleFenceError, NotFoundError) as exc:
                execution_id = getattr(message, "execution_id", None)
                if execution_id:
                    conn.outbox.put_nowait(Cancel(execution_id=execution_id, reason=exc.code))
                conn.outbox.put_nowait(Ack(ref_id=message.id, ok=False, code=exc.code))
            except DomainError as exc:
                conn.outbox.put_nowait(Ack(ref_id=message.id, ok=False, code=exc.code))

    async def _handle(
        self,
        conn: AgentConnection,
        message: Hello
        | Inventory
        | Heartbeat
        | StepStarted
        | StepLog
        | StepFinished
        | ArtifactUpload
        | ExecutionFinished,
    ) -> None:
        svc = self.services
        match message:
            case Inventory():
                await svc.fleet.register_inventory(conn.host, message.devices)
                conn.outbox.put_nowait(Ack(ref_id=message.id))
            case Heartbeat():
                result = await svc.fleet.heartbeat(conn.host, message.devices, message.executions)
                for orphan in result.orphans:
                    conn.outbox.put_nowait(
                        Cancel(execution_id=orphan, reason="lease no longer held")
                    )
            case StepStarted():
                await svc.executions.step_started(message)
            case StepLog():
                await svc.executions.step_log(message)
            case StepFinished():
                await svc.executions.step_finished(message)
                types = conn.step_types.get(message.execution_id, [])
                kind = types[message.step_index] if message.step_index < len(types) else "unknown"
                self._metrics.step_duration.labels(kind, message.state).observe(
                    message.duration_ms / 1000
                )
            case ArtifactUpload():
                await svc.executions.artifact_uploaded(message)
            case ExecutionFinished():
                await svc.executions.execution_finished(message)
                conn.step_types.pop(message.execution_id, None)
                if message.state == "failed":
                    fc = classify(message.failure_code, message.error or "")
                    self._metrics.failures.labels(fc.value).inc()
                conn.outbox.put_nowait(Ack(ref_id=message.id))
            case Hello():
                conn.outbox.put_nowait(
                    ServerError(code="unexpected_hello", message="already connected")
                )
