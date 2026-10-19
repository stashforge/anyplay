"""Renderer selection is independent of either renderer's implementation."""

import importlib
from types import SimpleNamespace

import pytest


def component():
    return importlib.import_module("media_cli.ui.renderer_selection")


@pytest.mark.parametrize(
    "plain_requested, input_is_terminal, expected",
    [(False, True, "TUI"), (True, True, "PLAIN"), (False, False, "PLAIN"), (True, False, "PLAIN")],
)
def test_explicit_mode_and_terminal_capability_select_backend(plain_requested, input_is_terminal, expected):
    module = component()
    assert module.select_renderer(plain_requested=plain_requested, input_is_terminal=input_is_terminal).name == expected


@pytest.mark.parametrize("field", ["plain_requested", "input_is_terminal"])
@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_ambiguous_selection_inputs_fail(field, value):
    module = component()
    kwargs = dict(plain_requested=False, input_is_terminal=True)
    kwargs[field] = value
    with pytest.raises(TypeError, match=field):
        module.select_renderer(**kwargs)


@pytest.mark.parametrize("terminal", [True, False])
def test_legacy_flags_adapter_uses_same_policy(monkeypatch, terminal):
    module = component()
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: terminal))
    assert module.renderer_for(SimpleNamespace()) == module.select_renderer(
        plain_requested=False, input_is_terminal=terminal
    )
    assert module.renderer_for(SimpleNamespace(plain_mode=True)) is module.RendererKind.PLAIN


def test_selection_does_not_import_renderers():
    # A fresh interpreter catches eager imports hidden by other tests.
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from media_cli.ui.renderer_selection import select_renderer; "
                "select_renderer(plain_requested=False, input_is_terminal=True); "
                "assert 'media_cli.ui.picker' not in sys.modules; "
                "assert 'media_cli.ui.plain_picker' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
