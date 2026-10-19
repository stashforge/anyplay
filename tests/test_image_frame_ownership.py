"""Native image ownership follows committed destination frames."""

import os
from io import StringIO
from types import SimpleNamespace

import pytest

from media_cli.context import FLAGS
from media_cli.ui import download_manager, info_screen, player_screen, terminal_images

DELETE = "\033_Ga=d\033\\"
NATIVE = "\033_Ga=T,f=100;AAAA\033\\"


class FrameOutput(StringIO):
    def __init__(self):
        super().__init__()
        self.frames = []

    def isatty(self):
        return True

    def write(self, text):
        self.frames.append(text)
        return super().write(text)


@pytest.fixture
def output(monkeypatch):
    stream = FrameOutput()
    streams = SimpleNamespace(stdout=stream, stdin=SimpleNamespace(isatty=lambda: True, fileno=lambda: -1))
    for module in (info_screen, player_screen, download_manager):
        monkeypatch.setattr(module, "sys", streams)
    monkeypatch.setattr(os, "get_terminal_size", lambda *args: os.terminal_size((80, 24)))
    monkeypatch.setattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(terminal_images, "_active", True)
    return stream


@pytest.mark.parametrize("previous", [False, True])
@pytest.mark.parametrize("has_image", [False, True])
def test_replace_for_frame_deletes_previous_owner_before_recording_new_owner(monkeypatch, previous, has_image):
    monkeypatch.setattr(terminal_images, "_active", previous)
    during_delete = []

    def deletion():
        during_delete.append(terminal_images._active)
        return DELETE

    monkeypatch.setattr(terminal_images, "clear_terminal_images", deletion)
    assert terminal_images.replace_for_frame(has_image=has_image) == (DELETE if previous else "")
    assert during_delete == ([False] if previous else [])
    assert terminal_images._active is has_image


@pytest.mark.parametrize("poster", ["", NATIVE])
def test_info_replaces_inherited_cover_only_with_first_ready_frame(monkeypatch, output, poster):
    def content(show, width):
        assert terminal_images._active
        assert output.getvalue() == "", "Source must remain intact until content is ready"
        return ["INFO CONTENT"]

    def close_info(fd):
        assert terminal_images._active is bool(poster)
        return "Q"

    monkeypatch.setattr(info_screen, "_build_content", content)
    monkeypatch.setattr(info_screen, "_read_key", close_info)
    manager = SimpleNamespace(get=lambda show: poster)
    info_screen._show_info_screen({}, -1, poster_manager=manager, ui=None)
    first_frame = next(frame for frame in output.frames if "INFO CONTENT" in frame)
    assert DELETE in first_frame
    assert "\033[2J" in first_frame
    assert first_frame.index(DELETE) < first_frame.index("INFO CONTENT")
    if poster:
        assert first_frame.index("INFO CONTENT") < first_frame.index(poster)
    assert not terminal_images._active


@pytest.fixture
def player(monkeypatch, output):
    monkeypatch.setattr(player_screen, "_player_ui_state", dict(player_screen._player_ui_state))
    player_screen.activate({"name": "Demo", "_enrichment_attempted": True}, 1, 1)
    return player_screen


def test_player_first_frame_keeps_new_native_image_owned(player, output):
    manager = SimpleNamespace(enabled=lambda: True, get=lambda show: NATIVE)
    player.render(poster_manager=manager)
    assert terminal_images._active
    frame = next(frame for frame in output.frames if NATIVE in frame)
    assert DELETE in frame
    assert frame.index(DELETE) < frame.index(NATIVE)
    output.frames.clear()
    player.render(poster_manager=manager)
    assert terminal_images._active
    assert all(DELETE not in frame and NATIVE not in frame for frame in output.frames)


@pytest.mark.parametrize("replacement", ["", "\033_Ga=T,f=100;BBBB\033\\"])
def test_player_changed_poster_replaces_owned_image_in_same_frame(player, output, replacement):
    poster = [NATIVE]
    manager = SimpleNamespace(enabled=lambda: True, get=lambda show: poster[0])
    player.render(poster_manager=manager)
    # Model an already committed player image independently of the first-frame bug.
    terminal_images.mark_active()
    output.frames.clear()
    poster[0] = replacement
    player.render(poster_manager=manager)
    assert terminal_images._active is bool(replacement)
    assert len(output.frames) == 1
    assert DELETE in output.frames[0]
    if replacement:
        assert output.frames[0].index(DELETE) < output.frames[0].index(replacement)


@pytest.mark.parametrize("close_alt", [False, True])
def test_player_teardown_writes_image_deletion_with_screen_exit(player, output, close_alt):
    terminal_images.mark_active()
    player.deactivate(close_alt=close_alt)
    assert not terminal_images._active
    assert len(output.frames) == 1
    assert output.frames[0].startswith(DELETE)
    assert ("\033[?1049l" if close_alt else "\033[2J") in output.frames[0]


@pytest.mark.parametrize("standalone", [False, True])
def test_curses_handoff_clears_cover_after_worker_preparation(monkeypatch, output, standalone):
    def start():
        assert terminal_images._active
        assert DELETE not in output.getvalue()

    def wrapper(runner):
        assert not terminal_images._active
        assert output.getvalue().endswith(DELETE)
        return "BACK"

    monkeypatch.setattr(download_manager.curses, "wrapper", wrapper)
    monkeypatch.setattr(download_manager, "_restore_terminal_input_modes", lambda: None)
    assert download_manager.run_download_manager(SimpleNamespace(start=start), standalone=standalone) == "BACK"
