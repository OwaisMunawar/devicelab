"""A CommandRunner that replays recorded tool output and records what was asked of it."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from devicelab.adapters.providers.commands import CommandResult, CommandRunner
from devicelab.domain.errors import StepFailure

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

type Responder = Callable[[Sequence[str]], CommandResult | None]


class RecordedRunner(CommandRunner):
    def __init__(self, responses: dict[tuple[str, ...], str | CommandResult] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, ...]] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout_s: float = 30.0,
        check: bool = True,
        allow_timeout: bool = False,
    ) -> CommandResult:
        key = tuple(argv)
        self.calls.append(key)
        response = self.responses.get(key)
        if response is None:
            # Commands we did not script succeed silently (install, launch, ...).
            return CommandResult(key, 0, "", "")
        if isinstance(response, str):
            return CommandResult(key, 0, response, "")
        if check and response.returncode != 0:
            raise StepFailure(response.stderr, code="device_unreachable")
        return response


def fixture(*parts: str) -> str:
    return FIXTURES.joinpath(*parts).read_text()
