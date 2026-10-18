from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest

from devicelab.adapters.appium.driver import AppiumConfig, AppiumUiDriver, wda_port
from devicelab.adapters.appium.errors import map_exception
from devicelab.adapters.appium.session import AppiumSession
from devicelab.domain.errors import StepFailure
from devicelab.domain.workflow import AssertText, Swipe, Tap, TypeText, Wait


class NoSuchElementException(Exception):
    pass


class TimeoutException(Exception):
    pass


class Element:
    def __init__(self, text: str = "15", displayed: bool = True) -> None:
        self.text, self.displayed, self.clicked, self.typed = text, displayed, False, ""

    def is_displayed(self) -> bool:
        return self.displayed

    def click(self) -> None:
        self.clicked = True

    def send_keys(self, value: str) -> None:
        self.typed += value

    def get_attribute(self, name: str) -> str | None:
        return {"value": self.text, "label": None}.get(name)


class StubDriver:
    def __init__(self) -> None:
        self.elements: dict[tuple[str, str], Element] = {("accessibility id", "result"): Element()}
        self.scripts: list[tuple[str, dict[str, Any]]] = []
        self.quit_called = False

    def find_element(self, by: str, value: str) -> Element:
        try:
            return self.elements[(by, value)]
        except KeyError:
            raise NoSuchElementException(f"{by}={value}") from None

    def execute_script(self, script: str, args: dict[str, Any]) -> None:
        self.scripts.append((script, args))

    def get_screenshot_as_base64(self) -> str:
        return base64.b64encode(b"\x89PNGpixels").decode()

    def quit(self) -> None:
        self.quit_called = True


async def test_session_actions() -> None:
    driver = StubDriver()
    driver.elements[("name", "7")] = Element()
    driver.elements[("accessibility id", "field")] = Element("")
    session = AppiumSession(driver)
    await session.perform(Tap(action="tap", by="name", value="7"))
    assert driver.elements[("name", "7")].clicked
    await session.perform(TypeText(action="type", value="field", text="hello"))
    assert driver.elements[("accessibility id", "field")].typed == "hello"
    await session.perform(Swipe(action="swipe", direction="up"))
    assert driver.scripts[-1] == ("mobile: swipe", {"direction": "up"})
    await session.perform(AssertText(action="assert_text", value="result", equals="15"))
    await session.perform(AssertText(action="assert_text", value="result", contains="1"))
    await session.perform(Wait(action="wait", seconds=0.01))
    await session.perform(Wait(action="wait", by="accessibility_id", value="result"))
    assert (await session.screenshot()).startswith(b"\x89PNG")
    await session.terminate_app("com.apple.calculator")
    assert driver.scripts[-1] == ("mobile: terminateApp", {"bundleId": "com.apple.calculator"})
    await session.close()
    assert driver.quit_called


async def test_session_failures_are_classified() -> None:
    driver = StubDriver()
    session = AppiumSession(driver)
    with pytest.raises(StepFailure) as wrong:
        await session.perform(AssertText(action="assert_text", value="result", equals="16"))
    assert wrong.value.code == "assertion_failed" and "'16'" in wrong.value.message
    with pytest.raises(StepFailure) as contains:
        await session.perform(AssertText(action="assert_text", value="result", contains="99"))
    assert contains.value.code == "assertion_failed"

    quick = AppiumSession(driver, wait_s=0.3)
    with pytest.raises(StepFailure) as missing:
        await quick.perform(Tap(action="tap", value="nowhere"))
    assert missing.value.code == "assertion_failed"

    class Dead(StubDriver):
        def get_screenshot_as_base64(self) -> str:
            raise RuntimeError("Could not proxy command to the remote server")

        def quit(self) -> None:
            raise RuntimeError("already gone")

    dead = AppiumSession(Dead())
    with pytest.raises(StepFailure) as proxy:
        await dead.screenshot()
    assert proxy.value.code == "wda_crashed"
    await dead.close()  # swallowing errors on close is deliberate


@pytest.mark.parametrize(
    ("exception", "code"),
    [
        (NoSuchElementException("x"), "assertion_failed"),
        (TimeoutException("x"), "assertion_failed"),
        (RuntimeError("WebDriverAgent is not responding"), "wda_crashed"),
        (RuntimeError("socket hang up"), "wda_crashed"),
        (RuntimeError("Unable to connect to device"), "device_unreachable"),
        (RuntimeError("The app crashed"), "app_crashed"),
        (RuntimeError("operation timed out"), "step_timeout"),
        (RuntimeError("???"), "step_failed"),
        (StepFailure("already typed", code="x_code"), "x_code"),
        (RuntimeError(""), "step_failed"),
    ],
)
def test_exception_mapping(exception: Exception, code: str) -> None:
    assert map_exception(exception).code == code


def test_capabilities_for_simulator_and_physical_device(tmp_path: Path) -> None:
    cfg = AppiumConfig(
        appium_home=tmp_path / "home", derived_data_path=tmp_path / "dd", team_id="TEAM123"
    )
    driver = AppiumUiDriver(cfg)
    sim = driver.capabilities("11111111-2222-3333-4444-555555555555", "com.apple.Preferences")
    assert sim["appium:derivedDataPath"] == str(tmp_path / "dd")
    assert sim["appium:bundleId"] == "com.apple.Preferences"
    assert "appium:xcodeOrgId" not in sim and "appium:updatedWDABundleId" not in sim

    real = driver.capabilities("00008000-000000000000000A", None)
    assert real["appium:xcodeOrgId"] == "TEAM123" and real["appium:allowProvisioningUpdates"]
    assert real["appium:updatedWDABundleId"] == "com.devicelab.WebDriverAgentRunner"
    assert "appium:bundleId" not in real
    assert 8100 <= real["appium:wdaLocalPort"] < 8900
    assert wda_port("a") == wda_port("a") != wda_port("b")


async def test_driver_opens_sessions_through_the_factory(tmp_path: Path) -> None:
    seen: list[dict[str, Any]] = []

    def factory(url: str, caps: dict[str, Any]) -> StubDriver:
        seen.append(caps)
        return StubDriver()

    driver = AppiumUiDriver(
        AppiumConfig(
            appium_home=tmp_path, derived_data_path=tmp_path, server_url="http://127.0.0.1:9"
        ),
        driver_factory=factory,
    )

    async def up() -> bool:
        return True

    driver._server_up = up  # type: ignore[method-assign]
    session = await driver.open("11111111-2222-3333-4444-555555555555", None)
    assert seen and isinstance(session, AppiumSession)
    await driver.shutdown()

    def broken(url: str, caps: dict[str, Any]) -> StubDriver:
        raise RuntimeError("Could not find a connected device")

    failing = AppiumUiDriver(
        AppiumConfig(appium_home=tmp_path, derived_data_path=tmp_path), driver_factory=broken
    )
    failing._server_up = up  # type: ignore[method-assign]
    with pytest.raises(StepFailure) as exc:
        await failing.open("x", None)
    assert exc.value.code == "device_unreachable"
