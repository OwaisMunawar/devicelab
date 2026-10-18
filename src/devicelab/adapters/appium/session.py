"""Appium-backed UI session. All WebDriver calls are blocking, so they run on a thread."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from collections.abc import Callable
from typing import Any

from devicelab.adapters.appium.errors import map_exception
from devicelab.domain.errors import StepFailure
from devicelab.domain.workflow import AssertText, Swipe, Tap, TypeText, UiAction, Wait

_LOCATORS = {
    "accessibility_id": "accessibility id",
    "name": "name",
    "predicate": "-ios predicate string",
    "class_chain": "-ios class chain",
    "xpath": "xpath",
}


class AppiumSession:
    def __init__(
        self, driver: Any, on_close: Callable[[], None] | None = None, *, wait_s: float = 10.0
    ) -> None:
        self._driver = driver
        self._wait_s = wait_s
        self._on_close = on_close

    def _find(self, by: str, value: str) -> Any:
        return self._driver.find_element(_LOCATORS[by], value)

    def _wait_for(self, by: str, value: str, seconds: float | None = None) -> Any:
        deadline = time.monotonic() + (self._wait_s if seconds is None else seconds)
        last: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                element = self._find(by, value)
                if element.is_displayed():
                    return element
            except Exception as exc:
                last = exc
            time.sleep(0.25)
        raise StepFailure(f"element not found: {by}={value} ({last})", code="assertion_failed")

    def _perform(self, action: UiAction) -> None:
        match action:
            case Tap():
                self._wait_for(action.by, action.value).click()
            case TypeText():
                element = self._wait_for(action.by, action.value)
                element.send_keys(action.text)
            case Swipe():
                self._driver.execute_script("mobile: swipe", {"direction": action.direction})
            case AssertText():
                element = self._wait_for(action.by, action.value)
                actual = " ".join(
                    str(x)
                    for x in (
                        element.text,
                        element.get_attribute("value"),
                        element.get_attribute("label"),
                    )
                    if x
                )
                if action.equals is not None and action.equals not in (
                    element.text,
                    element.get_attribute("value"),
                    element.get_attribute("label"),
                ):
                    raise StepFailure(
                        f"assertion failed: expected {action.equals!r}, got {actual!r}",
                        code="assertion_failed",
                    )
                if action.contains is not None and action.contains not in actual:
                    raise StepFailure(
                        f"assertion failed: {action.contains!r} not in {actual!r}",
                        code="assertion_failed",
                    )
            case Wait():
                if action.seconds is not None:
                    time.sleep(action.seconds)
                else:
                    if action.by is None or action.value is None:
                        raise StepFailure("wait needs a locator", code="step_failed")
                    self._wait_for(action.by, action.value, 30.0)

    async def perform(self, action: UiAction) -> None:
        try:
            await asyncio.to_thread(self._perform, action)
        except Exception as exc:
            raise map_exception(exc) from exc

    async def screenshot(self) -> bytes:
        try:
            encoded: str = await asyncio.to_thread(self._driver.get_screenshot_as_base64)
        except Exception as exc:
            raise map_exception(exc) from exc
        return base64.b64decode(encoded)

    async def terminate_app(self, bundle_id: str) -> None:
        try:
            await asyncio.to_thread(
                self._driver.execute_script, "mobile: terminateApp", {"bundleId": bundle_id}
            )
        except Exception as exc:
            raise map_exception(exc) from exc

    async def close(self) -> None:
        with contextlib.suppress(Exception):  # the session may already be dead
            await asyncio.to_thread(self._driver.quit)
        if self._on_close:
            self._on_close()
