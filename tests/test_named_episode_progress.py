"""Descriptive episode labels must not become numeric watched progress."""

from copy import deepcopy

import pytest
from test_provider_details_recovery import recovery  # noqa: F401

from media_cli import app_core
from media_cli.app import playback_menu
from media_cli.core import storage
from media_cli.domain.history import local_progress


@pytest.mark.parametrize("label", ["Movie", "OVA", "Special", "Finale"])
def test_named_label_uses_catalogue_position_for_progress(label):
    episode = "https://provider.test/a-movie-subtitles/"
    show = dict(_id="one", _episode_ids=[episode], _episode_ids_ttype="sub",
                _episode_labels={episode: label})
    entry = dict(show={"_id": "one"}, episode=episode, translation_type="sub")
    assert local_progress([entry], show) == 1


def test_named_second_episode_uses_its_position_not_a_movie_special_case():
    show = dict(_id="one", _episode_ids=["first", "finale"], _episode_ids_ttype="sub",
                _episode_labels={"first": "Prologue", "finale": "Finale"})
    assert local_progress([dict(show=show, episode="finale")], show) == 2


@pytest.mark.parametrize("number", ["13", "13.5", "214"])
def test_sparse_url_catalogue_uses_episode_number_not_row_position(number):
    episode = f"https://provider.test/example-episode-{number}-sub/"
    show = dict(_id="one", _episode_ids=["https://provider.test/example-episode-12-sub/", episode],
                _episode_ids_ttype="sub")
    entry = dict(show=show, episode=episode, translation_type="sub")
    assert str(local_progress([entry], show)) == number


@pytest.mark.parametrize("label,expected", [("Episode 2", "2"), ("Finale", "2")])
def test_catalogue_labels_override_numbers_in_provider_urls(label, expected):
    episode = "https://provider.test/example-episode-214-sub/"
    show = dict(_id="one", _episode_ids=["first", episode], _episode_ids_ttype="sub",
                _episode_labels={episode: label})
    assert str(local_progress([dict(show=show, episode=episode)], show)) == expected


@pytest.mark.parametrize("episode", [
    "https://provider.test/watch/9988",
    "https://provider.test/watch?id=9988",
    "https://provider.test/example-season-5",
])
def test_unlabelled_opaque_url_does_not_become_watched_episode_number(episode):
    show = dict(_id="one", _episode_ids=["first", episode], _episode_ids_ttype="sub")
    assert local_progress([dict(show=show, episode=episode)], show) == 2


@pytest.mark.parametrize("label,expected", [("Episode 51", "51"), ("12.5", "12.5"), ("0", "0")])
def test_numeric_labels_keep_their_episode_numbers(label, expected):
    show = dict(_id="one", _episode_ids=["opaque"], _episode_labels={"opaque": label})
    assert str(local_progress([dict(show=show, episode="opaque")], show)) == expected


def test_marking_named_movie_survives_history_reload_without_losing_its_label(monkeypatch, tmp_path):
    monkeypatch.setattr(storage.paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage.paths, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "_history_cache", None)
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    monkeypatch.setattr(storage, "load_config", lambda: {"auto_merge_history": False})
    monkeypatch.setattr(storage, "get_title_sync", lambda *a: False)
    episode = "https://provider.test/example-movie-subtitles/"
    show = dict(_id="one", _provider="example", name="Example", _episode_ids=[episode],
                _episode_ids_ttype="sub", _episode_labels={episode: "Movie"},
                availableEpisodes={"sub": 1}, episodeCount=1)
    storage.save_history(show, episode, "sub")
    storage._history_cache = None
    reopened = deepcopy(show)
    assert storage.get_local_progress(reopened) == 1
    assert storage.get_local_episode_label(reopened) == "Movie"
    assert storage.get_history_entry(reopened)["episode"] == episode


def test_details_recognizes_saved_named_movie_as_watched(request, monkeypatch):
    r = request.getfixturevalue("recovery")
    episode = "https://provider.test/example-movie-subtitles/"
    r.show.update(_episode_ids=[episode], _episode_ids_ttype="sub", _episode_labels={episode: "Movie"},
                  _local_episode_label="Movie", availableEpisodes={"sub": 1}, episodeCount=1)
    r.ms.current_ep, r.ms.current_ep_index, r.ms.total_eps = episode, 0, 1
    entry = dict(show={"_id": "one", "_provider": r.show["_provider"]},
                 episode=episode, translation_type="sub")
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *a, **kw: [episode])
    monkeypatch.setattr(app_core, "get_local_progress", lambda show, ttype: local_progress([entry], show, ttype))
    assert playback_menu.handle_action_menu_state(r.flags, r.ui, r.ms, {}, r.args, "sub", lambda *a: False) == "HISTORY"
    references = {item.reference for item in r.views[-1].items}
    assert "UNTRACK" in references and "TRACK_ONLY" not in references
