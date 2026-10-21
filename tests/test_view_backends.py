"""Each view backend owns its command vocabulary and presentation."""

import importlib
from types import SimpleNamespace

import pytest

from media_cli.ui.view_model import Blank, DetailLines, ViewAction, ViewSpec, ViewTab


def backend(name):
    module = importlib.import_module(f"media_cli.ui.view_{name}")
    return getattr(module, f"{name.title()}ViewBackend")()


def context(view):
    events, tabs, focuses = [], [], []
    return (
        SimpleNamespace(
            view=view,
            session=SimpleNamespace(navigation=None, input_blocked=False, cancel_navigation=lambda: None),
            emit=lambda *args: events.append(args),
            tab=lambda *args: tabs.append(args),
            focus=focuses.append,
        ),
        events,
        tabs,
        focuses,
    )


def view(**kwargs):
    return ViewSpec(id="test", prompt="Pick", items=(), details=DetailLines(Blank, Blank, Blank), **kwargs)


@pytest.mark.parametrize("delay,error", [(True, TypeError), ("1", TypeError), (None, TypeError),
                                         (-1, ValueError), (float("nan"), ValueError), (float("inf"), ValueError)])
def test_invalid_focus_delay_fails_before_rendering(delay, error):
    from media_cli.ui.view_picker import run_view_session
    from media_cli.ui.view_session import ViewSession

    with pytest.raises(error, match="focus_delay"):
        run_view_session(SimpleNamespace(plain_mode=True), SimpleNamespace(), ViewSession(view()), focus_delay=delay)


@pytest.mark.parametrize("command, kind", [(" back ", "back"), ("ESC", "back"), ("quit", "quit")])
def test_plain_commands_do_not_focus_or_use_tui_bindings(command, kind):
    ctx, events, tabs, focuses = context(view())
    assert backend("plain").handle_key(command, 2, ctx)
    assert events == [(kind, 2, None)]
    assert tabs == focuses == []


def test_plain_tab_and_custom_action_use_typed_commands():
    ctx, events, tabs, _ = context(
        view(tabs=(ViewTab("a", "A"),), active_tab_id="a", actions=(ViewAction("refresh", "Refresh", ("CTRL_R",)),))
    )
    renderer = backend("plain")
    assert renderer.handle_key("t", 0, ctx)
    assert tabs == [(0, 1)]
    assert renderer.handle_key("REFRESH", 0, ctx)
    assert events == [("action", 0, "refresh")]
    assert not renderer.handle_key("CTRL_R", 0, ctx)
    assert not renderer.handle_key("1", 0, ctx)


def test_plain_commands_are_listed_once_for_multiple_keyboard_binding_groups():
    content = view(actions=(
        ViewAction("translation", "Sub/Dub", ("TAB",)),
        ViewAction("translation", "Sub/Dub", ("BTAB", "CTRL_N"), footer=False),
    ))
    assert backend("plain").action_labels(content).count("[translation] Sub/Dub") == 1


def test_tui_printable_keys_remain_filter_input():
    ctx, events, tabs, focuses = context(view())
    assert not backend("tui").handle_key("q", 0, ctx)
    assert events == tabs == focuses == []


def test_tui_key_bindings_keep_custom_reserved_actions():
    ctx, events, _, _ = context(view(actions=(ViewAction("refresh", "Refresh", ("ESC",)),)))
    assert backend("tui").handle_key("ESC", 0, ctx)
    assert events == [("action", 0, "refresh")]


def test_headers_and_capabilities_belong_to_backend():
    calls = []
    presentation = SimpleNamespace(
        plain_details=lambda item: ["Primary", "Details"],
        details=lambda item, width: calls.append((item, width)) or "Styled details",
    )
    plain, tui = backend("plain"), backend("tui")
    assert plain.header(presentation, "a") == "Primary\nDetails"
    assert calls == []
    assert tui.header(presentation, "a") == "Styled details"
    assert calls[0][0] == "a" and calls[0][1] > 0
    assert not plain.supports_images and not plain.supports_hover
    assert tui.supports_images and tui.supports_hover


def test_action_hints_use_each_backends_own_input_vocabulary():
    spec = view(actions=(ViewAction("refresh", "Reload", ("CTRL_R",)),))
    assert "[refresh] Reload" in backend("plain").action_labels(spec)
    assert "[Ctrl+R] Reload" in backend("tui").action_labels(spec)
    assert "[b] Back" in backend("plain").action_labels(spec)


@pytest.mark.parametrize("kind, class_name", [("PLAIN", "PlainViewBackend"), ("TUI", "TuiViewBackend")])
def test_dispatcher_selects_adapter_without_mode_checks_in_session(monkeypatch, kind, class_name):
    module = importlib.import_module("media_cli.ui.renderer_selection")
    monkeypatch.setattr(module, "renderer_for", lambda flags: module.RendererKind[kind])
    assert type(module.view_backend_for(SimpleNamespace())).__name__ == class_name
