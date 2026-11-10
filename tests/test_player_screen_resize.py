import os
from types import SimpleNamespace

import pytest

from media_cli.context import FLAGS
from media_cli.core.terminal import strip_ansi
from media_cli.ui import player_screen as screen


@pytest.fixture
def player_frame(monkeypatch):
    size = [os.terminal_size((100, 42))]
    frames = []
    images = []
    monkeypatch.setattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(screen.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(screen.sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(screen.os, "get_terminal_size", lambda *a: size[0])
    monkeypatch.setattr(screen, "_safe_stdout_write", lambda text, **kw: frames.append(text) or True)
    monkeypatch.setattr(screen, "_poster_uses_native_protocol", lambda value: value == "NATIVE-COVER")
    monkeypatch.setattr(screen, "_poster_symbol_lines", lambda *a: [])
    monkeypatch.setattr(screen.terminal_images, "replace_for_frame",
                        lambda **kw: images.append(kw["has_image"]) or "")
    state = {"active": True, "show": {"name": "Example anime", "_provider": "animethemes",
                                      "description": "A long synopsis. " * 200},
             "current_ep": "OP1", "total_eps": 1, "status_lines": [],
             "stream_info": {}, "mpv_props": {"playback-time": 30, "duration": 90, "pause": True},
             "content_title": "Example anime", "content_secondary": "English title",
             "content_metadata": "TV • Spring 2023", "content_stream": "Song by Artist • OP 1 • 1080p",
             "audio_only": True, "versions_available": True}
    monkeypatch.setattr(screen, "_player_ui_state", state)
    poster = SimpleNamespace(enabled=lambda: True, get=lambda show: "NATIVE-COVER")
    return SimpleNamespace(size=size, frames=frames, state=state, images=images, poster=poster)


def body(frame):
    return strip_ansi(frame.split("\033[J", 1)[0]).split("\r\n")


@pytest.mark.parametrize("width,height", [(100, 42), (80, 24), (70, 20), (40, 18), (40, 12)])
def test_frame_fits_terminal_and_keeps_playback_controls(player_frame, width, height):
    f = player_frame
    f.size[0] = os.terminal_size((width, height))
    screen.render(f.poster)
    rows = body(f.frames[-1])
    text = "\n".join(rows)
    assert len(rows) <= height - 1
    assert "Paused" in text and "00:30 / 01:30" in text
    assert "Q Quit" in text and "R Repeat" in text and "Space Pause" in text
    assert "Seek 5s" in text and "Previous/Next" in text
    for key, expected in [("_row_state", "Paused"), ("_row_time", "00:30")]:
        assert 1 <= f.state[key] < height
        assert expected in rows[f.state[key] - 1]


def test_shrink_and_grow_clear_stale_frame_and_redraw_same_native_cover(player_frame):
    f = player_frame
    f.state["_poster_manager"] = f.poster
    for width, height, cover in [(100, 42, True), (70, 20, False), (90, 40, True), (80, 36, True)]:
        f.size[0] = os.terminal_size((width, height))
        screen.update_mpv_props(f.state["mpv_props"])
        frame = f.frames[-1]
        assert "\033[2J" in frame
        assert ("NATIVE-COVER" in frame) is cover
        assert len(body(frame)) <= height - 1
    screen.update_mpv_props({"playback-time": 31, "duration": 90, "pause": True})
    assert "00:31" in f.frames[-1]
    assert "Example anime" not in f.frames[-1]  # ordinary ticks stay partial


def test_tiny_terminal_never_scrolls_even_when_all_hints_cannot_fit(player_frame):
    f = player_frame
    f.size[0] = os.terminal_size((25, 8))
    screen.render(f.poster)
    assert len(body(f.frames[-1])) <= 7
    assert "Paused" in "\n".join(body(f.frames[-1]))
