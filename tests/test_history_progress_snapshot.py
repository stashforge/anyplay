"""History progress must survive removal of transient provider catalogues."""

from copy import deepcopy

import pytest

from media_cli.core import storage
from media_cli.core.terminal import strip_ansi
from media_cli.domain.history import history_entry_category, history_entry_progress
from media_cli.domain.metadata import batch_prepare_shows_display_state, format_info_metadata_line


@pytest.fixture
def history_store(monkeypatch, tmp_path):
    from media_cli.context import FLAGS

    monkeypatch.setattr(storage.paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage.paths, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "_history_cache", None)
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    monkeypatch.setattr(storage, "load_config", lambda: {"auto_merge_history": False})
    monkeypatch.setattr(storage, "get_title_sync", lambda *a: False)
    monkeypatch.setattr(storage, "load_prefs", lambda: {})
    monkeypatch.setattr(FLAGS, "incognito_mode", False)


def title():
    return dict(_id="one", _provider="example", name="Example", status="RELEASING", type="ONA",
                _episode_ids=["https://provider.test/prologue/", "https://provider.test/finale/"],
                _episode_ids_ttype="sub",
                _episode_labels={"https://provider.test/prologue/": "Prologue", "https://provider.test/finale/": "Finale"},
                availableEpisodes={"sub": 2})


def test_saved_named_progress_renders_in_history_without_catalogue(history_store):
    show = title()
    storage.save_history(show, show["_episode_ids"][-1], "sub")
    storage._history_cache = None
    entry = storage.load_history()[0]
    assert "_episode_ids" not in entry["show"]
    assert storage.get_local_progress(entry["show"]) == 2
    assert history_entry_progress(entry)[1] == "2"
    assert history_entry_category(entry) == "Up to date"
    batch_prepare_shows_display_state([entry["show"]])
    rendered = strip_ansi(format_info_metadata_line(entry["show"]))
    assert "Watched 2" in rendered and "Watched 0" not in rendered


def test_legacy_entry_is_backfilled_when_catalogue_is_learned(history_store):
    show = title()
    legacy = dict(show={key: value for key, value in show.items() if not key.startswith("_episode_")},
                  episode=show["_episode_ids"][-1], translation_type="sub")
    storage.save_refreshed_history([legacy])
    assert "progress" not in storage.load_history()[0]
    storage.patch_history_entry_show("one", "sub", show)
    storage._history_cache = None
    entry = storage.load_history()[0]
    assert "_episode_ids" not in entry["show"]
    assert storage.get_local_progress(entry["show"]) == 2
    assert entry["episode"] == legacy["episode"]


def test_unmark_and_older_metadata_refresh_do_not_restore_stale_progress(history_store):
    show = title()
    storage.save_history(show, show["_episode_ids"][-1], "sub")
    stale = deepcopy(storage.load_history()[0])
    storage.write_history_progress(show, "0", "sub")
    storage.patch_history_entry_state(stale)
    storage._history_cache = None
    entry = storage.load_history()[0]
    assert entry["episode"] == "0"
    assert storage.get_local_progress(entry["show"]) == 0


def test_loaded_catalogue_remains_authoritative_over_saved_progress(history_store):
    show = title()
    storage.save_history(show, show["_episode_ids"][-1], "sub")
    show["_episode_ids"].insert(0, "new-prologue")
    assert storage.get_local_progress(show) == 3


def test_same_episode_rewrite_without_catalogue_preserves_progress(history_store, monkeypatch):
    show = title()
    episode = show["_episode_ids"][-1]
    storage.save_history(show, episode, "sub")
    stored_show = storage.load_history()[0]["show"]
    monkeypatch.setattr(storage, "episode_id_for_progress", lambda *a: episode)
    storage.write_history_progress(stored_show, episode, "sub", touch=True)
    storage._history_cache = None
    assert storage.get_local_progress(storage.load_history()[0]["show"]) == 2


def test_unknown_legacy_url_is_not_guessed_from_title_or_count(history_store):
    show = {"_id": "one", "name": "Example 2026", "availableEpisodes": {"sub": 1}}
    storage.save_refreshed_history([{
        "show": show, "episode": "https://provider.test/unknown-movie-2026/", "translation_type": "sub",
    }])
    entry = storage.load_history()[0]
    assert "progress" not in entry
    assert storage.get_local_progress(entry["show"]) is None


def test_sparse_catalogue_progress_survives_reload_and_detects_next_release(history_store):
    episode = "https://provider.test/example-episode-13-sub/"
    show = dict(_id="one", _provider="example", name="Example", status="RELEASING",
                _episode_ids=["https://provider.test/example-episode-12-sub/", episode],
                _episode_ids_ttype="sub", availableEpisodes={"sub": 13})
    storage.save_refreshed_history([dict(show=show, episode=episode, translation_type="sub")])
    storage._history_cache = None
    entry = storage.load_history()[0]
    assert "_episode_ids" not in entry["show"]
    assert str(entry["progress"]) == "13"
    assert str(storage.get_local_progress(entry["show"])) == "13"
    assert history_entry_category(entry) == "Up to date"
    entry["show"]["availableEpisodes"]["sub"] = 14
    assert history_entry_category(entry) == "Active"
    assert entry["has_new_release"] is True


