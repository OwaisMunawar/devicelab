"""`devicelab-agent`: run a host agent for simulators, USB devices, or a simulated fleet."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
import structlog

from devicelab.adapters.appium.driver import AppiumConfig, AppiumUiDriver
from devicelab.adapters.providers.base import DeviceProvider, UiDriver
from devicelab.adapters.providers.commands import SubprocessRunner
from devicelab.adapters.providers.fake import FakeBehaviour, FakeFleetProvider
from devicelab.adapters.providers.simulator import SimulatorProvider
from devicelab.adapters.providers.usb import UsbDeviceProvider
from devicelab.agent.config import AgentConfig
from devicelab.agent.connection import AgentFatalError, AgentRuntime
from devicelab.api.observability import configure_logging

log = structlog.get_logger("agent")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devicelab-agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run an agent for this machine's devices")
    run.add_argument("--server", required=True, help="ws(s)://host:port/v1/agent/ws")
    run.add_argument("--host-name", required=True)
    run.add_argument("--enrol-token", default=os.environ.get("DEVICELAB_ENROL_TOKEN"))
    run.add_argument("--provider", choices=["simulator", "usb", "fake"], default="simulator")
    run.add_argument("--state-dir", type=Path, default=Path.home() / ".devicelab")
    run.add_argument(
        "--app-root", type=Path, default=None, help="directory install_app may read from"
    )
    run.add_argument(
        "--sim-prefix", default="DeviceLab", help="only manage simulators with this name prefix"
    )
    run.add_argument("--usb-allow", action="append", default=None, help="restrict to these UDIDs")
    run.add_argument(
        "--redact-names", action="store_true", help="report generic names for USB devices"
    )
    run.add_argument("--no-appium", action="store_true")
    run.add_argument("--appium-home", type=Path, default=Path(".cache/appium"))
    run.add_argument("--appium-bin", type=Path, default=None)
    run.add_argument("--derived-data", type=Path, default=Path(".cache/wda"))
    run.add_argument("--wda-bundle-id", default="com.devicelab.WebDriverAgentRunner")
    run.add_argument("--fake-devices", type=int, default=6)
    run.add_argument("--fake-site", default="demo")
    run.add_argument("--fake-seed", type=int, default=7)

    demo = sub.add_parser("demo", help="run several simulated sites against one control plane")
    demo.add_argument("--api", required=True, help="http(s)://host:port of the control plane")
    demo.add_argument("--api-key", default=os.environ.get("DEVICELAB_API_KEY"))
    demo.add_argument("--sites", default="berlin,austin,tokyo,lisbon")
    demo.add_argument("--devices", type=int, default=24)
    demo.add_argument("--state-dir", type=Path, default=Path.home() / ".devicelab")
    demo.add_argument("--seed", type=int, default=7)
    return parser


def _build(args: argparse.Namespace) -> tuple[DeviceProvider, UiDriver | None]:
    if args.provider == "fake":
        provider = FakeFleetProvider(args.fake_site, args.fake_devices, seed=args.fake_seed)
        return provider, provider.ui
    runner = SubprocessRunner()
    if args.provider == "simulator":
        provider_: DeviceProvider = SimulatorProvider(runner, name_prefix=args.sim_prefix)
    else:
        provider_ = UsbDeviceProvider(runner, allow=args.usb_allow, redact_names=args.redact_names)
    ui: UiDriver | None = None
    if not args.no_appium:
        ui = AppiumUiDriver(
            AppiumConfig(
                appium_home=args.appium_home.resolve(),
                derived_data_path=args.derived_data.resolve(),
                appium_binary=args.appium_bin,
                # Signing identity details come from the environment so they never land in a repo.
                team_id=os.environ.get("DEVICELAB_TEAM_ID"),
                wda_bundle_id=args.wda_bundle_id,
            )
        )
    return provider_, ui


async def _run(args: argparse.Namespace) -> int:
    provider, ui = _build(args)
    config = AgentConfig(
        server_url=args.server,
        host_name=args.host_name,
        state_dir=args.state_dir,
        enrol_token=args.enrol_token,
        app_root=args.app_root,
    )
    agent = AgentRuntime(config, provider, ui)
    try:
        await agent.run()
    except AgentFatalError as exc:
        log.error("refused_by_control_plane", reason=str(exc))
        return 2
    except asyncio.CancelledError:
        await agent.stop()
    return 0


async def _wait_for_api(base: str) -> None:
    async with httpx.AsyncClient(timeout=3.0) as client:
        for _ in range(120):
            try:
                if (await client.get(f"{base}/healthz")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1.0)
    raise RuntimeError(f"control plane at {base} never became healthy")


async def _enrol_token(base: str, key: str, host: str, site: str) -> str | None:
    async with httpx.AsyncClient(
        base_url=base, headers={"Authorization": f"Bearer {key}"}
    ) as client:
        response = await client.post(
            "/v1/enrolment-tokens",
            json={"host_name": host, "site": site, "region": "demo", "max_concurrency": 6},
        )
        response.raise_for_status()
        token: str = response.json()["token"]
        return token


async def _demo(args: argparse.Namespace) -> int:
    if not args.api_key:
        log.error("demo needs --api-key or DEVICELAB_API_KEY")
        return 2
    base = args.api.rstrip("/")
    await _wait_for_api(base)
    ws_url = base.replace("http", "ws", 1) + "/v1/agent/ws"
    sites = [s.strip() for s in args.sites.split(",") if s.strip()]
    per_site = [
        args.devices // len(sites) + (1 if i < args.devices % len(sites) else 0)
        for i in range(len(sites))
    ]
    agents: list[AgentRuntime] = []
    for site, count in zip(sites, per_site, strict=True):
        host = f"demo-{site}"
        provider = FakeFleetProvider(
            site,
            count,
            seed=args.seed,
            behaviour=FakeBehaviour(
                step_failure_rate=0.06,
                assertion_failure_rate=0.03,
                disconnect_rate_per_min=0.08,
                disconnect_duration_s=25.0,
                latency_s=(0.4, 1.4),
            ),
        )
        from devicelab.agent.state import CredentialStore  # noqa: PLC0415

        token = None
        if CredentialStore(args.state_dir, host).load() is None:
            token = await _enrol_token(base, args.api_key, host, site)
        config = AgentConfig(
            server_url=ws_url, host_name=host, state_dir=args.state_dir, enrol_token=token
        )
        agents.append(AgentRuntime(config, provider, provider.ui))
    results = await asyncio.gather(*(a.run() for a in agents), return_exceptions=True)
    return 2 if any(isinstance(r, AgentFatalError) for r in results) else 0


def main(argv: list[str] | None = None) -> None:
    configure_logging(json_output=bool(os.environ.get("DEVICELAB_LOG_JSON")), level="INFO")
    args = _parser().parse_args(argv)
    runner = _run if args.command == "run" else _demo
    try:
        sys.exit(asyncio.run(runner(args)))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
