from __future__ import annotations

from datetime import UTC, datetime, timedelta
from random import Random

import pytest

from devicelab.domain import failures as f
from devicelab.domain.alert_rules import (
    AlertKind,
    MetricsSnapshot,
    OfflineDevice,
    Severity,
    evaluate,
    should_notify,
)
from devicelab.domain.backoff import backoff_delay
from devicelab.domain.errors import InvalidTransitionError, StaleFenceError, ValidationFailedError
from devicelab.domain.health import HealthStatus, assess_health
from devicelab.domain.leasing import check_fence, expiry_for, is_expired, next_fence, renewal_expiry
from devicelab.domain.models import DeviceState, ExecutionState, JobState
from devicelab.domain.schedules import MisfirePolicy, due_fire, next_fire, validate_cron
from devicelab.domain.state_machines import (
    is_terminal_job,
    transition_device,
    transition_execution,
    transition_job,
)
from devicelab.domain.versions import compare_versions, parse_version
from tests.domain.factories import NOW


def test_version_parsing_and_comparison() -> None:
    assert parse_version("17.5.1") == (17, 5, 1)
    assert compare_versions("17", "17.0.0") == 0
    assert compare_versions("18.2", "18.10") < 0
    with pytest.raises(ValidationFailedError):
        parse_version("seventeen")


def test_device_transitions() -> None:
    assert transition_device(DeviceState.AVAILABLE, DeviceState.LEASED) is DeviceState.LEASED
    assert transition_device(DeviceState.BUSY, DeviceState.BUSY) is DeviceState.BUSY
    with pytest.raises(InvalidTransitionError) as exc:
        transition_device(DeviceState.QUARANTINED, DeviceState.LEASED)
    assert exc.value.code == "invalid_device_transition"


def test_job_and_execution_transitions() -> None:
    assert transition_job(JobState.RUNNING, JobState.QUEUED) is JobState.QUEUED
    with pytest.raises(InvalidTransitionError):
        transition_job(JobState.SUCCEEDED, JobState.QUEUED)
    assert is_terminal_job(JobState.FAILED) and not is_terminal_job(JobState.QUEUED)
    assert transition_execution(ExecutionState.PENDING, ExecutionState.RUNNING)
    with pytest.raises(InvalidTransitionError):
        transition_execution(ExecutionState.LOST, ExecutionState.RUNNING)


def test_lease_rules() -> None:
    exp = expiry_for(NOW, 60)
    assert not is_expired(exp, NOW) and is_expired(exp, exp)
    assert next_fence(4) == 5
    check_fence(5, 5)
    with pytest.raises(StaleFenceError) as exc:
        check_fence(5, 4)
    assert exc.value.details == {"active": 5, "presented": 4}
    assert renewal_expiry(NOW, 60, exp + timedelta(hours=1), 60) == exp + timedelta(hours=1)
    assert renewal_expiry(NOW, 60, exp, 30) == exp


@pytest.mark.parametrize(
    ("code", "message", "expected"),
    [
        ("wda_crashed", "", f.FailureClass.WDA_CRASHED),
        ("step_timeout", "", f.FailureClass.TIMEOUT),
        (None, "Could not proxy command to remote server", f.FailureClass.WDA_CRASHED),
        (None, "Unable to connect to device", f.FailureClass.DEVICE_UNREACHABLE),
        (None, "app crashed on launch", f.FailureClass.APP_CRASHED),
        (None, "element 'Sign in' not found", f.FailureClass.ASSERTION_FAILED),
        (None, "operation timed out", f.FailureClass.TIMEOUT),
        ("nonsense", "???", f.FailureClass.UNKNOWN),
    ],
)
def test_classify(code: str | None, message: str, expected: f.FailureClass) -> None:
    assert f.classify(code, message) is expected


def test_step_recovery_escalates_to_reboot() -> None:
    dev = f.FailureClass.DEVICE_UNREACHABLE
    assert f.step_recovery(dev, 1, allow_reboot=True) is f.StepRecovery.RESTART_SESSION
    assert f.step_recovery(dev, 2, allow_reboot=True) is f.StepRecovery.REBOOT_DEVICE
    assert f.step_recovery(dev, 2, allow_reboot=False) is f.StepRecovery.RESTART_SESSION
    wda = f.FailureClass.WDA_CRASHED
    assert f.step_recovery(wda, 1, allow_reboot=True) is f.StepRecovery.RESTART_SESSION
    assert f.step_recovery(f.FailureClass.APP_CRASHED, 1, allow_reboot=True) is f.StepRecovery.NONE
    assert not f.is_retryable_step(f.FailureClass.ASSERTION_FAILED)
    assert f.is_retryable_step(f.FailureClass.TIMEOUT)


