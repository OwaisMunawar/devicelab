"""Physical iPhones and iPads attached over USB.

Discovery and health come from libimobiledevice (`idevice_id`, `ideviceinfo`); install and
launch go through `xcrun devicectl`, which is what Apple supports from iOS 17. Anything that
needs the UI (screenshots, taps) belongs to the Appium/WDA driver, not to this provider.

What libimobiledevice cannot tell us is reported honestly: there is no thermal reading over
lockdown, so `thermal` is always "nominal" for USB devices.
"""

from __future__ import annotations

import plistlib
from collections.abc import Collection
from pathlib import Path
from typing import Any

from devicelab.adapters.providers.base import (
    DeviceInfo,
    HealthSnapshot,
    Setting,
    locale_launch_args,
)
from devicelab.adapters.providers.commands import CommandRunner
from devicelab.domain.errors import SettingNotSupportedError, StepFailure

_MODELS = {
    "iPhone12,1": "iPhone 11",
    "iPhone13,2": "iPhone 12",
    "iPhone14,5": "iPhone 13",
    "iPhone14,7": "iPhone 14",
    "iPhone15,2": "iPhone 14 Pro",
    "iPhone15,4": "iPhone 15",
    "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro",
    "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone17,3": "iPhone 16",
    "iPhone17,1": "iPhone 16 Pro",
}


class UsbDeviceProvider:
    kind = "usb"

    def __init__(
        self,
        runner: CommandRunner,
        *,
        allow: Collection[str] | None = None,
        redact_names: bool = False,
    ) -> None:
        self._run = runner
        self._allow = frozenset(allow) if allow else None
        self._redact = redact_names

    async def _udids(self) -> list[str]:
        out = (await self._run.run(["idevice_id", "-l"])).stdout
        udids = [line.strip() for line in out.splitlines() if line.strip()]
        return [u for u in udids if self._allow is None or u in self._allow]

    async def _plist(self, udid: str, *query: str) -> dict[str, Any]:
        result = await self._run.run(["ideviceinfo", "-u", udid, *query, "-x"], timeout_s=20.0)
        try:
            parsed = plistlib.loads(result.stdout.encode())
        except Exception as exc:
            raise StepFailure(
                f"unreadable ideviceinfo output: {exc}", code="device_unreachable"
            ) from exc
        if not isinstance(parsed, dict):
            raise StepFailure("unexpected ideviceinfo output", code="device_unreachable")
        return parsed

    async def discover(self) -> list[DeviceInfo]:
        devices: list[DeviceInfo] = []
        for index, udid in enumerate(await self._udids(), start=1):
            info = await self._plist(udid)
            product = str(info.get("ProductType", "unknown"))
            family = str(info.get("DeviceClass", "iPhone"))
            name = f"USB {family} {index}" if self._redact else str(info.get("DeviceName", product))
            devices.append(
                DeviceInfo(
                    udid=udid,
                    name=name,
                    model=_MODELS.get(product, product),
                    model_family="iPad" if family == "iPad" else "iPhone",
                    ios_version=str(info.get("ProductVersion", "0")),
                    kind="usb",
                    health=await self.health(udid),
                    labels=["usb", "physical"],
                )
            )
        return devices

    async def health(self, udid: str) -> HealthSnapshot:
        if udid not in await self._udids():
            return HealthSnapshot(battery_pct=None, connectivity="unreachable")
        battery = await self._plist(udid, "-q", "com.apple.mobile.battery")
        disk = await self._plist(udid, "-q", "com.apple.disk_usage")
        free = disk.get("AmountDataAvailable")
        capacity = battery.get("BatteryCurrentCapacity")
        return HealthSnapshot(
            battery_pct=int(capacity) if isinstance(capacity, int) else None,
            thermal="nominal",
            storage_free_gb=round(free / 1e9, 1) if isinstance(free, int) else None,
        )

    async def install_app(self, udid: str, app_path: Path) -> None:
        await self._run.run(
            ["xcrun", "devicectl", "device", "install", "app", "--device", udid, str(app_path)],
            timeout_s=240.0,
        )

    async def launch_app(self, udid: str, bundle_id: str, *, locale: str | None = None) -> None:
        extra = locale_launch_args(locale) if locale else []
        await self._run.run(
            [
                "xcrun",
                "devicectl",
                "device",
                "process",
                "launch",
                "--device",
                udid,
                "--terminate-existing",
                bundle_id,
                *extra,
            ],
            timeout_s=60.0,
        )

    async def terminate_app(self, udid: str, bundle_id: str) -> None:
        raise SettingNotSupportedError(
            "terminate through the Appium session on physical devices", code="use_ui_driver"
        )

    async def screenshot(self, udid: str) -> bytes:
        raise SettingNotSupportedError(
            "screenshots on physical devices come from the Appium session", code="use_ui_driver"
        )

    async def collect_logs(self, udid: str, duration_s: int) -> bytes:
        """idevicesyslog can only record forward in time, so this listens for `duration_s`."""
        result = await self._run.run(
            ["idevicesyslog", "-u", udid],
            timeout_s=float(duration_s),
            check=False,
            allow_timeout=True,
        )
        return result.stdout.encode()

    async def set_setting(self, udid: str, setting: Setting, value: str) -> None:
        raise SettingNotSupportedError(
            f"changing {setting} on a physical device is not supported",
            code="setting_not_supported",
        )

    async def reboot(self, udid: str) -> None:
        await self._run.run(["idevicediagnostics", "-u", udid, "restart"], timeout_s=30.0)
