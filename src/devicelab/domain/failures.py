"""Failure classification and the recovery policy that hangs off it.

The split that matters is infrastructure vs test failures. A flaky device should be
retried elsewhere and eventually benched; a failed assertion is the answer the user asked
for and must not be retried into a pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class FailureClass(StrEnum):
    DEVICE_UNREACHABLE = "device_unreachable"
    WDA_CRASHED = "wda_crashed"
    APP_CRASHED = "app_crashed"
    ASSERTION_FAILED = "assertion_failed"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


INFRASTRUCTURE = frozenset(
    {FailureClass.DEVICE_UNREACHABLE, FailureClass.WDA_CRASHED, FailureClass.TIMEOUT}
)

_BY_CODE = {c.value: c for c in FailureClass} | {"step_timeout": FailureClass.TIMEOUT}

_PATTERNS: tuple[tuple[re.Pattern[str], FailureClass], ...] = tuple(
    (re.compile(p, re.IGNORECASE), c)
    for p, c in (
        (
            r"could not proxy|webdriveragent|wda .*(crash|not respond|failed)|xcodebuild failed",
            FailureClass.WDA_CRASHED,
        ),
        (
            r"unable to connect to device|device .*(not found|disconnected|offline)"
            r"|lockdown|no route to host|connection refused",
            FailureClass.DEVICE_UNREACHABLE,
        ),
        (
            r"app .*crash|crashed on launch|process .*terminated unexpectedly"
            r"|application is not running",
            FailureClass.APP_CRASHED,
        ),
        (
            r"assert|expected .* (to|but)|element .* not (found|visible)",
            FailureClass.ASSERTION_FAILED,
        ),
        (r"timed out|timeout|deadline exceeded", FailureClass.TIMEOUT),
    )
)


def classify(code: str | None, message: str = "") -> FailureClass:
    """Trust an explicit code from the agent first; fall back to message patterns."""
    if code and code in _BY_CODE:
        return _BY_CODE[code]
    for pattern, failure_class in _PATTERNS:
        if pattern.search(message):
            return failure_class
    return FailureClass.UNKNOWN


def is_infrastructure(failure_class: FailureClass) -> bool:
    return failure_class in INFRASTRUCTURE


def is_retryable_step(failure_class: FailureClass) -> bool:
    return failure_class is not FailureClass.ASSERTION_FAILED


class StepRecovery(StrEnum):
    NONE = "none"
    RESTART_SESSION = "restart_session"
    REBOOT_DEVICE = "reboot_device"


def step_recovery(failure_class: FailureClass, attempt: int, *, allow_reboot: bool) -> StepRecovery:
    """What the agent does between retries of one step. `attempt` is the one that just failed."""
    match failure_class:
        case FailureClass.WDA_CRASHED | FailureClass.TIMEOUT:
            return StepRecovery.RESTART_SESSION
        case FailureClass.DEVICE_UNREACHABLE:
            if attempt >= 2 and allow_reboot:
                return StepRecovery.REBOOT_DEVICE
            return StepRecovery.RESTART_SESSION
        case _:
            return StepRecovery.NONE


class JobAction(StrEnum):
    REQUEUE = "requeue"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class JobOutcome:
    action: JobAction
    exclude_device: bool
    quarantine_device: bool
    reason: str


def decide_job_outcome(
    failure_class: FailureClass,
    *,
    attempts: int,
    max_attempts: int,
    consecutive_infra_failures: int,
    quarantine_threshold: int,
) -> JobOutcome:
    """`consecutive_infra_failures` already includes this failure."""
    infra = is_infrastructure(failure_class)
    quarantine = infra and consecutive_infra_failures >= quarantine_threshold
    if not infra:
        return JobOutcome(JobAction.FAIL, False, False, f"{failure_class.value} is a test failure")
    if attempts >= max_attempts:
        return JobOutcome(JobAction.FAIL, True, quarantine, "attempts exhausted")
    return JobOutcome(JobAction.REQUEUE, True, quarantine, "infrastructure failure")
