from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from devicelab.adapters.providers.fake import FakeFleetProvider
from devicelab.agent.executor import StepExecutor
from devicelab.agent.runner import ExecutionRunner
from devicelab.domain.errors import StepFailure
from devicelab.domain.workflow import parse_template
from devicelab.protocol.messages import (
    ArtifactUpload,
    Assign,
    ExecutionFinished,
    StepFinished,
    StepLog,
    StepStarted,
)


class ScriptedProvider(FakeFleetProvider):
    """Fails specific operations on demand, then behaves."""

    def __init__(self) -> None:
        super().__init__("test", 1)
        self.script: list[str] = []
        self.delay = 0.0

    async def _do(self, udid: str, op: str, *, flaky: bool = True):  # type: ignore[no-untyped-def]
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.script and flaky:
            raise StepFailure(f"scripted {self.script[0]}", code=self.script.pop(0))
        return await super()._do(udid, op, flaky=False)


class Harness:
    def __init__(self, yaml_text: str, tmp_path: Path | None = None, **assign_kw: Any) -> None:
        self.provider = ScriptedProvider()
        self.udid = next(iter(self.provider.devices))
        self.sent: list[BaseModel] = []
        self.sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            self.sleeps.append(seconds)

        self.executor = StepExecutor(self.provider, self.provider.ui, tmp_path)
        self.runner = ExecutionRunner(
            self.executor, self.sent.append, rng=random.Random(1), sleep=sleep
        )
        template = parse_template(yaml_text)
        self.assign = Assign(
            execution_id="e1",
            job_id="j1",
            lease_id="l1",
            fencing_token=7,
            attempt=1,
            device_udid=self.udid,
            failure_policy=assign_kw.get("policy", template.failure_policy),
            steps=template.steps,
        )
        self.cancel = asyncio.Event()

    async def run(self) -> ExecutionFinished:
        await self.runner.run(self.assign, self.cancel)
        final = self.sent[-1]
        assert isinstance(final, ExecutionFinished)
        return final

    def of(self, kind: type[BaseModel]) -> list[Any]:
        return [m for m in self.sent if isinstance(m, kind)]

    def states(self) -> list[tuple[int, str]]:
        return [(m.step_index, m.state) for m in self.of(StepFinished)]


FLOW = """
name: runner-flow
steps:
  - {id: open, type: launch_app, bundle_id: com.apple.Preferences, retry: {max_attempts: 3, backoff_base_s: 2}}
  - {id: shot, type: screenshot, artifact_name: home}
  - {id: close, type: terminate_app, bundle_id: com.apple.Preferences, always: true}
"""


async def test_successful_run_reports_every_step_and_the_artifact() -> None:
    h = Harness(FLOW)
    final = await h.run()
    assert final.state == "succeeded" and final.fencing_token == 7
    assert h.states() == [(0, "succeeded"), (1, "succeeded"), (2, "succeeded")]
    (artifact,) = h.of(ArtifactUpload)
    assert artifact.artifact.name == "home.png" and artifact.fencing_token == 7
    assert all(getattr(m, "fencing_token", 7) == 7 for m in h.sent)
    assert h.provider.calls[0][1].startswith("launch")


async def test_wda_crash_restarts_the_session_and_retries_with_backoff() -> None:
    h = Harness(FLOW)
    h.provider.script = ["wda_crashed", "wda_crashed"]
    final = await h.run()
    assert final.state == "succeeded" and final.session_restarts == 2
    assert [s.attempt for s in h.of(StepStarted)][:3] == [1, 2, 3]
    assert len(h.sleeps) == 2 and 1.0 <= h.sleeps[0] <= 3.0 and h.sleeps[1] > h.sleeps[0] * 1.2
    assert any("restarting the automation session" in m.message for m in h.of(StepLog))


async def test_unreachable_device_escalates_to_reboot() -> None:
    h = Harness(FLOW)
    h.provider.script = ["device_unreachable", "device_unreachable"]
    final = await h.run()
    assert final.state == "succeeded" and final.reboots == 1 and final.session_restarts == 1


async def test_assertion_failure_is_final_and_cleanup_still_runs() -> None:
    h = Harness(FLOW)
    h.provider.script = ["assertion_failed"]
    final = await h.run()
    assert final.state == "failed" and final.failure_code == "assertion_failed"
    assert h.states() == [(0, "failed"), (1, "skipped"), (2, "succeeded")]
    assert len(h.of(StepStarted)) == 2  # no retry of an assertion