@pytest.mark.parametrize("count,absolute,total", [(29, 59, 52), (32, 464, None)])
def test_catalogue_display_survives_history_reload(history_store, count, absolute, total):
    from media_cli.app.show_view_data import build_show_detail_lines

    episode = f"https://provider.test/example-episode-{count}-{absolute}-sub/"
    show = dict(_id="one", _provider="example", name="Example", status="RELEASING", type="ONA",
                _episode_ids=[episode], _episode_ids_ttype="sub",
                _episode_labels={episode: f"Episode {count} [{absolute}]"},
                availableEpisodes={"sub": count}, episodeCount=total)
    storage.save_history(show, episode, "sub")
    detailed = deepcopy(show)
    batch_prepare_shows_display_state([detailed])
    expected = build_show_detail_lines(detailed, "sub").metadata.text
    storage._history_cache = None
    listed = storage.load_history()[0]["show"]
    assert "_episode_ids" not in listed and "_episode_labels" not in listed
    batch_prepare_shows_display_state([listed])
    actual = build_show_detail_lines(listed, "sub", local_only=True).metadata.text
    assert actual == expected
    assert f"[{absolute}]" in actual and "Avail" not in actual


def test_stale_download_original_total_is_not_appended_to_history(history_store):
    show = dict(_id="one", name="Example", status="RELEASING", type="ONA",
                originalEpisodeCount=17, episodeCount=32, availableEpisodes={"sub": 32},
                _local_progress=32, _local_episode_label="Episode 32 [464]")
    listed = strip_ansi(format_info_metadata_line(show, local_only=True))
    detailed = strip_ansi(format_info_metadata_line(show))
    assert listed == detailed
    assert "EP 17" not in listed


def test_legacy_history_panel_repaints_when_catalogue_summary_changes(monkeypatch):
    from media_cli.ui import info_panel

    monkeypatch.setattr(info_panel, "prepare_show_display_state", lambda *a, **kw: None)
    monkeypatch.setattr(info_panel, "_panel_cache", {})
    show = dict(_id="one", name="Example", status="RELEASING", type="ONA",
                _local_progress=32, _local_episode_label=32, availableEpisodes={"sub": 32})
    before = []
    info_panel.build_info_panel(show, "sub", 160, before, local_only=True)
    show["_episode_display"] = {"sub": {"available": "32", "latest_label": "Episode 32 [464]"}}
    after = []
    info_panel.build_info_panel(show, "sub", 160, after, local_only=True)
    assert "Avail 32 [464]" not in strip_ansi("\n".join(before))
    assert "Watched 32 [464]" in strip_ansi("\n".join(after))
    assert "Avail" not in strip_ansi("\n".join(after))


def test_behind_dual_watched_label_survives_history_reload(history_store):
    from media_cli.app.show_view_data import build_show_detail_lines

    previous, latest = "https://provider.test/episode-31-sub/", "https://provider.test/episode-32-sub/"
    show = dict(_id="one", _provider="example", name="Example", status="RELEASING",
                _episode_ids=[previous, latest], _episode_ids_ttype="sub",
                _episode_labels={previous: "Episode 31 [463]", latest: "Episode 32 [464]"},
                availableEpisodes={"sub": 32})
    storage.save_history(show, previous, "sub")
    detailed = deepcopy(show)
    batch_prepare_shows_display_state([detailed])
    expected = build_show_detail_lines(detailed, "sub").metadata.text
    storage._history_cache = None
    listed = storage.load_history()[0]["show"]
    batch_prepare_shows_display_state([listed])
    actual = build_show_detail_lines(listed, "sub", local_only=True).metadata.text
    assert actual == expected
    assert "Watched 31 [463]" in actual and "Avail 32 [464]" in actual

    storage.save_history(show, latest, "sub")
    storage._history_cache = None
    caught_up = storage.load_history()[0]["show"]
    batch_prepare_shows_display_state([caught_up])
    text = build_show_detail_lines(caught_up, "sub", local_only=True).metadata.text
    assert "Watched 32 [464]" in text and "Avail" not in text

    storage.write_history_progress(show, "0", "sub")
    storage._history_cache = None
    unwatched = storage.load_history()[0]["show"]
    batch_prepare_shows_display_state([unwatched])
    text = build_show_detail_lines(unwatched, "sub", local_only=True).metadata.text
    assert "Watched 0" in text and "Watched 0 [" not in text
    assert "Avail 32 [464]" in text


def test_summary_is_not_reused_after_availability_changes_or_for_another_translation(history_store):
    from media_cli.domain.metadata import format_available_episodes

    show = dict(status="RELEASING", availableEpisodes={"sub": 32, "dub": 4},
                _episode_display={"sub": {"available": "32", "latest_label": "Episode 32 [464]"}})
    assert format_available_episodes(show, "sub") == "Avail 32 [464]"
    assert format_available_episodes(show, "dub") == "Avail 4"
    show["availableEpisodes"]["sub"] = 33
    assert format_available_episodes(show, "sub") == "Avail 33"


def test_valid_original_total_is_shown_once_in_both_contexts():
    show = dict(status="FINISHED", episodeCount=3, originalEpisodeCount=24,
                _local_progress=3, _local_episode_label=3)
    listed = strip_ansi(format_info_metadata_line(show, local_only=True))
    detailed = strip_ansi(format_info_metadata_line(show))
    assert listed == detailed
    assert "Watched 3/24" in listed and "EP 24" not in listed
