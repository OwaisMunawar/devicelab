"""Periodic control-plane work. Each loop is independent: one failing never stops another."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable

import structlog

from devicelab.api.agent_hub import AgentHub
from devicelab.api.observability import Metrics
from devicelab.config import Settings
from devicelab.container import Services

log = structlog.get_logger("background")


class BackgroundRunner:
    def __init__(
        self, services: Services, settings: Settings, metrics: Metrics, hub: AgentHub
    ) -> None:
        self._svc = services
        self._settings = settings
        self._metrics = metrics
        self._hub = hub
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        s = self._settings
        loops: list[tuple[str, float, Callable[[], Awaitable[float | None]]]] = [
            ("scheduler", s.scheduler_interval_s, self._schedule_tick),
            ("sweeper", s.sweep_interval_s, self._sweep),
            ("schedules", s.schedule_interval_s, self._schedules),
            ("alerts", s.alert_interval_s, self._alerts),
            ("retention", s.retention_interval_s, self._retention),
            ("gauges", 5.0, self._gauges),
        ]
        self._tasks = [asyncio.create_task(self._run(n, i, fn), name=n) for n, i, fn in loops]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

    async def _run(
        self, name: str, interval: float, fn: Callable[[], Awaitable[float | None]]
    ) -> None:
        while True:
            delay = interval
            try:
                delay = await fn() or interval
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("loop_failed", loop=name)
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.sleep(delay)

    async def _schedule_tick(self) -> float | None:
        started = time.perf_counter()
        result = await self._svc.scheduler.tick()
        self._metrics.tick.observe(time.perf_counter() - started)
        if result.jobs_assigned:
            self._metrics.assigned.inc(result.jobs_assigned)
            return 0.01  # more work may be waiting; do not idle a full interval
        return None

    async def _sweep(self) -> None:
        await self._svc.fleet.sweep()
        reclaimed = await self._svc.scheduler.reclaim_expired()
        if reclaimed:
            self._metrics.reclaimed.inc(reclaimed)

    async def _schedules(self) -> None:
        await self._svc.schedules.tick()

    async def _alerts(self) -> None:
        await self._svc.alerts.evaluate()

    async def _retention(self) -> None:
        await self._svc.executions.apply_retention()

    async def _gauges(self) -> None:
        m = self._metrics
        for state, count in (await self._svc.fleet.fleet_summary()).items():
            m.devices.labels(state).set(count)
        jobs = await self._svc.jobs.counts()
        for state, count in jobs.items():
            m.jobs.labels(state).set(count)
        m.queue_depth.set(jobs.get("queued", 0))
        m.leases_active.set(await self._svc.scheduler.active_leases())
        m.agents.set(self._hub.connected_count)
