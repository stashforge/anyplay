"""A structured fallback rebuilds presentation and input together."""

from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from media_cli.ui.picker_dispatch import RendererUnavailable
from media_cli.ui.poster import PosterManager
from media_cli.ui.view_model import Blank, Content, DetailLines, Inherit, ViewAction, ViewItem, ViewSpec, ViewTab
from media_cli.ui.view_session import ExitSession, ReplaceView, ViewSession


def environment(monkeypatch, tmp_path):
    import sys

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    flags = SimpleNamespace(show_image=True, plain_mode=False)
    ui = SimpleNamespace(active_picker_query="", hovered_show_id=None, hovered_show_obj=None)
    manager = PosterManager(
        enabled=lambda: False,
        cache_dir=lambda: str(tmp_path),
        hovered_show_id=lambda: ui.hovered_show_id,
        request_redraw=lambda: None,
        loading_frame=lambda: "*",
    )
    return flags, ui, manager


def test_fallback_rebuilds_headers_commands_and_tabs_without_losing_state(monkeypatch, tmp_path):
    from media_cli.ui import plain_picker, view_picker

    flags, ui, manager = environment(monkeypatch, tmp_path)
    spec = ViewSpec(
        "root",
        "Pick",
        (ViewItem("a", "Same", details=Inherit), ViewItem("b", "Same", details=Inherit)),
        details=DetailLines(Content("Primary"), Blank, Blank),
        tabs=(ViewTab("a", "First"), ViewTab("b", "Second")),
        active_tab_id="a",
        actions=(ViewAction("refresh", "Reload", ("CTRL_R",)),),
    )
    events, attempts, plain_calls = [], [], []

    def handle(event):
        events.append(event)
        if event.kind == "tab":
            return ReplaceView(event.view_id, event.generation, replace(spec, active_tab_id=event.value))
        if event.kind == "select":
            return ExitSession(event.item_id)

    session = ViewSession(spec, handle)
    session.position.selected_id = "b"
    session.position.query = "same"
    session.position.marked_ids = {"b"}
    generation = session.generation

    def unavailable(flags, ui, prompt, options, **hooks):
        attempts.append(prompt)
        assert hooks["top_header_fn"] is not None
        raise RendererUnavailable("Forced terminal setup failure")

    def plain(flags, ui, prompt, options, **hooks):
        plain_calls.append(prompt)
        assert "\033" not in hooks["header"]
        assert hooks["top_header_fn"] is None
        assert hooks["tick_fn"]() is False
        assert "[refresh] Reload" in hooks["action_labels"]
        assert hooks["state"].query == "same"
        assert hooks["state"].selected_index == 1
        assert hooks["state"].marked_indices == {1}
        if len(plain_calls) == 1:
            assert session.generation == generation
            assert hooks["key_fn"]("t", 1)
            return -6
        if len(plain_calls) == 2:
            assert hooks["key_fn"]("refresh", 1)
            return -6
        return 1

    monkeypatch.setattr(plain_picker, "plain_pick", plain)
    assert view_picker.run_view_session(flags, ui, session, picker=unavailable, poster_manager=manager) == "b"
    assert len(attempts) == 1
    assert len(plain_calls) == 3
    assert plain_calls[0].endswith("First") and plain_calls[-1].endswith("Second")
    assert [event.kind for event in events] == ["tab", "action", "select"]
    assert events[1].value == "refresh"
    assert flags.plain_mode is False


