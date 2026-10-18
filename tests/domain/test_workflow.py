from __future__ import annotations

import json
from pathlib import Path

import pytest

from devicelab.domain.errors import ValidationFailedError
from devicelab.domain.templating import render_steps, resolve_params
from devicelab.domain.workflow import LaunchApp, Screenshot, parse_template

MINIMAL = """
name: demo-flow
params:
  bundle: {type: string, default: com.apple.Preferences}
  wait: {type: integer, required: true}
steps:
  - {id: open, type: launch_app, bundle_id: "{{ params.bundle }}"}
  - {id: pause, type: appium_actions, actions: [{action: wait, seconds: 1}]}
  - {id: shot, type: screenshot, artifact_name: "{{ device.udid }}"}
"""


def test_parse_and_render() -> None:
    tpl = parse_template(MINIMAL)
    steps = render_steps(tpl, {"wait": 3}, {"udid": "abc123"})
    first, last = steps[0], steps[2]
    assert isinstance(first, LaunchApp) and first.bundle_id == "com.apple.Preferences"
    assert isinstance(last, Screenshot) and last.artifact_name == "abc123"


def test_json_is_accepted() -> None:
    doc = {"name": "json-flow", "steps": [{"id": "r", "type": "reboot_device"}]}
    assert parse_template(json.dumps(doc)).steps[0].type == "reboot_device"


@pytest.mark.parametrize(
    "text",
    [
        "name: x",
        "- a list",
        "name: ok-name\nsteps: [{id: a, type: run_shell, cmd: ls}]",
        "name: ok-name\nsteps: [{id: Bad-ID, type: reboot_device}]",
        "name: ok-name\nsteps: [{id: a, type: reboot_device}, {id: a, type: reboot_device}]",
        "name: ok-name\nsteps: [{id: a, type: launch_app, bundle_id: 'x; rm -rf /'}]",
        "name: ok-name\nsteps: [{id: a, type: reboot_device, surprise: 1}]",
        "name: ok-name\nsteps: [{id: a, type: set_device_setting, setting: appearance, value: neon}]",
        "name: ok-name\nsteps: [{id: a, type: set_device_setting, setting: locale, value: '!!'}]",
        "name: ok-name\nsteps: [{id: a, type: set_device_setting, setting: timezone, value: '../x'}]",
        "name: ok-name\nsteps: [{id: a, type: appium_actions,"
        " actions: [{action: assert_text, value: x}]}]",
        "name: ok-name\nsteps: [{id: a, type: appium_actions, actions: [{action: wait}]}]",
        "name: [unterminated",
    ],
)
def test_invalid_templates_are_rejected(text: str) -> None:
    with pytest.raises(ValidationFailedError):
        parse_template(text)


def test_problems_are_reported_with_paths() -> None:
    with pytest.raises(ValidationFailedError) as exc:
        parse_template("name: ok-name\nsteps: [{id: a, type: launch_app, bundle_id: ''}]")
    assert exc.value.code == "template_invalid"
    assert exc.value.details["problems"][0]["path"].startswith("steps")


def test_param_resolution_errors() -> None:
    tpl = parse_template(MINIMAL)
    with pytest.raises(ValidationFailedError, match="missing"):
        resolve_params(tpl, {})
    with pytest.raises(ValidationFailedError, match="unknown"):
        resolve_params(tpl, {"wait": 1, "nope": 1})
    with pytest.raises(ValidationFailedError, match="integer"):
        resolve_params(tpl, {"wait": "x"})


def test_unresolved_placeholder_and_post_render_validation() -> None:
    tpl = parse_template(
        "name: ok-name\nparams: {b: {type: string, required: true}}\n"
        "steps: [{id: a, type: launch_app, bundle_id: '{{ params.b }}'},"
        "{id: c, type: screenshot, artifact_name: '{{ device.nope }}'}]"
    )
    with pytest.raises(ValidationFailedError):
        render_steps(tpl, {"b": "bad bundle!"}, {})
    with pytest.raises(ValidationFailedError, match="unresolved"):
        render_steps(tpl, {"b": "com.ok.app"}, {})


def test_shipped_examples_are_valid() -> None:
    root = Path(__file__).resolve().parents[2] / "examples" / "templates"
    files = sorted(root.glob("*.yaml"))
    assert len(files) >= 4
    for path in files:
        parse_template(path.read_text())
