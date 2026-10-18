"""The host agent: one outbound WebSocket to the control plane, reconnecting forever.

Execution state deliberately outlives the socket. A step that is mid-flight when the link
drops keeps running; its messages queue in the outbox and flow again after reconnect. The
control plane decides, via lease expiry and fencing tokens, whether that work still counts.
"""

from __future__ import annotations

import asyncio
import random
from collections import deque
from dataclasses import dataclass, field

import structlog
import websockets
from pydantic import BaseModel, ValidationError
from websockets.asyncio.client import ClientConnection

from devicelab.adapters.providers.base import DeviceInfo, DeviceProvider, HealthSnapshot, UiDriver
from devicelab.agent.config import AgentConfig
from devicelab.agent.executor import StepExecutor
from devicelab.agent.runner import ExecutionRunner
from devicelab.agent.state import CredentialStore
from devicelab.domain.backoff import backoff_delay
from devicelab.protocol.messages import (
    PROTOCOL_VERSION,
    Ack,
    ActiveExecution,
    Assign,
    Cancel,
    DeviceReport,
    ExecutionFinished,
    HealthReport,
    Heartbeat,
    Hello,
    Inventory,
    ServerError,
    StepLog,
    Welcome,
    encode,
    parse_server_message,
)

log = structlog.get_logger("agent")
OUTBOX_LIMIT = 20_000


class AgentFatalError(Exception):
    """The control plane refused us in a way retrying cannot fix (bad credentials)."""


