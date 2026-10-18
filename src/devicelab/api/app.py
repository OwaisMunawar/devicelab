from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from devicelab import __version__
from devicelab.adapters.db.migrate import upgrade
from devicelab.api.background import BackgroundRunner
from devicelab.api.errors import install_error_handlers
from devicelab.api.observability import configure_logging, correlation_middleware
from devicelab.api.routers import fleet, ops, workflows
from devicelab.application.ports import AlertNotifier
from devicelab.config import Settings
from devicelab.runtime import build_runtime

log = structlog.get_logger()


def create_app(
    settings: Settings | None = None,
    *,
    notifier: AlertNotifier | None = None,
    run_background: bool = True,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        configure_logging(json_output=settings.log_json, level=settings.log_level)
        if settings.migrate_on_start:
            await asyncio.to_thread(upgrade, settings.database_url)
        runtime = build_runtime(settings, notifier)
        app.state.runtime = runtime
        app.state.services = runtime.services
        app.state.bus = runtime.bus
        app.state.hub = runtime.hub
        app.state.metrics = runtime.metrics
        app.state.settings = settings

        if settings.bootstrap_admin_key and await runtime.services.auth.bootstrap(
            settings.bootstrap_admin_key
        ):
            log.info("bootstrap_admin_key_created")
        await runtime.services.alerts.ensure_default_rules()
        if settings.demo:
            from devicelab.demo import seed_demo  # noqa: PLC0415 - demo-only code path

            await seed_demo(runtime)

        runner = BackgroundRunner(runtime.services, settings, runtime.metrics, runtime.hub)
        if run_background:
            runner.start()
        log.info("control_plane_started", version=__version__)
        try:
            yield
        finally:
            await runner.stop()
            await runtime.aclose()

    app = FastAPI(
        title="DeviceLab",
        version=__version__,
        description="Control plane for automated workflows across a device fleet.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "Idempotent-Replay"],
        max_age=600,
    )

    async def security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=security_headers)
    # Added last so it runs first and the correlation id is bound before anything logs.
    metrics_holder = _MetricsProxy(app)
    app.add_middleware(BaseHTTPMiddleware, dispatch=correlation_middleware(metrics_holder))  # type: ignore[arg-type]
    install_error_handlers(app)
    app.include_router(fleet.router)
    app.include_router(workflows.router)
    app.include_router(ops.router)

    @app.websocket("/v1/agent/ws")
    async def agent_socket(ws: WebSocket) -> None:
        await app.state.hub.serve(ws)

    return app


class _MetricsProxy:
    """The metrics registry only exists once the lifespan has run; resolve it per request."""

    def __init__(self, app: FastAPI) -> None:
        self._app = app

    @property
    def http(self):  # type: ignore[no-untyped-def]
        return self._app.state.metrics.http
