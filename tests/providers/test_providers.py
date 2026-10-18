from __future__ import annotations

from pathlib import Path

import pytest

from devicelab.adapters.providers.base import locale_launch_args
from devicelab.adapters.providers.commands import CommandResult, SubprocessRunner
from devicelab.adapters.providers.fake import FakeBehaviour, FakeFleetProvider
from devicelab.adapters.providers.simulator import SimulatorProvider
from devicelab.adapters.providers.usb import UsbDeviceProvider
from devicelab.domain.errors import SettingNotSupportedError, StepFailure
from tests.providers.recorded import RecordedRunner, fixture

SIM = "11111111-2222-3333-4444-555555555555"
USB = "00008000-000000000000000A"


def sim_runner() -> RecordedRunner:
    return RecordedRunner(
        {("xcrun", "simctl", "list", "devices", "--json"): fixture("simctl", "list_devices.json")}
    )


async def test_simulator_only_sees_its_own_devices() -> None:
    provider = SimulatorProvider(sim_runner())
    devices = await provider.discover()
    assert {d.name for d in devices} == {"DeviceLab iPhone 17 Pro", "DeviceLab iPad"}
    phone = next(d for d in devices if d.udid == SIM)
    assert (phone.ios_version, phone.model_family, phone.health.connectivity) == (
        "26.5",
        "iPhone",
        "connected",
    )
    assert phone.model == "iPhone 17 Pro"
    pad = next(d for d in devices if d.model_family == "iPad")
    assert pad.ios_version == "18.6" and pad.health.connectivity == "shutdown"
    assert (await provider.health(SIM)).connectivity == "connected"
    with pytest.raises(StepFailure):
        await provider.health("66666666-2222-3333-4444-555555555555")  # not ours: invisible


async def test_simulator_commands() -> None:
    runner = sim_runner()
    provider = SimulatorProvider(runner)
    await provider.install_app(SIM, Path("/apps/Demo.app"))
    await provider.launch_app(SIM, "com.apple.Preferences", locale="fr_FR")
    await provider.terminate_app(SIM, "com.apple.Preferences")
    await provider.set_setting(SIM, "appearance", "dark")
    await provider.set_setting(SIM, "locale", "de_DE")
    await provider.reboot(SIM)
    assert ("xcrun", "simctl", "install", SIM, "/apps/Demo.app") in runner.calls
    assert (
        "xcrun",
        "simctl",
        "launch",
        "--terminate-running-process",
        SIM,
        "com.apple.Preferences",
        "-AppleLanguages",
        "(fr)",
        "-AppleLocale",
        "fr_FR",
    ) in runner.calls
    assert ("xcrun", "simctl", "ui", SIM, "appearance", "dark") in runner.calls
    assert (
        "xcrun",
        "simctl",
        "spawn",
        SIM,
        "defaults",
        "write",
        "-g",
        "AppleLanguages",
        "-array",
        "de",
    ) in runner.calls
    assert ("xcrun", "simctl", "shutdown", SIM) in runner.calls
    assert ("xcrun", "simctl", "bootstatus", SIM, "-b") in runner.calls
    with pytest.raises(SettingNotSupportedError):
        await provider.set_setting(SIM, "timezone", "Europe/Berlin")
    logs = await provider.collect_logs(SIM, 5)
    assert logs == b"" and any(c[4:6] == ("log", "show") for c in runner.calls)


async def test_simulator_screenshot_reads_the_file_simctl_wrote() -> None:
    class Writer(RecordedRunner):
        async def run(self, argv, **kw):  # type: ignore[no-untyped-def]
            if "screenshot" in argv:
                Path(argv[-1]).write_bytes(b"\x89PNGdata")  # noqa: ASYNC240
            return await super().run(argv, **kw)

    assert await SimulatorProvider(Writer()).screenshot(SIM) == b"\x89PNGdata"


