"""A deterministic simulated fleet for demo mode, scale tests and chaos tests.

Same provider interface as the real ones, so the agent cannot tell the difference. Every
random decision comes from a seeded RNG: the same seed produces the same devices, the same
flaky steps and the same disconnects.
"""

from __future__ import annotations

import asyncio
import io
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from devicelab.adapters.providers.base import (
    DeviceInfo,
    HealthSnapshot,
    Setting,
    UiSession,
)
from devicelab.domain.errors import StepFailure
from devicelab.domain.workflow import AssertText, UiAction

MODELS: tuple[tuple[str, str, str], ...] = (
    ("iPhone 15", "iPhone", "17.6"),
    ("iPhone 15 Pro", "iPhone", "18.2"),
    ("iPhone 16", "iPhone", "18.6"),
    ("iPhone 17 Pro", "iPhone", "26.0"),
    ("iPad Air", "iPad", "18.2"),
    ("iPhone 14", "iPhone", "17.6"),
)
FLAKY_CODES = ("wda_crashed", "device_unreachable", "step_timeout")


def fake_udid(site: str, index: int) -> str:
    return f"fake-{site}-{index:02d}"


@dataclass
class FakeDevice:
    udid: str
    name: str
    model: str
    family: str
    ios: str
    battery: float
    thermal: str = "nominal"
    connected: bool = True
    busy: bool = False
    appearance: str = "light"
    locale: str = "en_US"
    foreground: str | None = None
    labels: list[str] = field(default_factory=list)
    reconnect_at: float = 0.0
    # Chaos hook: report this battery value regardless of the simulated one (e.g. 400).
    force_battery: int | None = None


@dataclass
class FakeBehaviour:
    """Knobs for chaos. Defaults are calm so unit tests are boring."""

    step_failure_rate: float = 0.0
    assertion_failure_rate: float = 0.0
    disconnect_rate_per_min: float = 0.0
    disconnect_duration_s: float = 20.0
    latency_s: tuple[float, float] = (0.0, 0.0)


class FakeFleetProvider:
    kind = "fake"

    def __init__(
        self,
        site: str,
        count: int,
        *,
        seed: int = 7,
        behaviour: FakeBehaviour | None = None,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], asyncio.Future[None] | object] | None = None,
    ) -> None:
        self.site = site
        self._rng = random.Random(f"{seed}:{site}")
        self.behaviour = behaviour or FakeBehaviour()
        self._clock = clock or (lambda: asyncio.get_event_loop().time())
        self._sleep = sleep or asyncio.sleep
        self.devices: dict[str, FakeDevice] = {}
        self.calls: list[tuple[str, str]] = []
        for i in range(count):
            model, family, ios = MODELS[(i + self._rng.randrange(len(MODELS))) % len(MODELS)]
            udid = fake_udid(site, i + 1)
            self.devices[udid] = FakeDevice(
                udid=udid,
                name=f"{site.title()} {model} #{i + 1}",
                model=model,
                family=family,
                ios=ios,
                battery=self._rng.uniform(55, 100),
                labels=[
                    "simulated",
                    *(["wifi-only"] if i % 5 == 0 else []),
                    *(["dark-lab"] if i % 7 == 0 else []),
                ],
            )
        self._last_tick = self._clock()

    # -- simulation -----------------------------------------------------------------

    def tick(self) -> None:
        """Advance battery, thermals and connectivity by the wall time since the last call."""
        now = self._clock()
        minutes = max(now - self._last_tick, 0.0) / 60.0
        self._last_tick = now
        for d in self.devices.values():
            if not d.connected and now >= d.reconnect_at:
                d.connected = True
            drain = 0.6 if d.busy else 0.05
            d.battery = max(
                0.0, min(100.0, d.battery - drain * minutes + (0.0 if d.busy else 0.4 * minutes))
            )
            if d.battery < 12:
                d.battery = min(100.0, d.battery + 30 * minutes)
            heat = ("fair", "serious") if d.busy else ("nominal", "fair")
            if self._rng.random() < 0.05 * min(minutes * 6, 1.0):
                d.thermal = self._rng.choice(heat)
            if d.thermal != "nominal" and not d.busy and self._rng.random() < 0.5:
                d.thermal = "nominal"
            if (
                d.connected
                and not d.busy
                and self.behaviour.disconnect_rate_per_min > 0
                and self._rng.random() < self.behaviour.disconnect_rate_per_min * minutes
            ):
                self.disconnect(d.udid)

    def disconnect(self, udid: str, *, seconds: float | None = None) -> None:
        d = self.devices[udid]
        d.connected = False
        d.reconnect_at = self._clock() + (seconds or self.behaviour.disconnect_duration_s)

    # -- provider interface ------------------------------------------------------------

    def _info(self, d: FakeDevice) -> DeviceInfo:
        return DeviceInfo(
            udid=d.udid,
            name=d.name,
            model=d.model,
            model_family=d.family,
            ios_version=d.ios,
            kind="fake",
            health=self._snapshot(d),
            labels=list(d.labels),
        )

    @staticmethod
    def _snapshot(d: FakeDevice) -> HealthSnapshot:
        return HealthSnapshot(
            battery_pct=d.force_battery if d.force_battery is not None else round(d.battery),
            thermal=d.thermal,
            storage_free_gb=64.0,
            connectivity="connected" if d.connected else "unreachable",
        )

    async def discover(self) -> list[DeviceInfo]:
        self.tick()
        return [self._info(d) for d in self.devices.values()]

    async def health(self, udid: str) -> HealthSnapshot:
        self.tick()
        return self._snapshot(self.devices[udid])

    async def _do(self, udid: str, op: str, *, flaky: bool = True) -> FakeDevice:
        d = self.devices[udid]
        self.calls.append((udid, op))
        low, high = self.behaviour.latency_s
        if high > 0:
            await self._sleep(self._rng.uniform(low, high))  # type: ignore[misc]
        if not d.connected:
            raise StepFailure(f"device {udid} is unreachable", code="device_unreachable")
        if flaky and self._rng.random() < self.behaviour.step_failure_rate:
            code = self._rng.choice(FLAKY_CODES)
            raise StepFailure(f"simulated {code} during {op}", code=code)
        return d

    async def install_app(self, udid: str, app_path: Path) -> None:
        await self._do(udid, f"install {app_path.name}")

    async def launch_app(self, udid: str, bundle_id: str, *, locale: str | None = None) -> None:
        d = await self._do(udid, f"launch {bundle_id}")
        d.foreground = bundle_id
        if locale:
            d.locale = locale

    async def terminate_app(self, udid: str, bundle_id: str) -> None:
        d = await self._do(udid, f"terminate {bundle_id}", flaky=False)
        if d.foreground == bundle_id:
            d.foreground = None

    async def screenshot(self, udid: str) -> bytes:
        d = await self._do(udid, "screenshot")
        return render_screen(d, self._clock())

    async def collect_logs(self, udid: str, duration_s: int) -> bytes:
        d = await self._do(udid, "logs")
        lines = [
            f"{i:03d} simulated {d.model} backboardd[1]: frame {i} presented"
            for i in range(min(duration_s * 4, 60))
        ]
        return ("\n".join(lines) + "\n").encode()

    async def set_setting(self, udid: str, setting: Setting, value: str) -> None:
        d = await self._do(udid, f"set {setting}={value}")
        if setting == "appearance":
            d.appearance = value
        elif setting == "locale":
            d.locale = value

    async def reboot(self, udid: str) -> None:
        d = await self._do(udid, "reboot", flaky=False)
        d.foreground = None
        d.thermal = "nominal"
        # Like the real providers, return only once the device is back.
        d.connected = False
        await self._sleep(0.02)  # type: ignore[misc]
        d.connected = True

    # -- UI driver ---------------------------------------------------------------------

    @property
    def ui(self) -> FakeUiDriver:
        return FakeUiDriver(self)


