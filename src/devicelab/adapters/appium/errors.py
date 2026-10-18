from __future__ import annotations

from devicelab.domain.errors import StepFailure


def map_exception(exc: BaseException) -> StepFailure:
    """Translate Selenium/Appium exceptions into the failure codes the recovery policy uses."""
    name = type(exc).__name__
    message = " ".join(str(exc).split())[:400] or name
    lowered = message.lower()
    if isinstance(exc, StepFailure):
        return exc
    if name in ("NoSuchElementException", "ElementNotVisibleException"):
        return StepFailure(f"element not found: {message}", code="assertion_failed")
    if name == "TimeoutException":
        return StepFailure(f"element wait timed out: {message}", code="assertion_failed")
    if any(
        s in lowered
        for s in ("could not proxy", "webdriveragent", "wda", "socket hang up", "econnrefused")
    ):
        return StepFailure(message, code="wda_crashed")
    if any(
        s in lowered
        for s in (
            "unable to connect",
            "device is not",
            "not connected",
            "could not find a connected",
            "no device",
        )
    ):
        return StepFailure(message, code="device_unreachable")
    if "crash" in lowered or "not running" in lowered:
        return StepFailure(message, code="app_crashed")
    if "timed out" in lowered or "timeout" in lowered:
        return StepFailure(message, code="step_timeout")
    return StepFailure(message, code="step_failed")
