"""Runs one assignment: step retries with backoff, session recovery, failure policy."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import random
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from devicelab.agent.executor import StepContext, StepExecutor
from devicelab.domain.backoff import backoff_delay
from devicelab.domain.errors import DomainError, StepFailure
from devicelab.domain.failures import (
    FailureClass,
    StepRecovery,
    classify,
    is_retryable_step,
    step_recovery,
)
from devicelab.domain.workflow import Step
from devicelab.protocol.messages import (
    ArtifactRef,
    ArtifactUpload,
    Assign,
    ExecutionFinished,
    StepFinished,
    StepLog,
    StepStarted,
)

Emit = Callable[[BaseModel], None]
Sleep = Callable[[float], Awaitable[None]]


class ExecutionRunner:
    def __init__(
        self,
        executor: StepExecutor,
        emit: Emit,
        *,
        rng: random.Random | None = None,
        sleep: Sleep = asyncio.sleep,
        allow_reboot: bool = True,
    ) -> None:
        self._executor = executor
        self._emit = emit
        self._rng = rng or random.Random()
        self._sleep = sleep
        self._allow_reboot = allow_reboot

    async def run(self, assign: Assign, cancel: asyncio.Event) -> None:
        eid, token = assign.execution_id, assign.fencing_token
        restarts = reboots = 0
        first_failure: tuple[str, str] | None = None
        aborted = False

        def log(level: str, message: str, index: int | None = None) -> None:
            self._emit(
                StepLog(
                    execution_id=eid,
                    fencing_token=token,
                    step_index=index,
                    level=level,
                    message=message,
                )
            )

        ctx = StepContext(udid=assign.device_udid, log=log)
        try:
            for index, step in enumerate(assign.steps):
                if cancel.is_set():
                    break
                if aborted and not step.always:
                    self._finished(assign, index, "skipped")
                    continue
                outcome = await self._run_step(assign, ctx, index, step, cancel, log)
                restarts += outcome.restarts
                reboots += outcome.reboots
                if cancel.is_set():
                    break
                if outcome.failure is not None:
                    if first_failure is None and not step.continue_on_error:
                        first_failure = outcome.failure
                    if not step.continue_on_error and assign.failure_policy == "abort":
                        aborted = True
        finally:
            with contextlib.suppress(Exception):
                await self._executor.close(ctx)

        if cancel.is_set():
            state, code, error = "cancelled", None, "cancelled by control plane"
        elif first_failure is not None:
            state, (code, error) = "failed", first_failure
        else:
            state, code, error = "succeeded", None, None
        self._emit(
            ExecutionFinished(
                execution_id=eid,
                fencing_token=token,
                state=state,
                failure_code=code,
                error=error,
                session_restarts=restarts,
                reboots=reboots,
            )
        )

    def _finished(self, assign: Assign, index: int, state: str, **extra: object) -> None:
        self._emit(
            StepFinished(
                execution_id=assign.execution_id,
                fencing_token=assign.fencing_token,
                step_index=index,
                state=state,
                **extra,
            )
        )

    async def _run_step(
        self,
        assign: Assign,
        ctx: StepContext,
        index: int,
        step: Step,
        cancel: asyncio.Event,
        log: Callable[..., None],
    ) -> _StepOutcome:
        eid, token = assign.execution_id, assign.fencing_token
        outcome = _StepOutcome()
        policy = step.retry
        for attempt in range(1, policy.max_attempts + 1):
            self._emit(
                StepStarted(
                    execution_id=eid,
                    fencing_token=token,
                    step_index=index,
                    step_id=step.id,
                    attempt=attempt,
                )
            )
            ctx.produced.clear()
            started = time.monotonic()
            log("info", f"{step.id}: {step.type} (attempt {attempt}/{policy.max_attempts})", index)
            try:
                await self._with_timeout(self._executor.run(ctx, step), step.timeout_s, cancel)
            except asyncio.CancelledError:
                raise
            except (StepFailure, DomainError) as exc:
                failure = exc
            except Exception as exc:  # a bug or driver error must fail the step, not the agent
                failure = StepFailure(f"{type(exc).__name__}: {exc}", code="step_failed")
            else:
                for item in ctx.produced:
                    self._emit(
                        ArtifactUpload(
                            execution_id=eid,
                            fencing_token=token,
                            step_index=index,
                            artifact=ArtifactRef(
                                name=item.name, kind=item.kind, content_type=item.content_type
                            ),
                            data_b64=base64.b64encode(item.data).decode(),
                        )
                    )
                elapsed_ms = int((time.monotonic() - started) * 1000)
                log("info", f"{step.id}: done in {elapsed_ms} ms", index)
                self._finished(
                    assign,
                    index,
                    "succeeded",
                    attempts=attempt,
                    duration_ms=elapsed_ms,
                    output={"artifacts": [p.name for p in ctx.produced]},
                )
                return outcome

            if cancel.is_set():
                return outcome
            failure_class = classify(failure.code, failure.message)
            last = attempt >= policy.max_attempts or not is_retryable_step(failure_class)
            log(
                "warning",
                f"{step.id} attempt {attempt} failed [{failure_class.value}]: {failure.message}",
                index,
            )
            if last:
                self._finished(
                    assign,
                    index,
                    "failed",
                    attempts=attempt,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    failure_code=failure_class.value,
                    error=failure.message,
                )
                outcome.failure = (failure_class.value, failure.message)
                return outcome

            await self._recover(ctx, failure_class, attempt, outcome, log, index)
            delay = backoff_delay(
                attempt,
                base_s=policy.backoff_base_s,
                cap_s=policy.backoff_max_s,
                jitter=policy.jitter,
                rng=self._rng.random,
            )
            log("info", f"retrying {step.id} in {delay:.1f}s", index)
            await self._sleep(delay)
        return outcome

    async def _recover(
        self,
        ctx: StepContext,
        failure_class: FailureClass,
        attempt: int,
        outcome: _StepOutcome,
        log: Callable[..., None],
        index: int,
    ) -> None:
        action = step_recovery(failure_class, attempt, allow_reboot=self._allow_reboot)
        try:
            if action is StepRecovery.RESTART_SESSION:
                log("info", "recovery: restarting the automation session", index)
                await self._executor.restart_session(ctx)
                outcome.restarts += 1
            elif action is StepRecovery.REBOOT_DEVICE:
                log("warning", "recovery: rebooting the device", index)
                await self._executor.reboot(ctx)
                outcome.reboots += 1
        except Exception as exc:
            log("error", f"recovery failed: {exc}", index)

    @staticmethod
    async def _with_timeout(work: Awaitable[None], timeout_s: float, cancel: asyncio.Event) -> None:
        task = asyncio.ensure_future(work)
        waiter = asyncio.ensure_future(cancel.wait())
        try:
            done, _ = await asyncio.wait(
                {task, waiter}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
            )
            if task in done:
                task.result()
                return
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            if waiter in done:
                return
            raise StepFailure(f"step exceeded its {timeout_s:g}s timeout", code="step_timeout")
        finally:
            waiter.cancel()
            if not task.done():
                task.cancel()


class _StepOutcome:
    def __init__(self) -> None:
        self.failure: tuple[str, str] | None = None
        self.restarts = 0
        self.reboots = 0
