"""Workflow template models.

Steps are a closed, typed set. There is deliberately no "run this shell command" step: a
template can only ask a host agent to do things the agent already knows how to do safely.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from devicelab.domain.errors import ValidationFailedError

_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
_BUNDLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.\-]{0,127}$")
_LOCALE = re.compile(r"^[a-z]{2,3}([_-][A-Za-z0-9]{2,4})?$")


def templated(value: str) -> bool:
    """Values that still contain a placeholder are re-validated after rendering."""
    return "{{" in value


_NAME = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _check_name(v: str) -> str:
    if not templated(v) and not _NAME.match(v):
        raise ValueError("use letters, digits, '_' and '-' (max 64)")
    return v


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryPolicy(Strict):
    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_base_s: float = Field(default=1.0, ge=0, le=60)
    backoff_max_s: float = Field(default=30.0, ge=0, le=600)
    jitter: float = Field(default=0.2, ge=0, le=1)


Locator = Literal["accessibility_id", "name", "predicate", "class_chain", "xpath"]


class Tap(Strict):
    action: Literal["tap"]
    by: Locator = "accessibility_id"
    value: str = Field(min_length=1, max_length=256)


class TypeText(Strict):
    action: Literal["type"]
    by: Locator = "accessibility_id"
    value: str = Field(min_length=1, max_length=256)
    text: str = Field(max_length=1024)


class Swipe(Strict):
    action: Literal["swipe"]
    direction: Literal["up", "down", "left", "right"]


class AssertText(Strict):
    action: Literal["assert_text"]
    by: Locator = "accessibility_id"
    value: str = Field(min_length=1, max_length=256)
    equals: str | None = None
    contains: str | None = None

    @model_validator(mode="after")
    def _one_matcher(self) -> AssertText:
        if (self.equals is None) == (self.contains is None):
            raise ValueError("assert_text needs exactly one of equals / contains")
        return self


class Wait(Strict):
    action: Literal["wait"]
    seconds: float | None = Field(default=None, gt=0, le=120)
    by: Locator | None = None
    value: str | None = None

    @model_validator(mode="after")
    def _seconds_or_element(self) -> Wait:
        if (self.seconds is None) == (self.value is None):
            raise ValueError("wait needs exactly one of seconds / value")
        return self


UiAction = Annotated[Tap | TypeText | Swipe | AssertText | Wait, Field(discriminator="action")]


class StepBase(Strict):
    id: str
    name: str | None = None
    timeout_s: int = Field(default=60, ge=1, le=1800)
    retry: RetryPolicy = RetryPolicy()
    continue_on_error: bool = False
    # Cleanup-style steps that must run even after an earlier failure.
    always: bool = False

    @field_validator("id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("step id must be lower_snake_case, max 48 chars")
        return v


class InstallApp(StepBase):
    type: Literal["install_app"]
    # Resolved by the agent under its configured app root; never an arbitrary path.
    app: str = Field(min_length=1, max_length=256)


class LaunchApp(StepBase):
    type: Literal["launch_app"]
    bundle_id: str
    locale: str | None = None

    @field_validator("bundle_id")
    @classmethod
    def _bundle(cls, v: str) -> str:
        if not templated(v) and not _BUNDLE.match(v):
            raise ValueError("invalid bundle id")
        return v

    @field_validator("locale")
    @classmethod
    def _locale(cls, v: str | None) -> str | None:
        if v is not None and not templated(v) and not _LOCALE.match(v):
            raise ValueError("invalid locale")
        return v


class TerminateApp(StepBase):
    type: Literal["terminate_app"]
    bundle_id: str

    @field_validator("bundle_id")
    @classmethod
    def _bundle(cls, v: str) -> str:
        if not templated(v) and not _BUNDLE.match(v):
            raise ValueError("invalid bundle id")
        return v


class AppiumActions(StepBase):
    type: Literal["appium_actions"]
    bundle_id: str | None = None
    actions: list[UiAction] = Field(min_length=1, max_length=50)


class Screenshot(StepBase):
    type: Literal["screenshot"]
    artifact_name: str = "screenshot"

    _name = field_validator("artifact_name")(_check_name)


class CaptureScreenshots(StepBase):
    type: Literal["capture_screenshots"]
    bundle_id: str
    locales: list[str] = Field(min_length=1, max_length=20)
    appearances: list[Literal["light", "dark"]] = Field(default=["light"])
    prefix: str = "shot"

    _prefix = field_validator("prefix")(_check_name)
    before_capture: list[UiAction] = Field(default_factory=list, max_length=20)

    @field_validator("locales")
    @classmethod
    def _locales(cls, v: list[str]) -> list[str]:
        for loc in v:
            if not templated(loc) and not _LOCALE.match(loc):
                raise ValueError(f"invalid locale: {loc}")
        return v


class CollectLogs(StepBase):
    type: Literal["collect_logs"]
    duration_s: int = Field(default=5, ge=1, le=300)
    artifact_name: str = "device-log"

    _name = field_validator("artifact_name")(_check_name)


class SetDeviceSetting(StepBase):
    type: Literal["set_device_setting"]
    setting: Literal["appearance", "locale", "timezone"]
    value: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def _value_matches_setting(self) -> SetDeviceSetting:
        if templated(self.value):
            return self
        if self.setting == "appearance" and self.value not in ("light", "dark"):
            raise ValueError("appearance must be light or dark")
        if self.setting == "locale" and not _LOCALE.match(self.value):
            raise ValueError("invalid locale")
        if self.setting == "timezone" and not re.match(
            r"^[A-Za-z_]+(/[A-Za-z_\-+0-9]+){0,2}$", self.value
        ):
            raise ValueError("invalid time zone identifier")
        return self


class RebootDevice(StepBase):
    type: Literal["reboot_device"]


Step = Annotated[
    InstallApp
    | LaunchApp
    | TerminateApp
    | AppiumActions
    | Screenshot
    | CaptureScreenshots
    | CollectLogs
    | SetDeviceSetting
    | RebootDevice,
    Field(discriminator="type"),
]

STEP_TYPES = (
    "install_app",
    "launch_app",
    "terminate_app",
    "appium_actions",
    "screenshot",
    "capture_screenshots",
    "collect_logs",
    "set_device_setting",
    "reboot_device",
)


class ParamSpec(Strict):
    type: Literal["string", "integer", "boolean"] = "string"
    default: str | int | bool | None = None
    description: str | None = None
    required: bool = False


class RequirementSpec(Strict):
    count: int = Field(default=1, ge=1, le=50)
    min_ios: str | None = None
    max_ios: str | None = None
    model_family: str | None = None
    site: str | None = None
    region: str | None = None
    labels: list[str] = Field(default_factory=list)


class WorkflowTemplate(Strict):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,62}$")
    description: str = ""
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    requirements: RequirementSpec = RequirementSpec()
    # abort: stop at the first failing step (except `always` steps). continue: run everything.
    failure_policy: Literal["abort", "continue"] = "abort"
    steps: list[Step] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _unique_ids(self) -> WorkflowTemplate:
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                raise ValueError(f"duplicate step id: {step.id}")
            seen.add(step.id)
        return self


def parse_template(text: str) -> WorkflowTemplate:
    """Accepts YAML or JSON (JSON is valid YAML) and reports every problem at once."""
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationFailedError(
            f"template is not valid YAML: {exc}", code="template_syntax"
        ) from exc
    return template_from_dict(raw)


def template_from_dict(raw: Any) -> WorkflowTemplate:
    if not isinstance(raw, dict):
        raise ValidationFailedError("template must be a mapping", code="template_syntax")
    try:
        return WorkflowTemplate.model_validate(raw)
    except ValidationError as exc:
        problems = [
            {"path": ".".join(str(p) for p in e["loc"]), "message": e["msg"]}
            for e in json.loads(exc.json())
        ]
        raise ValidationFailedError(
            f"template invalid: {len(problems)} problem(s)",
            code="template_invalid",
            details={"problems": problems},
        ) from exc
