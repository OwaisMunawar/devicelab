"""Maps allow-listed workflow steps onto provider and UI-driver calls.

This is the only place steps become actions, and it is a closed `match`: a step type that
is not handled here cannot run, no matter what the control plane sends.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from devicelab.adapters.providers.base import DeviceProvider, UiDriver, UiSession
from devicelab.domain.errors import SettingNotSupportedError, StepFailure
from devicelab.domain.workflow import (
    AppiumActions,
    CaptureScreenshots,
    CollectLogs,
    InstallApp,
    LaunchApp,
    RebootDevice,
    Screenshot,
    SetDeviceSetting,
    Step,
    TerminateApp,
)

LogFn = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class Produced:
    name: str
    kind: Literal["screenshot", "log", "result"]
    content_type: str
    data: bytes


@dataclass
class StepContext:
    udid: str
    log: LogFn
    session: UiSession | None = None
    produced: list[Produced] = field(default_factory=list)


class StepExecutor:
    def __init__(
        self, provider: DeviceProvider, ui: UiDriver | None = None, app_root: Path | None = None
    ) -> None:
        self._provider = provider
        self._ui = ui
        self._app_root = app_root.resolve() if app_root else None

    async def run(self, ctx: StepContext, step: Step) -> None:
        p, udid = self._provider, ctx.udid
        match step:
            case InstallApp():
                await p.install_app(udid, self._resolve_app(step.app))
            case LaunchApp():
                await p.launch_app(udid, step.bundle_id, locale=step.locale)
            case TerminateApp():
                try:
                    await p.terminate_app(udid, step.bundle_id)
                except SettingNotSupportedError:
                    session = await self._session(ctx, None)
                    await session.terminate_app(step.bundle_id)
            case AppiumActions():
                session = await self._session(ctx, step.bundle_id)
                for action in step.actions:
                    await session.perform(action)
            case Screenshot():
                data = await self._capture(ctx)
                ctx.produced.append(
                    Produced(f"{step.artifact_name}.png", "screenshot", "image/png", data)
                )
            case CaptureScreenshots():
                await self._capture_matrix(ctx, step)
            case CollectLogs():
                data = await p.collect_logs(udid, step.duration_s)
                ctx.produced.append(
                    Produced(f"{step.artifact_name}.log", "log", "text/plain", data)
                )
            case SetDeviceSetting():
                await p.set_setting(udid, step.setting, step.value)
            case RebootDevice():
                await self.reboot(ctx)

    async def _capture(self, ctx: StepContext) -> bytes:
        """Prefer the automation session when there is one, so what is captured is what the
        driver sees; fall back to the provider for agents running without Appium."""
        if self._ui is not None:
            return await (await self._session(ctx, None)).screenshot()
        return await self._provider.screenshot(ctx.udid)

    async def _capture_matrix(self, ctx: StepContext, step: CaptureScreenshots) -> None:
        for locale in step.locales:
            for appearance in step.appearances:
                ctx.log("info", f"capturing {locale} / {appearance}")
                await self._provider.set_setting(ctx.udid, "appearance", appearance)
                await self._provider.launch_app(ctx.udid, step.bundle_id, locale=locale)
                if step.before_capture:
                    session = await self._session(ctx, step.bundle_id)
                    for action in step.before_capture:
                        await session.perform(action)
                data = await self._capture(ctx)
                name = f"{step.prefix}-{locale}-{appearance}.png"
                ctx.produced.append(Produced(name, "screenshot", "image/png", data))

    async def _session(self, ctx: StepContext, bundle_id: str | None) -> UiSession:
        if self._ui is None:
            raise StepFailure("this agent has no UI driver configured", code="not_supported")
        if ctx.session is None:
            ctx.session = await self._ui.open(ctx.udid, bundle_id)
        return ctx.session

    def _resolve_app(self, relative: str) -> Path:
        if self._app_root is None:
            raise StepFailure(
                "install_app needs the agent to be started with --app-root", code="forbidden_path"
            )
        path = (self._app_root / relative).resolve()
        if self._app_root not in path.parents or not path.exists():
            raise StepFailure(
                f"app not found under the app root: {relative}", code="forbidden_path"
            )
        return path

    async def restart_session(self, ctx: StepContext) -> None:
        await self.close(ctx)

    async def reboot(self, ctx: StepContext) -> None:
        await self.close(ctx)
        await self._provider.reboot(ctx.udid)

    async def close(self, ctx: StepContext) -> None:
        if ctx.session is not None:
            await ctx.session.close()
            ctx.session = None
