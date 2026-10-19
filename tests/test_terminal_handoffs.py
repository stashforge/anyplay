"""Consumer boundaries preserve terminal ownership and child input modes."""

from __future__ import annotations

import json
import os
import pty
import subprocess
import sys
import termios
from contextlib import nullcontext

import pytest

from media_cli.core import enrichment
from media_cli.media import custom_skips
from media_cli.playback import local
from media_cli.state import config
from media_cli.ui import display, info_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.fixture
def terminal():
    master, slave = pty.openpty()
    try:
        yield master, slave
    finally:
        os.close(slave)
        os.close(master)


@pytest.mark.parametrize("consumer", ["local", "config", "skips"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("outcome", ["success", "nonzero", "launch_error"])
def test_foreground_child_receives_original_terminal(monkeypatch, tmp_path, terminal, consumer, active, outcome):
    from media_cli.ui.terminal_runtime import TerminalRuntime, current_runtime

    _, slave = terminal
    original = termios.tcgetattr(slave)
    real_run = subprocess.run
    observed = []
    errors = []
    video = tmp_path / "episode.mp4"
    video.touch()
    monkeypatch.setattr(local.shutil, "which", lambda command: command)
    monkeypatch.setattr(config, "resolve_editor", lambda override: "editor --wait")
    monkeypatch.setattr(custom_skips, "resolve_editor", lambda settings: ["editor", "--wait"])

    def launch(command, **kwargs):
        child = real_run(
            [
                sys.executable,
                "-c",
                "import json, sys, termios; print(json.dumps(termios.tcgetattr(sys.stdin.fileno())[:6]))",
            ],
            stdin=slave,
            stdout=subprocess.PIPE,
            text=True,
            check=True,
            timeout=5,
        )
        observed.append((json.loads(child.stdout), current_runtime() is None))
        if outcome == "launch_error":
            raise OSError("child unavailable")
        return subprocess.CompletedProcess(command, 7 if outcome == "nonzero" else 0)

    monkeypatch.setattr(subprocess, "run", launch)
    with TerminalRuntime(fd=slave) if active else nullcontext() as runtime:
        if consumer == "local":
            result = local.play_local_video(str(video), "mpv", termux=False, error=errors.append)
            assert result is (outcome != "launch_error")
            assert bool(errors) is (outcome == "launch_error")
        elif consumer == "config":
            result = config.open_config_editor()
            assert result == {"success": 0, "nonzero": 7, "launch_error": 1}[outcome]
        elif outcome == "success":
            result = custom_skips.edit_skip_form(title="Demo", providers=["all"], entry=None, config={})
            assert result["mode"] == "session"
        else:
            exception = OSError if outcome == "launch_error" else custom_skips.SkipEditorError
            with pytest.raises(exception):
                custom_skips.edit_skip_form(title="Demo", providers=["all"], entry=None, config={})
        assert len(observed) == 1
        assert observed[0][1], "UI still owns input during the foreground child"
        assert observed[0][0] == original[:6], "Foreground child inherited raw/no-echo mode"
        if active:
            assert current_runtime() is runtime
            assert not termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ICANON)
        else:
            assert termios.tcgetattr(slave) == original
    assert termios.tcgetattr(slave) == original


def test_skip_editor_combines_screen_and_input_handoff(monkeypatch, terminal):
    from media_cli.ui.terminal_runtime import TerminalRuntime

    _, slave = terminal
    original = termios.tcgetattr(slave)
    active, calls = [True], []
    monkeypatch.setattr(custom_skips, "resolve_editor", lambda cfg: ["editor"])
    monkeypatch.setattr(display, "is_alt_screen_active", lambda: active[0])

    def leave():
        calls.append("leave")
        active[0] = False

    def enter():
        assert not termios.tcgetattr(slave)[3] & termios.ECHO
        calls.append("enter")
        active[0] = True

    def launch(command, **kwargs):
        assert not active[0]
        assert termios.tcgetattr(slave) == original
        calls.append("editor")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(display, "exit_alt_screen", leave)
    monkeypatch.setattr(display, "enter_alt_screen", enter)
    monkeypatch.setattr(subprocess, "run", launch)
    with TerminalRuntime(fd=slave):
        custom_skips.edit_skip_form(title="Demo", providers=["all"], entry=None, config={})
    assert calls == ["leave", "editor", "enter"]
    assert active[0]


@pytest.mark.parametrize("interrupt", [False, True])
def test_nested_info_never_restores_outer_terminal(monkeypatch, terminal, interrupt):
    from media_cli.ui.terminal_runtime import TerminalRuntime, current_runtime

    _, slave = terminal
    original = termios.tcgetattr(slave)
    changes = []
    opens = []
    reads = []
    real_setattr = termios.tcsetattr
    real_open = os.open

    def open_terminal(path, flags, *args, **kwargs):
        if path == "/dev/tty":
            opens.append(path)
            return os.dup(slave)
        return real_open(path, flags, *args, **kwargs)

    def change_mode(fd, when, attrs):
        changes.append(attrs)
        return real_setattr(fd, when, attrs)

    def read_key(fd):
        reads.append(fd)
        if interrupt:
            raise KeyboardInterrupt
        return "Q"

    monkeypatch.setattr(os, "open", open_terminal)
    monkeypatch.setattr(info_screen, "_read_key", read_key)
    with TerminalRuntime(fd=slave) as runtime:
        with monkeypatch.context() as scope:
            scope.setattr(termios, "tcsetattr", change_mode)
            if interrupt:
                with pytest.raises(KeyboardInterrupt):
                    info_screen.show_info_screen({"name": "Demo", "_enrichment_attempted": True})
            else:
                info_screen.show_info_screen({"name": "Demo", "_enrichment_attempted": True})
        assert reads
        assert not changes, "Nested info changed or restored the outer terminal mode"
        assert not opens, "Nested info opened a second controlling terminal"
        assert current_runtime() is runtime
        assert not termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ICANON)
        os.fstat(runtime.fileno())
    assert termios.tcgetattr(slave) == original


