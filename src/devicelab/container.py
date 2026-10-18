"""Composition root: the one place that wires use cases to their ports."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from devicelab.application.alerts import AlertService
from devicelab.application.auth import AuthService
from devicelab.application.config import ControlPlaneConfig
from devicelab.application.executions import ExecutionService
from devicelab.application.fleet import FleetService
from devicelab.application.jobs import JobService, TemplateService
from devicelab.application.lifecycle import Lifecycle
from devicelab.application.ports import (
    AgentGateway,
    AlertNotifier,
    ArtifactStore,
    Clock,
    EventPublisher,
    IdFactory,
    UowFactory,
)
from devicelab.application.reports import ReportService
from devicelab.application.schedules import ScheduleService
from devicelab.application.scheduling import SchedulerService


@dataclass(frozen=True, slots=True)
class Services:
    lifecycle: Lifecycle
    fleet: FleetService
    scheduler: SchedulerService
    executions: ExecutionService
    jobs: JobService
    templates: TemplateService
    schedules: ScheduleService
    alerts: AlertService
    reports: ReportService
    auth: AuthService


def build_services(
    *,
    uow_factory: UowFactory,
    clock: Clock,
    config: ControlPlaneConfig,
    store: ArtifactStore,
    notifier: AlertNotifier,
    events: EventPublisher,
    gateway: AgentGateway,
    ids: IdFactory = uuid4,
) -> Services:
    lifecycle = Lifecycle(config, clock, events, gateway)
    jobs = JobService(uow_factory, clock, ids, config, lifecycle, events)
    return Services(
        lifecycle=lifecycle,
        fleet=FleetService(uow_factory, clock, ids, config, lifecycle, events),
        scheduler=SchedulerService(uow_factory, clock, ids, config, lifecycle, events, gateway),
        executions=ExecutionService(uow_factory, clock, ids, config, lifecycle, events, store),
        jobs=jobs,
        templates=TemplateService(uow_factory, clock, ids),
        schedules=ScheduleService(uow_factory, clock, ids, jobs),
        alerts=AlertService(uow_factory, clock, ids, notifier, events),
        reports=ReportService(uow_factory, clock),
        auth=AuthService(uow_factory, clock, ids),
    )
