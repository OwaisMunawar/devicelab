"""Device-provider ports. A provider knows how to find devices and do device-level things
(install, launch, screenshot, logs); a UiDriver knows how to drive the UI of one device."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

from devicelab.domain.workflow import UiAction

Setting = Literal["appearance", "locale", "timezone"]


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    battery_pct: int | None
    thermal: str = "nominal"
    storage_free_gb: float | None = None
    connectivity: str = "connected"


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    udid: str
    name: str
    model: str
    model_family: str
    ios_version: str
    kind: Literal["simulator", "usb", "fake"]
    health: HealthSnapshot
    labels: list[str] = field(default_factory=list)


class DeviceProvider(Protocol):
    kind: str

    async def discover(self) -> list[DeviceInfo]: ...
    async def health(self, udid: str) -> HealthSnapshot: ...
    async def install_app(self, udid: str, app_path: Path) -> None: ...
    async def launch_app(self, udid: str, bundle_id: str, *, locale: str | None = None) -> None: ...
    async def terminate_app(self, udid: str, bundle_id: str) -> None: ...
    async def screenshot(self, udid: str) -> bytes: ...
    async def collect_logs(self, udid: str, duration_s: int) -> bytes: ...
    async def set_setting(self, udid: str, setting: Setting, value: str) -> None: ...
    async def reboot(self, udid: str) -> None: ...


class UiSession(Protocol):
    async def perform(self, action: UiAction) -> None: ...
    async def screenshot(self) -> bytes: ...
    async def terminate_app(self, bundle_id: str) -> None: ...
    async def close(self) -> None: ...


class UiDriver(Protocol):
    """Opens UI automation sessions (Appium/WDA in production)."""

    async def open(self, udid: str, bundle_id: str | None) -> UiSession: ...
    async def shutdown(self) -> None: ...


def locale_launch_args(locale: str) -> list[str]:
    """Per-app language override, understood by every iOS app at launch."""
    language = locale.replace("_", "-").split("-")[0]
    region = locale.replace("-", "_")
    return ["-AppleLanguages", f"({language})", "-AppleLocale", region]
