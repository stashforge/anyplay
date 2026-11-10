from types import SimpleNamespace

from media_cli.playback.menu import run_playback_menu


def player():
    return SimpleNamespace(props={}, process=SimpleNamespace(poll=lambda: None),
                           client=None, _ipc_buffer="", send_cmd=lambda *a: None)


def test_menu_escape_keeps_playback_running():
    p = player()
    assert run_playback_menu(p, ["Mirror"], "Mirrors", picker=lambda **kw: -2) == ("RETURN", None)


def test_menu_inactivity_exits_only_after_thirty_seconds():
    p = player()
    clock = [0]
    def pick(**kw):
        clock[0] = 29
        assert not kw["exit_fn"]()
        kw["key_fn"]("DOWN", 0)
        clock[0] = 58
        assert not kw["exit_fn"]()
        clock[0] = 60
        assert kw["exit_fn"]()
        return -6
    assert run_playback_menu(p, ["Mirror"], "Mirrors", picker=pick, clock=lambda: clock[0]) == ("RETURN", None)


def test_menu_closed_player_returns_details():
    p = player()
    p.process = SimpleNamespace(poll=lambda: 0)
    def pick(**kw):
        assert kw["exit_fn"]()
        return -6
    assert run_playback_menu(p, ["Mirror"], "Mirrors", picker=pick) == ("ENDED", None)


def test_eof_inside_menu_retires_menu_and_preserves_event_for_owner():
    p = player()
    p._ipc_buffer = '{"event":"end-file","reason":"eof"}\n'.replace("\\n", "\n")
    def pick(**kwargs):
        assert kwargs["exit_fn"]()
        return -6
    assert run_playback_menu(p, ["Mirror"], "Mirrors", picker=pick) == ("ENDED", None)
    assert p.props["_menu_end_reason"] == "eof"
    assert '"end-file"' in p._ipc_buffer


def test_menu_selection_keeps_current_pause_state():
    p = player()
    p.props["pause"] = True
    assert run_playback_menu(p, ["Mirror"], "Mirrors", picker=lambda **kw: 0) == ("SELECT", 0)
    assert p.props["pause"] is True


def test_playback_view_reuses_original_view(monkeypatch):
    from media_cli.playback import menu
    from media_cli.ui.view_model import ViewSpec, ViewItem, Inherit, DetailLines, Blank
    from media_cli.ui.view_session import ExitSession
    from media_cli.ui import view_picker

    view = ViewSpec("original:quality", "Select quality", (ViewItem("quality:3", "1080p", reference=3, details=Inherit),), details=DetailLines(Blank, Blank, Blank))
    def render(flags, ui, session, **kwargs):
        assert session.current is view
        session.dispatch(__import__("media_cli.ui.view_session", fromlist=["UiEvent"]).UiEvent(
            "select", view.id, session.generation, "quality:3"))
        return session.result
    monkeypatch.setattr(view_picker, "run_view_session", render)
    monkeypatch.setattr(menu, "run_playback_menu", lambda p, labels, title, picker: ("SELECT", picker()))
    assert menu.run_playback_view(player(), view, None, None) == ("SELECT", 3)


def test_playback_view_timeout_exits_structured_session(monkeypatch):
    from media_cli.playback import menu
    from media_cli.ui import view_picker, picker
    from media_cli.ui.view_model import ViewSpec, ViewItem, Inherit, DetailLines, Blank
    from media_cli import app_core

    view = ViewSpec("quality", "Select quality", (ViewItem("q", "720p", reference=0, details=Inherit),),
                    details=DetailLines(Blank, Blank, Blank))
    monkeypatch.setattr(app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(picker, "tui_pick", lambda *a, **kw: -6 if kw["exit_fn"]() else 0)
    def render(flags, ui, session, **kwargs):
        kwargs["picker"](flags, ui, view.prompt, ["720p"])
        assert session.exited
        return session.result
    monkeypatch.setattr(view_picker, "run_view_session", render)
    monkeypatch.setattr(menu, "run_playback_menu", lambda p, labels, title, picker: (
        "RETURN", picker(exit_fn=lambda: True, key_fn=lambda *a: False, header_fn=lambda i: "Returning")))
    assert menu.run_playback_view(player(), view, None, None) == ("RETURN", -2)


def test_playback_footer_hint_uses_footer_style():
    from media_cli.playback.menu import playback_picker
    from media_cli.ui.footer import MUTED

    captured = {}
    def pick(*args, **kwargs):
        captured["header"] = kwargs["header_fn"](0)
        return -2
    playback_picker(pick, {"key_fn": lambda *a: False, "exit_fn": lambda: False,
                          "header_fn": lambda i: "Esc=player"},
                    header_fn=lambda i: "Title\nAlternative\nMetadata\nAnimeThemes • Esc=back")
    assert captured["header"].splitlines()[-1].startswith(MUTED)
    assert "Esc=back" not in captured["header"]


def test_playback_menu_clears_previous_frame_before_each_open(monkeypatch):
    from media_cli.playback import menu
    from media_cli.ui import terminal_output, picker, terminal_images

    calls = []
    monkeypatch.setattr(picker, "invalidate_screen_buffer", lambda: calls.append("invalidate"))
    monkeypatch.setattr(terminal_images, "clear_if_active", lambda: "")
    monkeypatch.setattr(terminal_output, "write_terminal", lambda text: calls.append(text))
    for _ in range(2):
        run_playback_menu(player(), ["Mirror"], "Mirrors", picker=lambda **kw: -2)
    assert calls.count("invalidate") == 2
    assert sum("\x1b[2J" in value for value in calls) == 2
