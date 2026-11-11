import pytest

from media_cli.core.terminal import strip_ansi
from media_cli.ui import player_screen


@pytest.mark.parametrize(
    ("provider", "started", "expected"),
    [
        ("Anikoto", 100, "~ Loading stream from Anikoto... (5s)"),
        ("", 100, "~ Loading stream... (5s)"),
        ("Anikoto", None, "~ Loading stream from Anikoto..."),
    ],
)
def test_shared_player_loading_line_preserves_wording_and_style(monkeypatch, provider, started, expected):
    monkeypatch.setattr(player_screen.time, "time", lambda: 105.9)
    monkeypatch.setattr(player_screen, "spinner_frame", lambda *args: "~")
    line = player_screen._loading_status_line({
        "loading_provider_name": provider, "loading_start_time": started,
    })
    assert strip_ansi(line) == expected
    assert line.startswith("\033[1;36m")
    assert line.endswith("\033[0m")


def test_shared_player_loading_line_refreshes_elapsed_time(monkeypatch):
    now = [102.0]
    monkeypatch.setattr(player_screen.time, "time", lambda: now[0])
    monkeypatch.setattr(player_screen, "spinner_frame", lambda *args: "~")
    state = {"loading_start_time": 100}
    assert "(2s)" in player_screen._loading_status_line(state)
    now[0] = 107.0
    assert "(7s)" in player_screen._loading_status_line(state)


@pytest.mark.parametrize("audio_only,expected", [(True, "Loading audio… (8s)"), (False, "Loading video… (8s)")])
def test_player_startup_label_follows_playback_mode(audio_only, expected):
    assert player_screen._playback_status_text({"_startup_elapsed": 8}, audio_only=audio_only) == expected