def usb_runner() -> RecordedRunner:
    return RecordedRunner(
        {
            ("idevice_id", "-l"): f"{USB}\n",
            ("ideviceinfo", "-u", USB, "-x"): fixture("usb", "ideviceinfo_full.xml"),
            ("ideviceinfo", "-u", USB, "-q", "com.apple.mobile.battery", "-x"): fixture(
                "usb", "ideviceinfo_battery.xml"
            ),
            ("ideviceinfo", "-u", USB, "-q", "com.apple.disk_usage", "-x"): fixture(
                "usb", "ideviceinfo_disk.xml"
            ),
        }
    )


async def test_usb_inventory_from_recorded_output() -> None:
    provider = UsbDeviceProvider(usb_runner())
    (device,) = await provider.discover()
    assert (device.model, device.ios_version, device.model_family) == (
        "iPhone 15",
        "18.2",
        "iPhone",
    )
    assert device.health.battery_pct == 73 and device.health.storage_free_gb == 90.7
    assert device.health.thermal == "nominal" and device.name == "Fixture Phone"
    assert "usb" in device.labels

    redacted = UsbDeviceProvider(usb_runner(), redact_names=True)
    assert (await redacted.discover())[0].name == "USB iPhone 1"


async def test_usb_allow_list_and_unplugged_device() -> None:
    other = UsbDeviceProvider(usb_runner(), allow=["00008000-FFFFFFFFFFFFFFFF"])
    assert await other.discover() == []
    assert (await other.health(USB)).connectivity == "unreachable"
    gone = UsbDeviceProvider(RecordedRunner({("idevice_id", "-l"): ""}))
    assert (await gone.health(USB)).connectivity == "unreachable"


async def test_usb_rejects_garbage_from_the_tool() -> None:
    runner = RecordedRunner(
        {("idevice_id", "-l"): f"{USB}\n", ("ideviceinfo", "-u", USB, "-x"): "not xml"}
    )
    with pytest.raises(StepFailure) as exc:
        await UsbDeviceProvider(runner).discover()
    assert exc.value.code == "device_unreachable"
    arr = RecordedRunner(
        {
            ("idevice_id", "-l"): f"{USB}\n",
            (
                "ideviceinfo",
                "-u",
                USB,
                "-x",
            ): "<?xml version='1.0'?><plist version='1.0'><array/></plist>",
        }
    )
    with pytest.raises(StepFailure):
        await UsbDeviceProvider(arr).discover()


async def test_usb_device_commands() -> None:
    runner = usb_runner()
    runner.responses[("idevicesyslog", "-u", USB)] = "log line\n"
    provider = UsbDeviceProvider(runner)
    await provider.install_app(USB, Path("/apps/Demo.app"))
    await provider.launch_app(USB, "com.apple.calculator", locale="en_US")
    await provider.reboot(USB)
    assert (
        "xcrun",
        "devicectl",
        "device",
        "install",
        "app",
        "--device",
        USB,
        "/apps/Demo.app",
    ) in runner.calls
    assert (
        "xcrun",
        "devicectl",
        "device",
        "process",
        "launch",
        "--device",
        USB,
        "--terminate-existing",
        "com.apple.calculator",
        "-AppleLanguages",
        "(en)",
        "-AppleLocale",
        "en_US",
    ) in runner.calls
    assert ("idevicediagnostics", "-u", USB, "restart") in runner.calls
    assert await provider.collect_logs(USB, 1) == b"log line\n"
    for call in (
        provider.terminate_app(USB, "x"),
        provider.screenshot(USB),
        provider.set_setting(USB, "appearance", "dark"),
    ):
        with pytest.raises(SettingNotSupportedError):
            await call


