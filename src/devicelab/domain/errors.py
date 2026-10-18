"""Typed errors. Every error carries a stable machine-readable code that the API maps
onto its error envelope, so clients never have to parse messages."""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    code = "domain_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        self.details = details or {}


class NotFoundError(DomainError):
    code = "not_found"


class ConflictError(DomainError):
    code = "conflict"


class ValidationFailedError(DomainError):
    code = "validation_failed"


class InvalidTransitionError(ConflictError):
    code = "invalid_transition"


class StaleFenceError(ConflictError):
    """A caller presented a fencing token that is no longer the current one."""

    code = "stale_fence"


class UnauthorizedError(DomainError):
    code = "unauthorized"


class ForbiddenError(DomainError):
    code = "forbidden"


class SettingNotSupportedError(DomainError):
    code = "setting_not_supported"


class StepFailure(DomainError):
    """A workflow step failed on a device. `code` feeds failure classification, so use the
    codes in `domain.failures` (device_unreachable, wda_crashed, app_crashed, ...)."""

    code = "step_failed"

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message, code=code)
