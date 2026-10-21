from types import SimpleNamespace

import pytest

from media_cli.app import animethemes_song_title as titles
from media_cli.playback import desktop


@pytest.mark.parametrize("row,expected", [
    ({"song": "Idol", "artists": "YOASOBI", "anime": "[Oshi no Ko]", "theme": "OP1", "episodes": "1-11"},
     "Idol by YOASOBI - [Oshi no Ko] OP 1 (EP 1-11)"),
    ({"song": "Burning", "artists": "Hitsujibungaku", "anime": "Anime", "theme": "ED1v2", "episodes": "7-11"},
     "Burning by Hitsujibungaku - Anime ED 1 v2 (EP 7-11)"),
    ({"song": "Song", "anime": "Anime", "theme": "OP2"}, "Song - Anime OP 2"),
    ({"song": "Song", "artists": "Singer", "theme": "OP"}, "Song by Singer - OP"),
    ({"song": "Song"}, "Song"),
    ({"song": "Song", "theme": "ED1", "raw": {"entry": {"version": 3, "episodes": "2, 5-8"}}},
     "Song - ED 1 v3 (EP 2, 5-8)"),
    ({"song": "Song", "theme": "OP1", "raw": {"entry": {"version": 1}}}, "Song - OP 1"),
])
def test_player_title_uses_selected_version_range_and_omits_missing_fields(row, expected):
    assert titles.song_player_title(row) == expected


def test_desktop_uses_complete_media_title_without_extra_episode_or_quality():
    loaded = []
    ipc = SimpleNamespace(props={}, load=lambda url, title, *args, **kwargs: loaded.append(title),
                          wait_for_playback=lambda *args, **kwargs: ("QUIT", 0))
    result = desktop.play_desktop(ipc, "Idol", "OP1", {
        "link": "https://example.test/song.webm", "resolution": "1080",
        "media_title": "Idol by YOASOBI - [Oshi no Ko] OP 1 (EP 1-11)"},
        get_resume_time=lambda *args: 0, get_preferred_mirror=lambda *args: {},
        update_stream_info=lambda info: None, aniskip_enabled=False)
    assert result[0] == "QUIT"
    assert loaded == ["Idol by YOASOBI - [Oshi no Ko] OP 1 (EP 1-11)"]


@pytest.mark.parametrize("row,expected", [
    ({"theme": "OP1", "episodes": "1-11", "stream": {"resolution": "1080", "source": "BD"},
      "raw": {"video": {"nc": True}}}, ("OP 1 • Original • EP 1-11", "1080p • BD • NC")),
    ({"theme": "ED1v2", "raw": {"entry": {"version": 2, "episodes": "7-11"}},
      "stream": {"resolution": "720p", "source": "WEB"}},
     ("ED 1 • Version 2 • EP 7-11", "720p • WEB")),
    ({"theme": "OP1", "stream": {"resolution": "Unknown"}}, ("OP 1 • Original", "")),
])
def test_song_player_lines_use_video_metadata_without_episode_placeholders(row, expected):
    assert titles.song_player_lines(row) == expected


def test_normal_player_activation_clears_song_presentation(monkeypatch):
    from media_cli.ui import player_screen

    state = {"content_metadata": "OP 1 • Original", "content_stream": "1080p • BD"}
    monkeypatch.setattr(player_screen, "_player_ui_state", state)
    player_screen.activate({"name": "Normal anime"}, "2", 12)
    assert state["content_metadata"] is None
    assert state["content_stream"] is None


@pytest.mark.parametrize("entries,metadata", [
    ([{"episodes": "1-11"}], "OP 1 • EP 1-11"),
    ([{"episodes": "1-11"}, {"episodes": "1-11"}], "OP 1 • EP 1-11"),
    ([{"episodes": "1-6"}, {"episodes": "7-11"}], "OP 1"),
    ([{"episodes": "1-11"}, {}], "OP 1"),
    ([], "OP 1"),
])
def test_theme_result_header_never_invents_or_picks_a_version_range(entries, metadata):
    theme = {"slug": "OP1", "anime": {"name": "Anime"},
             "song": {"title": "Song", "artists": [{"name": "Singer"}]},
             "animethemeentries": entries}
    assert titles.theme_result_lines(theme) == ("Song", "No alternative title", "by Singer · Anime · " + metadata.replace(" • ", " · "))


def test_missing_anime_or_artist_does_not_duplicate_song_information():
    assert titles.theme_result_lines({"slug": "ED1", "song": {"title": "Song"}}) == ("Song", "No alternative title", "ED 1")


def test_song_alternate_title_never_uses_anime_alternative_title():
    raw = {"slug": "OP1", "anime": {"name": "Anime", "english_name": "Anime alternate"},
           "song": {"title": "Song", "alternative_title": "Alternate song"}}
    assert titles.theme_result_lines(raw) == ("Song", "Alternate song", "Anime · OP 1")
    del raw["song"]["alternative_title"]
    assert titles.theme_result_lines(raw)[1] == "No alternative title"