async def test_command_runner_is_an_allow_list_without_a_shell() -> None:
    runner = SubprocessRunner()
    with pytest.raises(StepFailure) as exc:
        await runner.run(["rm", "-rf", "/"])
    assert exc.value.code == "not_allowed"
    with pytest.raises(StepFailure):
        await runner.run([])
    custom = SubprocessRunner(allowed={"echo", "false", "sleep", "sh"})
    assert (await custom.run(["echo", "$HOME; ls"])).stdout.strip() == "$HOME; ls"  # no expansion
    with pytest.raises(StepFailure):
        await custom.run(["false"])
    assert (await custom.run(["false"], check=False)).returncode == 1
    with pytest.raises(StepFailure) as timeout:
        await custom.run(["sleep", "5"], timeout_s=0.2)
    assert timeout.value.code == "step_timeout"
    partial = await custom.run(["sleep", "5"], timeout_s=0.2, allow_timeout=True)
    assert partial.returncode == 0
    missing = SubprocessRunner(allowed={"definitely-not-installed"})
    with pytest.raises(StepFailure) as absent:
        await missing.run(["definitely-not-installed"])
    assert absent.value.code == "tool_missing"
    unreachable = await SubprocessRunner(allowed={"sh"}).run(
        ["sh", "-c", "echo 'Unable to connect to device' >&2; exit 2"], check=False
    )
    assert unreachable.returncode == 2
    with pytest.raises(StepFailure) as conn:
        await SubprocessRunner(allowed={"sh"}).run(
            ["sh", "-c", "echo 'Unable to connect to device' >&2; exit 2"]
        )
    assert conn.value.code == "device_unreachable"


def test_locale_launch_args() -> None:
    assert locale_launch_args("pt-BR") == ["-AppleLanguages", "(pt)", "-AppleLocale", "pt_BR"]


def roster(p: FakeFleetProvider) -> list[tuple[str, str, str]]:
    return [(d.udid, d.model, d.ios) for d in p.devices.values()]


async def test_fake_fleet_is_deterministic() -> None:
    a = FakeFleetProvider("berlin", 6, seed=3)
    b = FakeFleetProvider("berlin", 6, seed=3)
    c = FakeFleetProvider("berlin", 6, seed=4)
    assert roster(a) == roster(b) != roster(c)
    assert [d.health.battery_pct for d in await a.discover()] == [
        d.health.battery_pct for d in await b.discover()
    ]


async def test_fake_fleet_failures_and_disconnects() -> None:
    clock = [0.0]
    provider = FakeFleetProvider(
        "tokyo", 2, seed=1, clock=lambda: clock[0], behaviour=FakeBehaviour(step_failure_rate=1.0)
    )
    udid = next(iter(provider.devices))
    with pytest.raises(StepFailure) as exc:
        await provider.launch_app(udid, "com.example")
    assert exc.value.code in ("wda_crashed", "device_unreachable", "step_timeout")

    calm = FakeFleetProvider("tokyo", 1, seed=1, clock=lambda: clock[0])
    udid = next(iter(calm.devices))
    calm.disconnect(udid, seconds=30)
    with pytest.raises(StepFailure) as down:
        await calm.screenshot(udid)
    assert down.value.code == "device_unreachable"
    assert (await calm.health(udid)).connectivity == "unreachable"
    clock[0] = 31.0
    assert (await calm.health(udid)).connectivity == "connected"

    await calm.set_setting(udid, "appearance", "dark")
    await calm.set_setting(udid, "locale", "ja_JP")
    await calm.launch_app(udid, "com.example", locale="fr_FR")
    await calm.install_app(udid, Path("x.app"))
    png = await calm.screenshot(udid)
    assert png.startswith(b"\x89PNG")
    assert b"backboardd" in await calm.collect_logs(udid, 1)
    await calm.terminate_app(udid, "com.example")
    await calm.reboot(udid)


def test_fake_battery_and_thermal_evolve() -> None:
    clock = [0.0]
    provider = FakeFleetProvider(
        "x", 3, seed=2, clock=lambda: clock[0], behaviour=FakeBehaviour(disconnect_rate_per_min=5.0)
    )
    before = {u: d.battery for u, d in provider.devices.items()}
    for d in provider.devices.values():
        d.busy = True
    clock[0] = 600.0
    provider.tick()
    assert any(provider.devices[u].battery < b for u, b in before.items())
    for d in provider.devices.values():
        d.busy = False
        d.battery = 5.0
    clock[0] = 1200.0
    provider.tick()
    assert all(d.battery > 5.0 for d in provider.devices.values())
    assert CommandResult(("a",), 0, "", "").argv == ("a",)
