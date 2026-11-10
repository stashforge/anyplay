from types import SimpleNamespace

import pytest

from test_animethemes_song_menu import controller, select
from test_animethemes_songs import anime_fixture
from media_cli.app import animethemes, animethemes_song_playback as flow
from media_cli.playback.mpv import MpvIpc
from media_cli.services.animethemes import anime_song_rows
from media_cli.ui.key_codes import decode_key_bytes


def test_audio_mode_uses_shared_toggle_and_stays_in_collection_session():
    menu, _ = controller()
    select(menu, "action:playback")
    assert not menu.audio_only
    select(menu, "setting:audio-only")
    assert menu.audio_only
    assert any(item.label == "Audio only: On" for item in menu.session.current.items)
    fresh, _ = controller()
    assert not fresh.audio_only


@pytest.mark.parametrize("termux", [False, True])
def test_audio_mode_routes_to_command_line_mpv_on_both_platforms(monkeypatch, termux):
    calls = []
    monkeypatch.setattr(animethemes.app_core, "is_termux", lambda: termux)
    monkeypatch.setattr(animethemes.app_core, "play_android", lambda *a, **kw: pytest.fail("No Android player"))
    monkeypatch.setattr(animethemes.app_core, "play_desktop", lambda *a, **kw: calls.append((a, kw)))
    monkeypatch.setattr(animethemes.app_core, "_exit_player_screen", lambda: None)
    row = anime_song_rows(anime_fixture())[0]["variants"][0]
    animethemes._play_row({**row, "audio_only": True}, {}, SimpleNamespace(player=None))
    assert len(calls) == 1


def test_audio_and_repeat_hooks_are_scoped_and_restore_after_playback(monkeypatch):
    player = MpvIpc()
    old_repeat = lambda: "old"
    player.repeat_control = old_repeat
    monkeypatch.setattr(flow.app_core, "_ipc_player", player)
    monkeypatch.setattr(flow.app_core, "is_termux", lambda: True)
    seen = []
    def play(row):
        assert row["audio_only"] and player.audio_only
        seen.append(player.repeat_control())
        return "QUIT", 0, 10, 90
    menu = flow.create_song_menu(SimpleNamespace(), None, {}, SimpleNamespace(),
                                 anime_song_rows(anime_fixture()), 0,
                                 collection_id="audio", play_callback=play)
    menu.audio_only = True
    menu.play(menu.songs[0], None)
    assert seen == ["Once"]
    assert menu.repeat_mode == "once"
    assert not player.audio_only
    assert player.repeat_control is old_repeat


def test_player_keys_seek_pause_and_repeat_without_affecting_other_providers():
    player = MpvIpc()
    commands = []
    player.send_cmd = lambda *args: commands.append(args)
    assert player.song_key("RIGHT") is None
    assert player.song_key("r") is None
    player.repeat_control = lambda: "Once"
    player.audio_only = True
    for key in ("LEFT", "RIGHT", " ", "r"):
        assert player.song_key(key) == "HANDLED"
    assert commands == [("seek", -5, "relative"), ("seek", 5, "relative"),
                        ("cycle", "pause"), ("show-text", "Repeat: Once", 2000)]
    assert player.song_key(decode_key_bytes(b"\x1b[1;2C")) == "NEXT"
    assert player.song_key(decode_key_bytes(b"\x1b[1;2D")) == "PREV"
    player.audio_only = False
    assert player.song_key("RIGHT") is None


def test_audio_load_disables_video_and_window_even_with_fullscreen_override(monkeypatch):
    player = MpvIpc()
    commands = []
    monkeypatch.setattr(player, "start", lambda: None)
    monkeypatch.setattr(player, "send_cmd", lambda *args: commands.append(args))
    player.audio_only = True
    player.fullscreen_override = True
    player.load("https://example.test/song.webm", "Song", {}, "")
    assert ("set_property", "vid", "no") in commands
    assert ("set_property", "force-window", False) in commands
    assert ("set_property", "fullscreen", True) not in commands


def test_background_player_start_inherits_audio_mode(monkeypatch):
    from media_cli.playback import engine
    modes = []
    candidate = SimpleNamespace(audio_only=False, quit=lambda: None)
    candidate.start = lambda **kw: modes.append(candidate.audio_only)
    player = SimpleNamespace(process=None, client=None, audio_only=True,
                             adopt_startup=lambda accepted: modes.append(accepted.audio_only))
    monkeypatch.setattr(engine, "MpvIpc", lambda: candidate)
    monkeypatch.setattr(engine, "_prepare_owned_playback_resource", lambda prepare, close: prepare(lambda: False))
    engine._start_player_for_playback(player)
    assert modes == [True, True]