class FakeUiSession:
    def __init__(self, provider: FakeFleetProvider, udid: str) -> None:
        self._p = provider
        self._udid = udid

    async def perform(self, action: UiAction) -> None:
        d = await self._p._do(self._udid, f"ui {action.action}")
        if (
            isinstance(action, AssertText)
            and self._p._rng.random() < self._p.behaviour.assertion_failure_rate
        ):
            raise StepFailure(
                f"assertion failed: expected text in {action.value!r} on {d.name}",
                code="assertion_failed",
            )

    async def screenshot(self) -> bytes:
        return await self._p.screenshot(self._udid)

    async def terminate_app(self, bundle_id: str) -> None:
        await self._p.terminate_app(self._udid, bundle_id)

    async def close(self) -> None:
        return None


class FakeUiDriver:
    def __init__(self, provider: FakeFleetProvider) -> None:
        self._p = provider

    async def open(self, udid: str, bundle_id: str | None) -> UiSession:
        await self._p._do(udid, "open session")
        return FakeUiSession(self._p, udid)

    async def shutdown(self) -> None:
        return None


def render_screen(device: FakeDevice, now: float) -> bytes:
    """A labelled placeholder frame. Deliberately stamped SIMULATED so it can never be
    mistaken for a capture from real hardware."""
    dark = device.appearance == "dark"
    background, foreground = (
        ((18, 18, 22), (235, 235, 240)) if dark else ((246, 246, 250), (28, 28, 32))
    )
    image = Image.new("RGB", (390, 844), background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)
    small = ImageFont.load_default(size=15)
    draw.rounded_rectangle((20, 70, 370, 150), radius=18, fill=(40, 90, 200))
    draw.text((40, 95), "SIMULATED DEVICE", fill=(255, 255, 255), font=font)
    rows = [
        device.name,
        f"{device.model} - iOS {device.ios}",
        f"app: {device.foreground or '(home screen)'}",
        f"locale: {device.locale}   appearance: {device.appearance}",
        f"battery {round(device.battery)}%   thermal {device.thermal}",
        f"t+{now:,.0f}s",
    ]
    for i, text in enumerate(rows):
        draw.text((28, 190 + i * 38), text, fill=foreground, font=small)
    for i in range(4):
        draw.rounded_rectangle(
            (28 + i * 88, 460, 100 + i * 88, 532), radius=16, fill=(90 + i * 30, 120, 200 - i * 30)
        )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


__all__ = [
    "FakeBehaviour",
    "FakeDevice",
    "FakeFleetProvider",
    "fake_udid",
    "render_screen",
]
