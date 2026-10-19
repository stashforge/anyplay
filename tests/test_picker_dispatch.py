"""Legacy entry routing is outside either rendering implementation."""

import importlib
import inspect
from types import SimpleNamespace

import pytest

from media_cli.ui.picker_state import PickerState


def setup_dispatch(monkeypatch, *, plain=False, failure=None):
    module = importlib.import_module("media_cli.ui.picker_dispatch")
    plain_module = importlib.import_module("media_cli.ui.plain_picker")
    calls = []
    monkeypatch.setattr(
        module, "renderer_for", lambda flags: module.RendererKind.PLAIN if plain else module.RendererKind.TUI
    )

    def plain_pick(**request):
        calls.append(("plain", request))
        return 7

    monkeypatch.setattr(plain_module, "plain_pick", plain_pick)

    def terminal_pick(flags, ui, prompt, options, *, state=None, select_fn=None, **kwargs):
        calls.append(
            ("tui", dict(flags=flags, ui=ui, prompt=prompt, options=options, state=state, select_fn=select_fn))
        )
        if failure is not None:
            raise failure(module)
        return 3

    return module, module.dispatch_legacy_picker(terminal_pick), calls, terminal_pick


@pytest.mark.parametrize("plain, expected", [(False, "tui"), (True, "plain")])
def test_only_selected_renderer_executes(monkeypatch, plain, expected):
    _, pick, calls, _ = setup_dispatch(monkeypatch, plain=plain)
    flags, ui = SimpleNamespace(), SimpleNamespace()
    options = ["A", "B"]
    result = pick(flags, ui, "Pick", options)
    assert result == (7 if plain else 3)
    assert [kind for kind, _ in calls] == [expected]
    assert calls[0][1]["options"] is options


def test_only_terminal_unavailability_activates_fallback(monkeypatch):
    _, pick, calls, _ = setup_dispatch(monkeypatch, failure=lambda module: module.RendererUnavailable("No terminal"))
    state = PickerState(query="a", selected_index=1)
    assert pick(None, SimpleNamespace(), "Pick", ["A", "B"], state=state) == 7
    assert [kind for kind, _ in calls] == ["tui", "plain"]
    assert all(request["state"] is state for _, request in calls)


@pytest.mark.parametrize("error", [RuntimeError, OSError, ValueError])
def test_renderer_errors_are_not_hidden_as_plain_fallback(monkeypatch, error):
    _, pick, calls, _ = setup_dispatch(monkeypatch, failure=lambda module: error("Broken renderer"))
    with pytest.raises(error, match="Broken renderer"):
        pick(None, SimpleNamespace(), "Pick", ["A"])
    assert [kind for kind, _ in calls] == ["tui"]


def test_plain_renderer_errors_are_not_retried(monkeypatch):
    module, pick, calls, _ = setup_dispatch(monkeypatch, plain=True)
    plain_module = importlib.import_module("media_cli.ui.plain_picker")

    def broken(**request):
        raise module.RendererUnavailable("Plain failure")

    monkeypatch.setattr(plain_module, "plain_pick", broken)
    with pytest.raises(module.RendererUnavailable, match="Plain failure"):
        pick(None, SimpleNamespace(), "Pick", ["A"])
    assert calls == []


def test_compatibility_signature_is_preserved(monkeypatch):
    _, pick, _, original = setup_dispatch(monkeypatch)
    assert inspect.signature(pick) == inspect.signature(original)


@pytest.mark.parametrize("plain", [False, True])
def test_resume_state_is_resolved_before_either_renderer(monkeypatch, plain):
    _, pick, calls, _ = setup_dispatch(monkeypatch, plain=plain)
    state = PickerState(query="saved", selected_index=1)
    ui = SimpleNamespace(_resume_picker_position=("Pick", state))
    pick(None, ui, lambda: "Pick", ["A", "B"])
    assert ui._resume_picker_position is None
    assert calls[0][1]["state"] is state


@pytest.mark.parametrize("plain", [False, True])
def test_invalid_state_fails_before_either_renderer(monkeypatch, plain):
    _, pick, calls, _ = setup_dispatch(monkeypatch, plain=plain)
    with pytest.raises(TypeError, match="state"):
        pick(None, SimpleNamespace(), "Pick", ["A"], state="wrong")
    assert calls == []


def test_preparation_cancellation_remains_shared(monkeypatch):
    _, pick, calls, _ = setup_dispatch(monkeypatch, plain=True)
    from media_cli.ui import picker as picker_module
    from media_cli.ui.transition import PreparationCancelled

    invalidations = []
    monkeypatch.setattr(picker_module, "invalidate_screen_buffer", lambda: invalidations.append(True))

    def cancelled(index):
        raise PreparationCancelled()

    pick(None, SimpleNamespace(), "Pick", ["A"], select_fn=cancelled)
    assert calls[0][1]["select_fn"](0) is None
    assert invalidations == [True]