def test_job_outcome_policy() -> None:
    def decide(fc: f.FailureClass, attempts: int = 1, streak: int = 1) -> f.JobOutcome:
        return f.decide_job_outcome(
            fc,
            attempts=attempts,
            max_attempts=3,
            consecutive_infra_failures=streak,
            quarantine_threshold=3,
        )

    assert decide(f.FailureClass.ASSERTION_FAILED).action is f.JobAction.FAIL
    assert not decide(f.FailureClass.ASSERTION_FAILED).quarantine_device
    requeue = decide(f.FailureClass.WDA_CRASHED)
    assert requeue.action is f.JobAction.REQUEUE and requeue.exclude_device
    assert not requeue.quarantine_device
    assert decide(f.FailureClass.WDA_CRASHED, streak=3).quarantine_device
    assert decide(f.FailureClass.TIMEOUT, attempts=3).action is f.JobAction.FAIL


def test_health_assessment() -> None:
    assert assess_health(80, "nominal", 20).status is HealthStatus.OK
    assert assess_health(10, "serious", 0.5).status is HealthStatus.DEGRADED
    bad = assess_health(400, "banana", -1)
    assert bad.status is HealthStatus.CORRUPT and len(bad.reasons) == 3
    assert assess_health(None, "fair", None).status is HealthStatus.OK


def test_backoff_is_bounded_and_jittered() -> None:
    assert backoff_delay(1, base_s=1.0, cap_s=10.0, jitter=0.5, rng=lambda: 0.5) == 1.0
    assert backoff_delay(10, base_s=1.0, cap_s=10.0, jitter=0.5, rng=lambda: 0.5) == 10.0
    low = backoff_delay(3, base_s=1.0, cap_s=10.0, jitter=0.5, rng=lambda: 0.0)
    high = backoff_delay(3, base_s=1.0, cap_s=10.0, jitter=0.5, rng=lambda: 1.0)
    assert (low, high) == (2.0, 6.0)
    r = Random(1)
    assert all(
        0 <= backoff_delay(4, base_s=1.0, cap_s=10.0, jitter=0.5, rng=r.random) <= 15
        for _ in range(100)
    )


def test_cron_validation_and_next_fire() -> None:
    validate_cron("0 6 * * *", "Europe/Berlin")
    with pytest.raises(ValidationFailedError):
        validate_cron("not cron", "UTC")
    with pytest.raises(ValidationFailedError):
        validate_cron("* * * * *", "Mars/Olympus")
    # 06:00 Berlin in March (CET, UTC+1) is 05:00 UTC.
    assert next_fire("0 6 * * *", "Europe/Berlin", NOW) == datetime(2026, 3, 2, 5, 0, tzinfo=UTC)


def test_misfire_policies() -> None:
    start = datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
    # Fires hourly; the control plane was down until 03:30.
    late = datetime(2026, 3, 1, 3, 30, tzinfo=UTC)
    three = datetime(2026, 3, 1, 3, 0, tzinfo=UTC)
    assert (
        due_fire("0 * * * *", "UTC", checked_through=start, now=late, policy=MisfirePolicy.SKIP)
        is None
    )
    assert (
        due_fire("0 * * * *", "UTC", checked_through=start, now=late, policy=MisfirePolicy.RUN_ONCE)
        == three
    )
    on_time = datetime(2026, 3, 1, 3, 0, 20, tzinfo=UTC)
    assert (
        due_fire("0 * * * *", "UTC", checked_through=start, now=on_time, policy=MisfirePolicy.SKIP)
        == three
    )
    assert (
        due_fire("0 6 * * *", "UTC", checked_through=start, now=on_time, policy=MisfirePolicy.SKIP)
        is None
    )


def test_alert_rules() -> None:
    snap = MetricsSnapshot(
        now=NOW,
        offline_devices=(
            OfflineDevice("d1", "iPhone A", "berlin", NOW - timedelta(minutes=9)),
            OfflineDevice("d2", "iPhone B", "berlin", NOW - timedelta(minutes=1)),
        ),
        finished_in_window=20,
        failed_in_window=12,
        oldest_queued_wait_s=900,
        reclaimed_leases=(("l1", "iPhone A"),),
    )
    off = evaluate(AlertKind.DEVICE_OFFLINE, {"minutes": 5}, snap)
    assert [a.dedupe_key for a in off] == ["device_offline:d1"]
    rate = evaluate(AlertKind.FAILURE_RATE, {"threshold": 0.3, "min_executions": 10}, snap)
    assert rate[0].severity is Severity.CRITICAL
    assert evaluate(AlertKind.FAILURE_RATE, {"min_executions": 50}, snap) == []
    assert evaluate(AlertKind.FAILURE_RATE, {"threshold": 0.9}, snap) == []
    assert evaluate(AlertKind.QUEUE_WAIT, {"sla_s": 300}, snap)
    assert evaluate(AlertKind.QUEUE_WAIT, {"sla_s": 3000}, snap) == []
    assert evaluate(AlertKind.LEASE_RECLAIMED, {}, snap)[0].dedupe_key == "lease_reclaimed:l1"


def test_cooldown() -> None:
    assert should_notify(None, NOW, 600)
    assert not should_notify(NOW - timedelta(seconds=10), NOW, 600)
    assert should_notify(NOW - timedelta(seconds=601), NOW, 600)