@pytest.mark.parametrize("error", [OSError, RuntimeError])
def test_unrelated_renderer_errors_do_not_activate_fallback(monkeypatch, tmp_path, error):
    from media_cli.ui import plain_picker, view_picker

    flags, ui, manager = environment(monkeypatch, tmp_path)
    spec = ViewSpec("root", "Pick", (), details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(spec, lambda event: ExitSession())
    calls = []
    monkeypatch.setattr(plain_picker, "plain_pick", lambda *args, **kwargs: calls.append(True))

    def broken(*args, **kwargs):
        raise error("Unrelated failure")

    with pytest.raises(error, match="Unrelated failure"):
        view_picker.run_view_session(flags, ui, session, picker=broken, poster_manager=manager)
    assert calls == []


def test_plain_execution_suspends_an_existing_raw_terminal(monkeypatch):
    from media_cli.ui import plain_picker, view_renderer

    steps = []

    @contextmanager
    def suspend():
        steps.append("canonical")
        try:
            yield
        finally:
            steps.append("raw")

    monkeypatch.setattr(view_renderer, "current_runtime", lambda: SimpleNamespace(suspend=suspend))
    monkeypatch.setattr(plain_picker, "plain_pick", lambda *args, **kwargs: steps.append("plain") or -3)
    renderer = view_renderer.ViewRenderer(SimpleNamespace(plain_mode=True))
    assert renderer.pick(None, None, "Pick", []) == -3
    assert steps == ["canonical", "plain", "raw"]


def test_plain_failure_is_not_retried():
    from media_cli.ui.view_renderer import ViewRenderer

    renderer = ViewRenderer(SimpleNamespace(plain_mode=True))
    error = RendererUnavailable("Plain failed")
    with pytest.raises(RendererUnavailable, match="Plain failed"):
        renderer.fallback(error)


def test_default_dispatch_rebuilds_before_real_plain_command_input(monkeypatch, tmp_path):
    from media_cli.ui import picker, plain_picker, view_picker

    flags, ui, manager = environment(monkeypatch, tmp_path)
    flags.show_image = False
    attempts, output = [], []

    @contextmanager
    def no_terminal():
        attempts.append(True)
        yield None

    original_plain = plain_picker.plain_pick

    def plain(*args, **kwargs):
        return original_plain(*args, **kwargs, input_fn=lambda prompt: "refresh", output_fn=output.append)

    monkeypatch.setattr(picker, "borrow_terminal", no_terminal)
    monkeypatch.setattr(plain_picker, "plain_pick", plain)
    spec = ViewSpec(
        "root",
        "Pick",
        (ViewItem("a", "A", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        actions=(ViewAction("refresh", "Reload", ("CTRL_R",)),),
    )
    session = ViewSession(spec, lambda event: ExitSession(event.value))
    assert view_picker.run_view_session(flags, ui, session, poster_manager=manager) == "refresh"
    assert attempts == [True]
    assert any("[refresh] Reload" in line for line in output)
    assert not any("Ctrl+R" in line for line in output)


def test_fallback_restores_real_terminal_input_settings(monkeypatch, tmp_path):
    import os
    import pty
    import termios

    from media_cli.ui import plain_picker, view_picker
    from media_cli.ui.terminal_runtime import TerminalRuntime

    flags, ui, manager = environment(monkeypatch, tmp_path)
    flags.show_image = False
    master, slave = pty.openpty()
    try:
        original = termios.tcgetattr(slave)
        calls = []

        def plain(*args, **kwargs):
            assert termios.tcgetattr(slave) == original
            calls.append(True)
            return -3

        def unavailable(*args, **kwargs):
            assert not termios.tcgetattr(slave)[3] & termios.ICANON
            raise RendererUnavailable("Descriptor unavailable after raw acquisition")

        monkeypatch.setattr(plain_picker, "plain_pick", plain)
        spec = ViewSpec("root", "Pick", (), details=DetailLines(Blank, Blank, Blank))
        session = ViewSession(spec, lambda event: ExitSession())
        with TerminalRuntime(fd=slave):
            view_picker.run_view_session(flags, ui, session, picker=unavailable, poster_manager=manager)
            assert not termios.tcgetattr(slave)[3] & termios.ICANON
        assert termios.tcgetattr(slave) == original
        assert calls == [True]
    finally:
        os.close(master)
        os.close(slave)
