"""`{{ params.x }}` substitution. Deliberately not Jinja: no expressions, no attribute walks,
nothing a template author can use to reach beyond the values we hand in."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from devicelab.domain.errors import ValidationFailedError
from devicelab.domain.workflow import Step, WorkflowTemplate, template_from_dict

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\.([A-Za-z0-9_]+)\s*\}\}")

type Scalar = str | int | bool


def resolve_params(template: WorkflowTemplate, supplied: Mapping[str, Any]) -> dict[str, Scalar]:
    unknown = set(supplied) - set(template.params)
    if unknown:
        raise ValidationFailedError(
            f"unknown parameter(s): {', '.join(sorted(unknown))}", code="unknown_params"
        )
    resolved: dict[str, Scalar] = {}
    for name, spec in template.params.items():
        if name in supplied:
            value = supplied[name]
        elif spec.default is not None:
            value = spec.default
        elif spec.required:
            raise ValidationFailedError(f"missing parameter: {name}", code="missing_param")
        else:
            continue
        resolved[name] = _coerce(name, spec.type, value)
    return resolved


def _coerce(name: str, kind: str, value: Any) -> Scalar:
    ok = (
        (kind == "string" and isinstance(value, str))
        or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (kind == "boolean" and isinstance(value, bool))
    )
    if not ok:
        raise ValidationFailedError(
            f"parameter {name} must be {kind}", code="bad_param_type", details={"param": name}
        )
    return value  # type: ignore[no-any-return]


def _render(value: Any, scopes: Mapping[str, Mapping[str, Scalar]]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda m: str(_lookup(scopes, m.group(1), m.group(2))), value)
    if isinstance(value, list):
        return [_render(v, scopes) for v in value]
    if isinstance(value, dict):
        return {k: _render(v, scopes) for k, v in value.items()}
    return value


def _lookup(scopes: Mapping[str, Mapping[str, Scalar]], scope: str, key: str) -> Scalar:
    try:
        return scopes[scope][key]
    except KeyError:
        raise ValidationFailedError(
            f"unresolved placeholder {scope}.{key}", code="unresolved_placeholder"
        ) from None


def render_steps(
    template: WorkflowTemplate,
    params: Mapping[str, Any],
    device: Mapping[str, Scalar],
) -> list[Step]:
    """Substitute, then validate again: a parameter can turn a valid string into an invalid
    bundle id, and the agent must never see a step that would not pass the schema."""
    scopes = {"params": resolve_params(template, params), "device": device}
    rendered: list[Step] = []
    for step in template.steps:
        raw = _render(step.model_dump(mode="json"), scopes)
        one = template_from_dict({"name": "render-check", "steps": [raw]})
        rendered.append(one.steps[0])
    return rendered
