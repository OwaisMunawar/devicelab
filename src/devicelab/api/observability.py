"""Structured logging, request correlation and Prometheus metrics."""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from starlette.requests import Request
from starlette.responses import Response

CORRELATION_HEADER = "X-Correlation-ID"


def configure_logging(*, json_output: bool, level: str) -> None:
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[*processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        cache_logger_on_first_use=True,
    )


class Metrics:
    """One registry per app instance, so tests can build many apps without collisions."""

    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.queue_depth = Gauge(
            "devicelab_queue_depth", "Jobs waiting for a device", registry=self.registry
        )
        self.leases_active = Gauge(
            "devicelab_leases_active", "Active device leases", registry=self.registry
        )
        self.devices = Gauge(
            "devicelab_devices", "Devices by state", ["state"], registry=self.registry
        )
        self.jobs = Gauge("devicelab_jobs", "Jobs by state", ["state"], registry=self.registry)
        self.agents = Gauge(
            "devicelab_agents_connected", "Connected host agents", registry=self.registry
        )
        self.step_duration = Histogram(
            "devicelab_step_duration_seconds",
            "Workflow step duration",
            ["type", "state"],
            buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300),
            registry=self.registry,
        )
        self.failures = Counter(
            "devicelab_execution_failures_total",
            "Failed executions by class",
            ["class"],
            registry=self.registry,
        )
        self.assigned = Counter(
            "devicelab_jobs_assigned_total", "Jobs leased to devices", registry=self.registry
        )
        self.reclaimed = Counter(
            "devicelab_leases_reclaimed_total",
            "Leases reclaimed after expiry",
            registry=self.registry,
        )
        self.tick = Histogram(
            "devicelab_scheduler_tick_seconds",
            "Scheduler tick duration",
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5),
            registry=self.registry,
        )
        self.http = Histogram(
            "devicelab_http_request_duration_seconds",
            "HTTP request duration",
            ["method", "route", "status"],
            registry=self.registry,
        )


def correlation_middleware(
    metrics: Metrics,
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    log = structlog.get_logger("http")

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        correlation_id = request.headers.get(CORRELATION_HEADER) or uuid.uuid4().hex
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers[CORRELATION_HEADER] = correlation_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = request.scope.get("route")
            template = getattr(route, "path", request.url.path)
            if not request.url.path.startswith(("/metrics", "/healthz")):
                metrics.http.labels(request.method, template, str(status)).observe(elapsed)
                log.info(
                    "request",
                    method=request.method,
                    path=request.url.path,
                    status=status,
                    ms=round(elapsed * 1000, 1),
                )

    return middleware
