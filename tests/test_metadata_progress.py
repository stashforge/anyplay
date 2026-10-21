from media_cli.core.terminal import strip_ansi
from media_cli.domain.metadata import (
    format_available_episodes,
    format_info_metadata_line,
    format_progress,
)
from media_cli.ui.info_panel import build_info_panel


def test_named_movie_metadata_uses_numeric_progress_and_availability():
    show = {
        "_id": "movie", "_provider": "animekhor", "name": "Example",
        "status": "RELEASING", "type": "ONA", "episodeCount": 1,
        "availableEpisodes": {"sub": 1}, "_episode_ids": ["opaque"],
        "_episode_labels": {"opaque": "Movie"},
        "_local_progress": 1, "_local_episode_label": "Movie",
    }
    rendered = strip_ansi(format_info_metadata_line(show))
    assert "Watched 1" in rendered and "Watched 0" not in rendered
    assert "Avail" not in rendered
    show["_local_progress"] = 0
    assert strip_ansi(format_progress(show)) == "Watched 0"


def test_named_second_episode_metadata_uses_numeric_progress_not_its_label():
    show = {
        "status": "RELEASING", "episodeCount": 12, "availableEpisodes": {"sub": 2},
        "_episode_ids": ["first", "last"], "_episode_labels": {"last": "Finale"},
        "_local_progress": 2, "_local_episode_label": "Finale",
    }
    assert strip_ansi(format_progress(show)) == "Watched 2/12"
    assert format_available_episodes(show) == ""


def test_batch_preparation_keeps_named_progress_separate_from_display_label(monkeypatch):
    from media_cli.context import FLAGS
    from media_cli.domain.metadata import batch_prepare_shows_display_state

    show = {"_id": "movie", "name": "Example", "_episode_ids": ["opaque"],
            "_episode_ids_ttype": "sub", "_episode_labels": {"opaque": "Movie"}}
    monkeypatch.setattr(FLAGS, "incognito_mode", False)
    monkeypatch.setattr("media_cli.core.storage.load_prefs", lambda: {})
    monkeypatch.setattr("media_cli.core.storage.load_history", lambda: [
        {"show": {"_id": "movie", "name": "Example"}, "episode": "opaque", "translation_type": "sub"},
    ])
    batch_prepare_shows_display_state([show])
    assert show["_local_progress"] == 1
    assert show["_local_episode_label"] == "Movie"


def _show(*, total=None, available=21):
    show = {
        "status": "RELEASING",
        "availableEpisodes": {"sub": available},
        "_episode_ids": [f"episode-{number}" for number in range(433, 454)],
        "_episode_labels": {
            f"episode-{number}": f"Episode {number - 432} [{number}]"
            for number in range(433, 454)
        },
        "_local_progress": "451",
        "_local_episode_label": "Episode 19 [451]",
    }
    if total is not None:
        show["episodeCount"] = total
    return show


def test_format_progress_uses_primary_number_for_dual_episode_label():
    rendered = format_progress(_show(total=52), local_only=True, ttype="sub")

    assert strip_ansi(rendered) == "Watched 19/52 [451]"
    assert strip_ansi(format_available_episodes(_show(total=52), ttype="sub")) == "Avail 21 [453]"


def test_format_progress_omits_total_when_season_total_is_unknown():
    rendered = format_progress(_show(), local_only=True, ttype="sub")

    assert strip_ansi(rendered) == "Watched 19 [451]"
    assert strip_ansi(format_available_episodes(_show(), ttype="sub")) == "Avail 21 [453]"


def test_format_progress_omits_stale_total_below_available_count():
    show = _show(total=17)
    rendered = format_progress(show, local_only=True, ttype="sub")

    assert strip_ansi(rendered) == "Watched 19 [451]"
    assert strip_ansi(format_available_episodes(show, local_only=True, ttype="sub")) == "Avail 21 [453]"


