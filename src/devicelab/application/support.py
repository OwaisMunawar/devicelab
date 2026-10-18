from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from devicelab.application.ports import Event, UnitOfWork
from devicelab.application.records import AuditRecord
from devicelab.domain.models import Role


@dataclass(frozen=True, slots=True)
class Actor:
    name: str
    role: Role
    correlation_id: str | None = None


SYSTEM = Actor("system", Role.ADMIN)


async def audit(
    uow: UnitOfWork,
    actor: Actor,
    action: str,
    target_type: str,
    target_id: object,
    now: Any,
    **details: Any,
) -> None:
    await uow.audit.add(
        AuditRecord(
            ts=now,
            actor=actor.name,
            action=action,
            target_type=target_type,
            target_id=str(target_id),
            details=details,
            correlation_id=actor.correlation_id,
        )
    )


def emit(uow: UnitOfWork, publisher: Any, topic: str, **data: Any) -> None:
    """Queue a dashboard event that is only published if the transaction commits."""
    event = Event(topic, data)
    uow.on_commit(lambda: publisher.publish(event))
