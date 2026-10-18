from __future__ import annotations

from collections.abc import Mapping

from devicelab.domain.errors import InvalidTransitionError
from devicelab.domain.models import DeviceState, ExecutionState, JobState

D = DeviceState
J = JobState
E = ExecutionState

DEVICE_TRANSITIONS: Mapping[DeviceState, frozenset[DeviceState]] = {
    D.AVAILABLE: frozenset({D.LEASED, D.OFFLINE, D.QUARANTINED, D.MAINTENANCE}),
    D.LEASED: frozenset({D.BUSY, D.AVAILABLE, D.OFFLINE, D.QUARANTINED, D.MAINTENANCE}),
    D.BUSY: frozenset({D.AVAILABLE, D.OFFLINE, D.QUARANTINED, D.MAINTENANCE}),
    D.OFFLINE: frozenset({D.AVAILABLE, D.QUARANTINED, D.MAINTENANCE}),
    # A quarantined device stays put until an operator returns it to service.
    D.QUARANTINED: frozenset({D.AVAILABLE, D.MAINTENANCE}),
    D.MAINTENANCE: frozenset({D.AVAILABLE, D.OFFLINE, D.QUARANTINED}),
}

JOB_TRANSITIONS: Mapping[JobState, frozenset[JobState]] = {
    J.QUEUED: frozenset({J.LEASED, J.CANCELLED}),
    J.LEASED: frozenset({J.RUNNING, J.QUEUED, J.FAILED, J.CANCELLED}),
    J.RUNNING: frozenset({J.SUCCEEDED, J.FAILED, J.QUEUED, J.CANCELLED}),
    J.SUCCEEDED: frozenset(),
    J.FAILED: frozenset(),
    J.CANCELLED: frozenset(),
}

EXECUTION_TRANSITIONS: Mapping[ExecutionState, frozenset[ExecutionState]] = {
    E.PENDING: frozenset({E.RUNNING, E.CANCELLED, E.LOST, E.FAILED}),
    E.RUNNING: frozenset({E.SUCCEEDED, E.FAILED, E.CANCELLED, E.LOST}),
    E.SUCCEEDED: frozenset(),
    E.FAILED: frozenset(),
    E.CANCELLED: frozenset(),
    E.LOST: frozenset(),
}


def _check[S](table: Mapping[S, frozenset[S]], kind: str, current: S, target: S) -> S:
    if current == target:
        return target
    if target not in table[current]:
        raise InvalidTransitionError(
            f"{kind} cannot move from {current} to {target}",
            code=f"invalid_{kind}_transition",
            details={"from": str(current), "to": str(target)},
        )
    return target


def transition_device(current: DeviceState, target: DeviceState) -> DeviceState:
    return _check(DEVICE_TRANSITIONS, "device", current, target)


def transition_job(current: JobState, target: JobState) -> JobState:
    return _check(JOB_TRANSITIONS, "job", current, target)


def transition_execution(current: ExecutionState, target: ExecutionState) -> ExecutionState:
    return _check(EXECUTION_TRANSITIONS, "execution", current, target)


def is_terminal_job(state: JobState) -> bool:
    return not JOB_TRANSITIONS[state]