@dataclass
class _Running:
    assign: Assign
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        provider: DeviceProvider,
        ui: UiDriver | None = None,
        *,
        rng: random.Random | None = None,
    ) -> None:
        self._cfg = config
        self._provider = provider
        self._ui = ui
        self._rng = rng or random.Random()
        self._store = CredentialStore(config.state_dir, config.host_name)
        self._executor = StepExecutor(provider, ui, config.app_root)
        self._outbox: deque[BaseModel] = deque()
        self._wakeup = asyncio.Event()
        self._running: dict[str, _Running] = {}
        self._unacked: dict[str, ExecutionFinished] = {}
        self._dead: set[str] = set()
        self._ws: ClientConnection | None = None
        self._stopping = False
        self._devices: list[DeviceInfo] = []
        self.connected = asyncio.Event()
        self.connection_count = 0

    # -- public ------------------------------------------------------------------

    async def run(self) -> None:
        attempt = 0
        while not self._stopping:
            try:
                await self._session()
                attempt = 0
            except AgentFatalError:
                raise
            except (OSError, websockets.WebSocketException, TimeoutError) as exc:
                attempt += 1
                log.warning(
                    "connection_lost", error=str(exc) or type(exc).__name__, attempt=attempt
                )
            self.connected.clear()
            if self._stopping:
                break
            delay = backoff_delay(
                attempt,
                base_s=self._cfg.reconnect_base_s,
                cap_s=self._cfg.reconnect_max_s,
                jitter=0.3,
                rng=self._rng.random,
            )
            await asyncio.sleep(delay)

    async def stop(self) -> None:
        self._stopping = True
        for item in list(self._running.values()):
            item.cancel.set()
        await self.drop_connection()
        tasks = [r.task for r in self._running.values() if r.task]
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._ui is not None:
            await self._ui.shutdown()

    async def drop_connection(self) -> None:
        """Close the socket abruptly. The run loop reconnects; used by chaos tests."""
        if self._ws is not None:
            await self._ws.close(code=1011)

    def kill(self) -> None:
        """Simulate the agent process dying: nothing is flushed, nothing is cleaned up."""
        self._stopping = True
        for item in self._running.values():
            if item.task:
                item.task.cancel()
        self._running.clear()

    @property
    def active_execution_ids(self) -> list[str]:
        return [*self._running, *self._unacked]

    # -- one connection ------------------------------------------------------------

    async def _session(self) -> None:
        credential = self._store.load()
        async with websockets.connect(
            self._cfg.server_url,
            max_size=self._cfg.max_message_bytes,
            open_timeout=10,
            ping_interval=20,
            ping_timeout=20,
        ) as ws:
            self._ws = ws
            hello = Hello(
                agent_version=self._cfg.agent_version,
                host_name=self._cfg.host_name,
                enrolment_token=None if credential else self._cfg.enrol_token,
                credential=credential,
                active_executions=self._refs(),
            )
            await ws.send(encode(hello))
            welcome = await self._expect_welcome(ws)
            if welcome.credential:
                self._store.save(welcome.credential)
            self.connection_count += 1
            self.connected.set()
            log.info("connected", site=welcome.site, protocol=PROTOCOL_VERSION)

            # Anything unacknowledged from before the drop goes out again first.
            for finished in self._unacked.values():
                self._outbox.appendleft(finished)
            tasks = [
                asyncio.create_task(self._sender(ws)),
                asyncio.create_task(self._receiver(ws)),
                asyncio.create_task(self._telemetry(welcome.heartbeat_interval_s)),
            ]
            try:
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in done:
                    task.result()
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                self._ws = None

    async def _expect_welcome(self, ws: ClientConnection) -> Welcome:
        message = parse_server_message(await asyncio.wait_for(ws.recv(), timeout=15))
        if isinstance(message, ServerError):
            if message.fatal:
                raise AgentFatalError(f"{message.code}: {message.message}")
            raise websockets.WebSocketException(message.message)
        if not isinstance(message, Welcome):
            raise websockets.WebSocketException("expected welcome")
        return message

    def _refs(self) -> list[ActiveExecution]:
        refs = [
            ActiveExecution(
                execution_id=eid, lease_id=r.assign.lease_id, fencing_token=r.assign.fencing_token
            )
            for eid, r in self._running.items()
        ]
        refs += [
            ActiveExecution(execution_id=eid, lease_id="", fencing_token=f.fencing_token)
            for eid, f in self._unacked.items()
        ]
        return refs

    # -- loops -------------------------------------------------------------------

    def _enqueue(self, message: BaseModel) -> None:
        if isinstance(message, ExecutionFinished):
            self._unacked[message.execution_id] = message
        if len(self._outbox) >= OUTBOX_LIMIT:
            # Under a long partition, shed the cheapest data first: log lines.
            for index, queued in enumerate(self._outbox):
                if isinstance(queued, StepLog):
                    del self._outbox[index]
                    break
        self._outbox.append(message)
        self._wakeup.set()

    async def _sender(self, ws: ClientConnection) -> None:
        while True:
            while self._outbox:
                message = self._outbox[0]
                execution_id = getattr(message, "execution_id", None)
                if execution_id in self._dead:
                    self._outbox.popleft()
                    continue
                await ws.send(encode(message))
                self._outbox.popleft()
            self._wakeup.clear()
            if not self._outbox:
                await self._wakeup.wait()

    async def _telemetry(self, interval_s: float) -> None:
        loop = asyncio.get_running_loop()
        next_inventory = 0.0
        while True:
            if loop.time() >= next_inventory:
                self._devices = await self._provider.discover()
                self._enqueue(Inventory(devices=[_report(d) for d in self._devices]))
                next_inventory = loop.time() + self._cfg.inventory_interval_s
            else:
                self._enqueue(await self._heartbeat())
            await asyncio.sleep(interval_s)

    async def _heartbeat(self) -> Heartbeat:
        reports: list[HealthReport] = []
        for device in self._devices:
            try:
                health = await self._provider.health(device.udid)
            except Exception:
                health = HealthSnapshot(battery_pct=None, connectivity="unreachable")
            reports.append(_health(device.udid, health))
        return Heartbeat(devices=reports, executions=self._refs())

    async def _receiver(self, ws: ClientConnection) -> None:
        async for raw in ws:
            try:
                message = parse_server_message(raw)
            except ValidationError as exc:
                log.warning("bad_server_frame", error=str(exc.errors()[0]["msg"]))
                continue
            match message:
                case Assign():
                    self._start(message)
                case Cancel():
                    self._cancel(message.execution_id, message.reason)
                case Ack():
                    for eid, finished in list(self._unacked.items()):
                        if finished.id == message.ref_id:
                            del self._unacked[eid]
                    if not message.ok:
                        log.warning("rejected_by_server", code=message.code)
                case ServerError():
                    log.warning("server_error", code=message.code, message=message.message)
                    if message.fatal:
                        raise AgentFatalError(f"{message.code}: {message.message}")
                case Welcome():
                    pass

    # -- executions --------------------------------------------------------------

    def _start(self, assign: Assign) -> None:
        if assign.execution_id in self._running or assign.execution_id in self._unacked:
            return
        known = {d.udid for d in self._devices}
        if assign.device_udid not in known:
            self._enqueue(
                ExecutionFinished(
                    execution_id=assign.execution_id,
                    fencing_token=assign.fencing_token,
                    state="failed",
                    failure_code="device_unreachable",
                    error=f"device {assign.device_udid} is not attached to this host",
                )
            )
            return
        running = _Running(assign)
        runner = ExecutionRunner(
            self._executor, self._enqueue, rng=self._rng, allow_reboot=self._cfg.allow_reboot
        )

        async def work() -> None:
            try:
                await runner.run(assign, running.cancel)
            finally:
                self._running.pop(assign.execution_id, None)

        running.task = asyncio.create_task(work(), name=f"exec-{assign.execution_id[:8]}")
        self._running[assign.execution_id] = running

    def _cancel(self, execution_id: str, reason: str) -> None:
        log.info("cancel", execution=execution_id, reason=reason)
        self._dead.add(execution_id)
        self._unacked.pop(execution_id, None)
        running = self._running.get(execution_id)
        if running is not None:
            running.cancel.set()


def _health(udid: str, health: HealthSnapshot) -> HealthReport:
    return HealthReport(
        udid=udid,
        battery_pct=health.battery_pct,
        thermal=health.thermal,
        storage_free_gb=health.storage_free_gb,
        connectivity=health.connectivity,
    )


def _report(info: DeviceInfo) -> DeviceReport:
    return DeviceReport(
        udid=info.udid,
        name=info.name,
        model=info.model,
        model_family=info.model_family,
        ios_version=info.ios_version,
        kind=info.kind,
        labels=info.labels,
        health=_health(info.udid, info.health),
    )


__all__ = ["AgentFatalError", "AgentRuntime"]