def test_format_progress_does_not_use_available_episode_as_total():
    show = {
        "status": "RELEASING",
        "type": "ONA",
        "availableEpisodes": {"sub": 698},
        "_local_progress": "670",
        "_local_episode_label": "670",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 670"


def test_format_progress_does_not_treat_donghua_latest_as_total():
    show = {
        "status": "RELEASING",
        "type": "ONA",
        "_provider": "animexin",
        "episodeCount": 698,
        "availableEpisodes": {"sub": 698},
        "_local_progress": "670",
        "_local_episode_label": "670",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 670"
    assert strip_ansi(format_available_episodes(show, ttype="sub")) == "Avail 698"


def test_format_progress_uses_known_donghua_total_when_above_available():
    show = {
        "status": "RELEASING",
        "type": "ONA",
        "_provider": "animexin",
        "episodeCount": 219,
        "availableEpisodes": {"sub": 214},
        "_local_progress": "214",
        "_local_episode_label": "214",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 214/219"
    rendered = strip_ansi(format_info_metadata_line(show, ttype="sub", local_only=True))
    assert "Watched 214/219" in rendered
    assert "Avail 214" not in rendered


def test_provider_backed_donghua_display_uses_provider_status_over_anilist_status():
    show = {
        "status": "RELEASING",
        "_anilist_status": "FINISHED",
        "type": "DONGHUA",
        "_provider": "lucifer",
        "episodeCount": 52,
        "availableEpisodes": {"sub": 29},
        "_local_progress": "28",
        "_local_episode_label": "28 [58]",
        "airedStart": {"year": 2026},
    }

    rendered = strip_ansi(format_info_metadata_line(show, ttype="sub", local_only=True))
    assert "AIRING" in rendered
    assert "FINISHED" not in rendered
    assert "Watched 28/52" in rendered
    assert "Avail 29" in rendered
    assert "2026 -" in rendered


def test_history_url_progress_uses_clean_episode_label_when_catalog_is_not_cached(monkeypatch, tmp_path):
    from media_cli.core import storage
    from media_cli.state import paths

    monkeypatch.setattr(paths, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage, "_history_cache", None)

    show = {
        "_id": "https://animexin.dev/btth-season-5/",
        "name": "Battle Through The Heavens Season 5",
        "_provider": "animexin",
        "status": "RELEASING",
        "type": "ONA",
        "episodeCount": 219,
        "availableEpisodes": {"sub": 214},
    }
    storage.save_refreshed_history([
        {
            "show": dict(show),
            "episode": "https://animexin.dev/battle-through-the-heavens-season-5-episode-214-indonesia-english-sub/",
            "translation_type": "sub",
        }
    ])

    parts = []
    build_info_panel(show, "sub", 120, parts, local_only=True)

    metadata_line = strip_ansi(parts[2])
    assert "Watched 214/219" in metadata_line
    assert "EP 219" not in metadata_line
    assert "Avail 214" not in metadata_line


def test_format_progress_uses_known_total_and_keeps_available_separate():
    show = {
        "status": "RELEASING",
        "type": "TV",
        "episodeCount": 24,
        "availableEpisodes": {"sub": 10},
        "_local_progress": "2",
        "_local_episode_label": "Episode 2",
    }

    assert strip_ansi(format_progress(show, local_only=True, ttype="sub")) == "Watched 2/24"
    assert strip_ansi(format_available_episodes(show, ttype="sub")) == "Avail 10"


def test_downloads_metadata_prefers_download_watched_state_over_stale_history(monkeypatch, tmp_path):
    from media_cli.core import storage
    from media_cli.state import paths

    monkeypatch.setattr(paths, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage, "_history_cache", None)

    history_show = {
        "_id": "shen-mu-3",
        "name": "Shen Mu 3",
        "status": "FINISHED",
        "type": "ONA",
        "episodeCount": 52,
        "availableEpisodes": {"sub": 52},
    }
    storage.write_history_progress(history_show, "36", "sub")

    downloaded_show = {
        "_id": "shen-mu-3",
        "name": "Shen Mu 3",
        "status": "FINISHED",
        "type": "ONA",
        "episodeCount": 52,
        "availableEpisodes": {"sub": 52},
        "watched_episodes": [str(ep) for ep in range(1, 53)],
    }
    parts = []

    build_info_panel(downloaded_show, "sub", 120, parts, local_only=True)

    metadata_line = strip_ansi(parts[2])
    assert "Watched 52/52" in metadata_line
    assert "Watched 36/52" not in metadata_line
