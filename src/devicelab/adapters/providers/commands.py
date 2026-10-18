"""Running host tools. argv only, never a shell, and only executables on an allow-list."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from devicelab.domain.errors import StepFailure


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str


class CommandRunner:
    """Port. `run` raises StepFailure on non-zero exit unless `check=False`."""

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float = 30.0,
        check: bool = True,
        allow_timeout: bool = False,
    ) -> CommandResult:
        raise NotImplementedError


DEFAULT_ALLOWED = frozenset(
    {
        "xcrun",
        "idevice_id",
        "ideviceinfo",
        "idevicesyslog",
        "idevicediagnostics",
    }
)


class SubprocessRunner(CommandRunner):
    def __init__(
        self, allowed: Collection[str] = DEFAULT_ALLOWED, env: Mapping[str, str] | None = None
    ) -> None:
        self._allowed = frozenset(allowed)
        self._env = {**os.environ, **(env or {})}

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float = 30.0,
        check: bool = True,
        allow_timeout: bool = False,
    ) -> CommandResult:
        if not argv or argv[0] not in self._allowed:
            raise StepFailure(f"executable not allowed: {argv[:1]}", code="not_allowed")
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._env,
            )
        except FileNotFoundError:
            raise StepFailure(f"{argv[0]} is not installed", code="tool_missing") from None
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            out, err = await proc.communicate()
            if not allow_timeout:
                raise StepFailure(
                    f"{argv[0]} timed out after {timeout_s}s", code="step_timeout"
                ) from None
            return CommandResult(
                tuple(argv), 0, out.decode(errors="replace"), err.decode(errors="replace")
            )
        result = CommandResult(
            tuple(argv),
            proc.returncode or 0,
            out.decode(errors="replace"),
            err.decode(errors="replace"),
        )
        if check and result.returncode != 0:
            raise StepFailure(
                f"{' '.join(argv[:3])} failed ({result.returncode}): {result.stderr.strip()[:300]}",
                code=_failure_code(result.stderr),
            )
        return result


def _failure_code(stderr: str) -> str:
    lowered = stderr.lower()
    if any(
        s in lowered for s in ("no device", "not connected", "unable to connect", "invalid device")
    ):
        return "device_unreachable"
    return "step_failed"
