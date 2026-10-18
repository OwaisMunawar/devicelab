"""iOS Simulator provider on top of `xcrun simctl`.

Scoped by name prefix on purpose: a lab host often has the developer's own simulators on
it, and the provider must never boot, shut down or change those.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from devicelab.adapters.providers.base import (
    DeviceInfo,
    HealthSnapshot,
    Setting,
    locale_launch_args,
)
from devicelab.adapters.providers.commands import CommandRunner
from devicelab.domain.errors import SettingNotSupportedError, StepFailure

_RUNTIME = re.compile(r"SimRuntime\.iOS-(\d+)-(\d+)(?:-(\d+))?$")


class SimulatorProvider:
    kind = "simulator"

    def __init__(
        self, runner: CommandRunner, *, name_prefix: str = "DeviceLab", data_dir: Path | None = None
    ) -> None:
        self._run = runner
        self._prefix = name_prefix
        self._data_dir = data_dir or Path.home()

    async def _simctl(self, *args: str, timeout_s: float = 60.0) -> str:
        return (await self._run.run(["xcrun", "simctl", *args], timeout_s=timeout_s)).stdout

    async def _list(self) -> list[dict[str, str]]:
        raw = await self._simctl("list", "devices", "--json")
        found: list[dict[str, str]] = []
        for runtime, devices in json.loads(raw)["devices"].items():
            match = _RUNTIME.search(runtime)
            if not match:
                continue
            version = ".".join(p for p in match.groups() if p)
            for device in devices:
                if device.get("isAvailable") and device["name"].startswith(self._prefix):
                    found.append({**device, "ios_version": version})
        return found

    def _storage_gb(self) -> float:
        return round(shutil.disk_usage(self._data_dir).free / 1e9, 1)

    async def discover(self) -> list[DeviceInfo]:
        infos = []
        for d in await self._list():
            booted = d["state"] == "Booted"
            infos.append(
                DeviceInfo(
                    udid=d["udid"],
                    name=d["name"],
                    model=_model_name(d["deviceTypeIdentifier"]),
                    model_family="iPad" if "iPad" in d["deviceTypeIdentifier"] else "iPhone",
                    ios_version=d["ios_version"],
                    kind="simulator",
                    health=HealthSnapshot(
                        battery_pct=None,
                        storage_free_gb=self._storage_gb(),
                        connectivity="connected" if booted else "shutdown",
                    ),
                    labels=["simulator"],
                )
            )
        return infos

    async def health(self, udid: str) -> HealthSnapshot:
        state = next((d["state"] for d in await self._list() if d["udid"] == udid), None)
        if state is None:
            raise StepFailure(f"simulator {udid} no longer exists", code="device_unreachable")
        return HealthSnapshot(
            battery_pct=None,
            storage_free_gb=self._storage_gb(),
            connectivity="connected" if state == "Booted" else "shutdown",
        )

    async def boot(self, udid: str) -> None:
        await self._run.run(["xcrun", "simctl", "boot", udid], check=False)
        await self._simctl("bootstatus", udid, "-b", timeout_s=240.0)

    async def install_app(self, udid: str, app_path: Path) -> None:
        await self._simctl("install", udid, str(app_path), timeout_s=180.0)

    async def launch_app(self, udid: str, bundle_id: str, *, locale: str | None = None) -> None:
        extra = locale_launch_args(locale) if locale else []
        await self._simctl("launch", "--terminate-running-process", udid, bundle_id, *extra)

    async def terminate_app(self, udid: str, bundle_id: str) -> None:
        # Exit code 3 means "not running", which is the state we wanted.
        await self._run.run(["xcrun", "simctl", "terminate", udid, bundle_id], check=False)

    async def screenshot(self, udid: str) -> bytes:
        with tempfile.TemporaryDirectory(prefix="devicelab-shot-") as tmp:
            target = Path(tmp) / "screen.png"
            await self._simctl("io", udid, "screenshot", "--type=png", str(target))
            return target.read_bytes()

    async def collect_logs(self, udid: str, duration_s: int) -> bytes:
        out = await self._simctl(
            "spawn",
            udid,
            "log",
            "show",
            "--last",
            f"{duration_s}s",
            "--style",
            "compact",
            timeout_s=duration_s + 60.0,
        )
        return out.encode()

    async def set_setting(self, udid: str, setting: Setting, value: str) -> None:
        match setting:
            case "appearance":
                await self._simctl("ui", udid, "appearance", value)
            case "locale":
                language = value.replace("-", "_").split("_")[0]
                await self._simctl(
                    "spawn", udid, "defaults", "write", "-g", "AppleLanguages", "-array", language
                )
                await self._simctl("spawn", udid, "defaults", "write", "-g", "AppleLocale", value)
            case "timezone":
                raise SettingNotSupportedError(
                    "simulators follow the host time zone", code="setting_not_supported"
                )

    async def reboot(self, udid: str) -> None:
        await self._run.run(["xcrun", "simctl", "shutdown", udid], check=False)
        await self.boot(udid)


def _model_name(device_type_identifier: str) -> str:
    return device_type_identifier.rsplit(".", 1)[-1].replace("-", " ")
