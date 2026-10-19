import os
import pty
import termios
import threading
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from media_cli.context import FLAGS
from media_cli.playback import desktop
from media_cli.ui import player_screen
from media_cli.ui.terminal_runtime import TerminalRuntime, current_runtime


def test_playback_borrows_existing_owner_without_changing_terminal_modes(monkeypatch):
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    monkeypatch.setattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(player_screen, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True),
    ))
    monkeypatch.setattr(player_screen, "_loading_cancel_event", threading.Event())
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    try:
        with TerminalRuntime(fd=slave) as runtime:
            with player_screen.playback_input_scope() as managed:
                assert managed is True
                assert current_runtime() is runtime
                os.write(master, b"q")
                assert os.read(runtime.fileno(), 1) == b"q"
            assert current_runtime() is runtime
            assert not termios.tcgetattr(slave)[3] & termios.ECHO
        assert termios.tcgetattr(slave) == original
    finally:
        os.close(slave)
        os.close(master)


def test_desktop_enters_handoff_before_playback_reader_and_releases_it():
    calls = []

    @contextmanager
    def handoff():
        calls.append("enter")
        try:
            yield True
        finally:
            calls.append("release")

    def wait(*args, **kwargs):
        assert calls == ["enter"]
        assert kwargs == {"terminal_managed": True}
        calls.append("read")
        return "QUIT", 0

    ipc = SimpleNamespace(props={}, load=lambda *a, **kw: None, wait_for_playback=wait)
    assert desktop.play_desktop(ipc, "Example", "1", {"link": "https://example.test/video.mp4"},
        get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda info: None,
        playback_input_scope=handoff)[0] == "QUIT"
    assert calls == ["enter", "read", "release"]


@pytest.mark.parametrize("quit_requested", [False, True])
def test_pending_loading_cancel_is_honored_before_media_load(monkeypatch, quit_requested):
    from media_cli.playback import engine

    cancelled, quit_event = threading.Event(), threading.Event()
    (quit_event if quit_requested else cancelled).set()
    monkeypatch.setattr(player_screen, "_loading_cancel_event", cancelled)
    monkeypatch.setattr(player_screen, "_loading_quit_event", quit_event)
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    ipc = SimpleNamespace(load=lambda *a, **kw: pytest.fail("Cancelled handoff loaded media"))

    def play():
        return desktop.play_desktop(ipc, "Example", "1", {"link": "https://example.test/video.mp4"},
            get_resume_time=lambda *a: 0, get_preferred_mirror=lambda *a: {}, update_stream_info=lambda info: None,
            playback_input_scope=engine._playback_input_scope)

    if quit_requested:
        with pytest.raises(KeyboardInterrupt):
            play()
    else:
        assert play()[0] == "CANCELLED"


def test_managed_playback_reader_never_changes_terminal_modes(monkeypatch):
    from media_cli.playback import mpv

    player = mpv.MpvIpc()
    monkeypatch.setattr(mpv, "sys", SimpleNamespace(stdin=SimpleNamespace(fileno=lambda: 123)))
    monkeypatch.setattr(mpv, "os", SimpleNamespace(isatty=lambda fd: True))
    monkeypatch.setattr(mpv, "termios", SimpleNamespace(
        tcgetattr=lambda *a: pytest.fail("Playback tried to own shared terminal settings"),
    ))
    assert player.wait_for_playback({}, "1", 1, None, terminal_managed=True)[0] == "QUIT"


@pytest.mark.parametrize("key,expected", [(b"q", "cancel"), (b"\x1b", "cancel"),
                                         (b"\x03", "quit"), (b"\x1b[D", None)])
def test_launch_preflight_checks_queued_keys_without_restoring_echo(monkeypatch, key, expected):
    import select

    from media_cli.ui.transition import PreparationCancelled

    master, slave = pty.openpty()
    monkeypatch.setattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(player_screen, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True),
    ))
    monkeypatch.setattr(player_screen, "_loading_cancel_event", threading.Event())
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    try:
        with TerminalRuntime(fd=slave) as runtime:
            with player_screen.playback_input_scope():
                os.write(master, key)
                assert select.select([runtime.fileno()], [], [], 1)[0]
                assert player_screen.loading_cancel_requested() is False
                assert select.select([runtime.fileno()], [], [], 0)[0], "State query consumed queued input"
                if expected:
                    with pytest.raises(KeyboardInterrupt if expected == "quit" else PreparationCancelled):
                        player_screen.check_loading_handoff_input()
                else:
                    player_screen.check_loading_handoff_input()
                assert not termios.tcgetattr(slave)[3] & termios.ECHO
    finally:
        os.close(slave)
        os.close(master)