def test_info_enrichment_uses_loading_before_first_frame(monkeypatch, terminal):
    from media_cli.ui.terminal_runtime import TerminalRuntime

    _, slave = terminal
    show = {"name": "Demo", "availableEpisodes": {"sub": 1}}
    events = []
    real_build = info_screen._build_content
    real_open = os.open

    def open_terminal(path, flags, *args, **kwargs):
        if path == "/dev/tty":
            return os.dup(slave)
        return real_open(path, flags, *args, **kwargs)

    def enrich(candidate):
        events.append("enrich")
        candidate.update(description="Enriched description", genres=["Action"])
        candidate["availableEpisodes"]["sub"] = 2
        return True

    def loading(message, fn, *args, **kwargs):
        events.append("loading")
        return fn(*args, **kwargs)

    def build(candidate, width):
        events.append("frame")
        return real_build(candidate, width)

    monkeypatch.setattr(enrichment, "enrich_show_if_missing", enrich)
    monkeypatch.setattr(display, "with_loading", loading)
    monkeypatch.setattr(info_screen, "_build_content", build)
    monkeypatch.setattr(info_screen, "_read_key", lambda fd: "Q")
    monkeypatch.setattr(os, "open", open_terminal)
    with TerminalRuntime(fd=slave):
        info_screen.show_info_screen(show)
    assert events == ["loading", "enrich", "frame"]
    assert show["description"] == "Enriched description"
    assert show["availableEpisodes"] == {"sub": 2}


def test_cancelled_info_enrichment_returns_without_frame_or_late_mutation(monkeypatch, terminal, capsys):
    from media_cli.ui.terminal_runtime import TerminalRuntime, current_runtime

    _, slave = terminal
    show = {"name": "Demo", "availableEpisodes": {"sub": 1}}
    abandoned = []
    frames = []
    real_open = os.open

    def open_terminal(path, flags, *args, **kwargs):
        if path == "/dev/tty":
            return os.dup(slave)
        return real_open(path, flags, *args, **kwargs)

    def enrich(candidate):
        candidate["description"] = "Late description"
        candidate["availableEpisodes"]["sub"] = 99
        return True

    def cancel_loading(message, fn, *args, **kwargs):
        abandoned.append(lambda: fn(*args, **kwargs))
        raise PreparationCancelled

    monkeypatch.setattr(enrichment, "enrich_show_if_missing", enrich)
    monkeypatch.setattr(display, "with_loading", cancel_loading)
    monkeypatch.setattr(info_screen, "_build_content", lambda *args: frames.append("frame") or [])
    monkeypatch.setattr(info_screen, "_read_key", lambda fd: "Q")
    monkeypatch.setattr(os, "open", open_terminal)
    with TerminalRuntime(fd=slave) as runtime:
        capsys.readouterr()
        info_screen.show_info_screen(show)
        assert not frames
        assert capsys.readouterr().out == ""
        assert current_runtime() is runtime
        assert not termios.tcgetattr(slave)[3] & termios.ECHO
        assert len(abandoned) == 1
        abandoned[0]()
        assert show == {"name": "Demo", "availableEpisodes": {"sub": 1}}


def test_info_without_terminal_returns_without_a_frame(monkeypatch, capsys):
    from media_cli.context import FLAGS

    frames = []
    monkeypatch.setattr(FLAGS, "plain_mode", True)
    monkeypatch.setattr(info_screen, "_build_content", lambda *args: frames.append("frame") or [])
    info_screen.show_info_screen({"name": "Demo", "_enrichment_attempted": True})
    assert not frames
    assert capsys.readouterr().out == ""
