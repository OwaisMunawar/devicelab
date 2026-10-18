"""Builds the object graph for a process. Shared by the API lifespan and the CLI."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy.ext.asyncio import AsyncEngine

from devicelab.adapters.alerts.webhook import WebhookNotifier
from devicelab.adapters.db.engine import create_engine, session_factory
from devicelab.adapters.db.repositories import SqlUnitOfWork
from devicelab.adapters.events import InProcessEventBus
from devicelab.adapters.storage.local import LocalArtifactStore
from devicelab.adapters.storage.s3 import S3ArtifactStore, create_s3_client
from devicelab.adapters.system import SystemClock
from devicelab.api.agent_hub import AgentHub
from devicelab.api.observability import Metrics
from devicelab.application.ports import AlertNotifier, ArtifactStore, UowFactory
from devicelab.config import Settings
from devicelab.container import Services, build_services


@dataclass
class Runtime:
    settings: Settings
    engine: AsyncEngine
    uow_factory: UowFactory
    services: Services
    bus: InProcessEventBus
    hub: AgentHub
    metrics: Metrics
    notifier: AlertNotifier

    async def aclose(self) -> None:
        close = getattr(self.notifier, "aclose", None)
        if close is not None:
            await close()
        await self.engine.dispose()


def build_store(settings: Settings) -> ArtifactStore:
    if settings.artifact_backend == "s3":
        client = create_s3_client(settings.s3_endpoint_url, settings.s3_region)
        return S3ArtifactStore(client, settings.s3_bucket)
    return LocalArtifactStore(settings.artifact_dir)


def build_runtime(settings: Settings, notifier: AlertNotifier | None = None) -> Runtime:
    engine = create_engine(settings.database_url, pool_size=settings.db_pool_size)
    factory = session_factory(engine)

    def uow_factory() -> SqlUnitOfWork:
        return SqlUnitOfWork(factory)

    bus = InProcessEventBus()
    metrics = Metrics()
    hub = AgentHub(metrics, settings.heartbeat_interval_s, settings.lease_ttl_s)
    notifier = notifier or WebhookNotifier(httpx.AsyncClient(timeout=5.0))
    services = build_services(
        uow_factory=uow_factory,
        clock=SystemClock(),
        config=settings.control_plane(),
        store=build_store(settings),
        notifier=notifier,
        events=bus,
        gateway=hub,
    )
    hub.bind(services)
    return Runtime(settings, engine, uow_factory, services, bus, hub, metrics, notifier)
