"""Appium 3 + XCUITest/WebDriverAgent driver. One shared Appium server per host; one
WebDriver session per device. Cache locations are injected so everything the driver
creates lives where the operator decided, normally the repo-local .cache/."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog

from devicelab.adapters.appium.errors import map_exception
from devicelab.adapters.appium.session import AppiumSession
from devicelab.adapters.providers.base import UiSession

log = structlog.get_logger("appium")


@dataclass(frozen=True, slots=True)
class AppiumConfig:
    appium_home: Path
    derived_data_path: Path
    server_url: str = "http://127.0.0.1:4723"
    appium_binary: Path | None = None
    # Real devices need signing; both come from the environment, never from the repo.
    team_id: str | None = None
    signing_id: str = "Apple Development"
    wda_bundle_id: str = "com.devicelab.WebDriverAgentRunner"
    wda_launch_timeout_ms: int = 240_000
    prebuilt_wda: bool = False
    extra_caps: dict[str, Any] = field(default_factory=dict)


def wda_port(udid: str) -> int:
    """Stable per-device port so concurrent sessions on one host never collide."""
    return 8100 + int(hashlib.sha256(udid.encode()).hexdigest(), 16) % 800


def default_driver_factory(url: str, caps: dict[str, Any]) -> Any:
    from appium import webdriver  # noqa: PLC0415 - heavy import, only on hosts that run Appium
    from appium.options.common import AppiumOptions  # noqa: PLC0415

    options = AppiumOptions()
    options.load_capabilities(caps)
    return webdriver.Remote(url, options=options)


class AppiumUiDriver:
    def __init__(
        self,
        config: AppiumConfig,
        *,
        driver_factory: Callable[[str, dict[str, Any]], Any] = default_driver_factory,
        is_real_device: Callable[[str], bool] = lambda udid: udid.count("-") != 4,
    ) -> None:
        self._cfg = config
        self._factory = driver_factory
        self._is_real = is_real_device
        self._proc: asyncio.subprocess.Process | None = None

    def capabilities(self, udid: str, bundle_id: str | None) -> dict[str, Any]:
        cfg = self._cfg
        caps: dict[str, Any] = {
            "platformName": "iOS",
            "appium:automationName": "XCUITest",
            "appium:udid": udid,
            "appium:wdaLocalPort": wda_port(udid),
            "appium:derivedDataPath": str(cfg.derived_data_path),
            "appium:usePrebuiltWDA": cfg.prebuilt_wda,
            "appium:wdaLaunchTimeout": cfg.wda_launch_timeout_ms,
            "appium:noReset": True,
            "appium:newCommandTimeout": 300,
            "appium:showXcodeLog": False,
        }
        if bundle_id:
            caps["appium:bundleId"] = bundle_id
        if self._is_real(udid):
            caps["appium:updatedWDABundleId"] = cfg.wda_bundle_id
            caps["appium:allowProvisioningUpdates"] = True
            caps["appium:xcodeSigningId"] = cfg.signing_id
            if cfg.team_id:
                caps["appium:xcodeOrgId"] = cfg.team_id
        return {**caps, **cfg.extra_caps}

    async def ensure_server(self) -> None:
        if await self._server_up():
            return
        binary = self._cfg.appium_binary or shutil.which("appium")
        if binary is None:
            raise RuntimeError(
                "appium binary not found; install it into APPIUM_HOME or set the path"
            )
        env = {**os.environ, "APPIUM_HOME": str(self._cfg.appium_home)}
        port = self._cfg.server_url.rsplit(":", 1)[-1]
        self._proc = await asyncio.create_subprocess_exec(
            str(binary),
            "--port",
            port,
            "--address",
            "127.0.0.1",
            "--log-level",
            "warn",
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        for _ in range(120):
            if await self._server_up():
                log.info("appium_started", port=port)
                return
            await asyncio.sleep(0.5)
        raise RuntimeError("appium server did not come up")

    async def _server_up(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                return (await client.get(f"{self._cfg.server_url}/status")).status_code == 200
        except httpx.HTTPError:
            return False

    async def open(self, udid: str, bundle_id: str | None) -> UiSession:
        await self.ensure_server()
        caps = self.capabilities(udid, bundle_id)
        try:
            driver = await asyncio.to_thread(self._factory, self._cfg.server_url, caps)
        except Exception as exc:
            raise map_exception(exc) from exc
        return AppiumSession(driver)

    async def shutdown(self) -> None:
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), 10)
            except TimeoutError:
                self._proc.kill()
        self._proc = None