async def test_exhausted_retries_fail_the_execution() -> None:
    h = Harness(FLOW)
    h.provider.script = ["wda_crashed"] * 3
    final = await h.run()
    assert final.state == "failed" and final.failure_code == "wda_crashed"
    assert h.of(StepFinished)[0].attempts == 3


async def test_continue_on_error_and_failure_policy() -> None:
    tolerant = """
name: tolerant
steps:
  - {id: flaky, type: launch_app, bundle_id: com.apple.Preferences, continue_on_error: true}
  - {id: shot, type: screenshot}
"""
    h = Harness(tolerant)
    h.provider.script = ["app_crashed"]
    assert (await h.run()).state == "succeeded"
    assert h.states() == [(0, "failed"), (1, "succeeded")]

    keep_going = Harness(
        FLOW.replace("name: runner-flow", "name: runner-flow\nfailure_policy: continue").replace(
            ", retry: {max_attempts: 3, backoff_base_s: 2}", ""
        )
    )
    keep_going.provider.script = ["app_crashed"]
    final = await keep_going.run()
    assert final.state == "failed" and final.failure_code == "app_crashed"
    assert keep_going.states() == [(0, "failed"), (1, "succeeded"), (2, "succeeded")]


async def test_step_timeout() -> None:
    h = Harness("name: slow\nsteps: [{id: slow, type: screenshot, timeout_s: 1}]")
    h.provider.delay = 1.5
    final = await h.run()
    assert final.state == "failed" and final.failure_code == "timeout"


async def test_cancel_stops_the_run() -> None:
    h = Harness(FLOW)
    h.provider.delay = 5.0
    task = asyncio.create_task(h.run())
    await asyncio.sleep(0.2)
    h.cancel.set()
    final = await asyncio.wait_for(task, 3)
    assert final.state == "cancelled"


async def test_install_is_confined_to_the_app_root(tmp_path: Path) -> None:
    (tmp_path / "Demo.app").mkdir()
    ok = Harness("name: inst\nsteps: [{id: i, type: install_app, app: Demo.app}]", tmp_path)
    assert (await ok.run()).state == "succeeded"
    for bad in ("../outside.app", "/etc/passwd", "Missing.app"):
        h = Harness(f"name: inst\nsteps: [{{id: i, type: install_app, app: '{bad}'}}]", tmp_path)
        final = await h.run()
        assert final.state == "failed", bad
    no_root = Harness("name: inst\nsteps: [{id: i, type: install_app, app: Demo.app}]")
    assert (await no_root.run()).state == "failed"


async def test_screenshot_matrix_and_logs_and_settings() -> None:
    h = Harness(
        """
name: matrix
steps:
  - {id: set_dark, type: set_device_setting, setting: appearance, value: dark}
  - id: matrix
    type: capture_screenshots
    bundle_id: com.example.app
    locales: [en_US, de_DE]
    appearances: [light, dark]
    prefix: store
    before_capture: [{action: wait, seconds: 1}, {action: tap, value: Continue}]
  - {id: logs, type: collect_logs, duration_s: 2}
  - {id: ui, type: appium_actions, actions: [{action: swipe, direction: up}]}
  - {id: reboot, type: reboot_device}
"""
    )
    final = await h.run()
    assert final.state == "succeeded"
    names = sorted(a.artifact.name for a in h.of(ArtifactUpload))
    assert names == [
        "device-log.log",
        "store-de_DE-dark.png",
        "store-de_DE-light.png",
        "store-en_US-dark.png",
        "store-en_US-light.png",
    ]


async def test_unexpected_driver_errors_fail_the_step_not_the_agent() -> None:
    h = Harness("name: boom\nsteps: [{id: s, type: screenshot}]")

    async def explode(*_: Any, **__: Any) -> bytes:
        raise RuntimeError("driver bug")

    h.provider.screenshot = explode  # type: ignore[method-assign]
    final = await h.run()
    assert final.state == "failed" and "driver bug" in (final.error or "")


async def test_executor_without_a_ui_driver_reports_not_supported() -> None:
    provider = ScriptedProvider()
    h = Harness(
        "name: ui\nsteps: [{id: s, type: appium_actions, actions: [{action: swipe, direction: up}]}]"
    )
    h.executor = StepExecutor(provider, None)
    h.runner = ExecutionRunner(h.executor, h.sent.append)
    final = await h.run()
    assert final.state == "failed" and "no UI driver" in (final.error or "")
