"""List and details preparation must interpret the same title state identically."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from media_cli.context import FLAGS
from media_cli.core import storage
from media_cli.domain.metadata import (
    batch_prepare_shows_display_state,
    format_info_metadata_parts,
    prepare_show_display_state,
)


@pytest.fixture
def display_history(monkeypatch):
    entries = []
    monkeypatch.setattr(FLAGS, "incognito_mode", False)
    monkeypatch.setattr(FLAGS, "sync_force_on", False)
    monkeypatch.setattr(FLAGS, "sync_force_off", False)
    monkeypatch.setattr(storage, "load_history", lambda: deepcopy(entries))
    monkeypatch.setattr(storage, "load_prefs", lambda: {})
    return entries


def assert_same_preparation(show, ttype="sub"):
    listed, detailed = deepcopy(show), deepcopy(show)
    batch_prepare_shows_display_state([listed], ttype)
    prepare_show_display_state(detailed, ttype)
    assert listed["_local_progress"] == detailed["_local_progress"]
    assert listed["_local_episode_label"] == detailed["_local_episode_label"]
    assert format_info_metadata_parts(listed, ttype) == format_info_metadata_parts(detailed, ttype)
    return listed


def test_list_recomputes_progress_after_history_changes(display_history):
    show = dict(_id="one", name="Example", status="RELEASING", availableEpisodes={"sub": 5},
                _local_progress=1, _local_episode_label="1")
    display_history.append(dict(show={"_id": "one"}, episode="5", translation_type="sub"))
    assert str(assert_same_preparation(show)["_local_progress"]) == "5"


@pytest.mark.parametrize("ttype,expected", [("sub", "5"), ("dub", "2")])
def test_list_and_details_use_requested_translation(display_history, ttype, expected):
    show = dict(_id="one", name="Example", availableEpisodes={"sub": 5, "dub": 2})
    display_history.extend((
        dict(show={"_id": "one"}, episode="5", translation_type="sub"),
        dict(show={"_id": "one"}, episode="2", translation_type="dub"),
    ))
    assert str(assert_same_preparation(show, ttype)["_local_progress"]) == expected


def test_download_watch_progress_uses_catalogue_numbers_in_both_views(display_history):
    show = dict(_id="one", name="Example", _episode_ids=["episode-12", "episode-13"],
                _episode_ids_ttype="sub", watched_episodes=["episode-12", "episode-13"],
                availableEpisodes={"sub": 13})
    assert assert_same_preparation(show)["_local_progress"] == 13


def test_batch_still_loads_history_and_preferences_only_once(monkeypatch, display_history):
    history = Mock(return_value=[])
    prefs = Mock(return_value={})
    monkeypatch.setattr(storage, "load_history", history)
    monkeypatch.setattr(storage, "load_prefs", prefs)
    batch_prepare_shows_display_state([dict(_id=str(i), name=f"Example {i}") for i in range(20)])
    history.assert_called_once_with()
    prefs.assert_called_once_with()


@pytest.mark.parametrize('ttype', ['sub', 'dub'])
@pytest.mark.parametrize('episode', ['0', 'ep-two', 'https://example.test/episode-2'])
def test_incognito_batch_reuses_one_snapshot_and_matches_single_title_preparation(monkeypatch, ttype, episode):
    monkeypatch.setattr(FLAGS, 'incognito_mode', True)
    entries = [dict(show=dict(_id=identity, _provider='example', name=identity),
                    episode=episode, translation_type=language)
               for identity, language in [('one', 'sub'), ('one', 'dub'), ('two', 'sub')]]
    history = Mock(side_effect=lambda: deepcopy(entries))
    monkeypatch.setattr(storage, 'load_history', history)
    prefs = Mock(side_effect=AssertionError('Incognito must not read sync preferences'))
    monkeypatch.setattr(storage, 'load_prefs', prefs)
    shows = [dict(_id=identity, _provider='example', name=identity,
                  _episode_ids=['ep-one', 'ep-two'], _episode_ids_ttype=ttype,
                  _episode_labels={'ep-two': '2'}, _sync_enabled=True,
                  _anilist_progress=9, _anilist_list={'progress': 9}, watched_episodes=['ep-one'])
             for identity in ['one', 'two', 'missing']]
    expected = [prepare_show_display_state(deepcopy(show), ttype) for show in shows]
    history.reset_mock()
    before = deepcopy(entries)
    assert batch_prepare_shows_display_state(shows, ttype) is shows
    history.assert_called_once_with()
    prefs.assert_not_called()
    assert shows == expected
    assert entries == before
    assert all(not show['_sync_enabled'] and show['_progress_authority'] == 'LOCAL' for show in shows)


def test_incognito_batch_reuses_empty_snapshot_without_preserving_stale_progress(monkeypatch):
    monkeypatch.setattr(FLAGS, 'incognito_mode', True)
    history = Mock(return_value=[])
    monkeypatch.setattr(storage, 'load_history', history)
    shows = [dict(_id=str(i), _local_progress=10, _local_episode_label='10') for i in range(3)]
    batch_prepare_shows_display_state(shows)
    history.assert_called_once_with()
    assert all(show['_local_progress'] is None and show['_local_episode_label'] is None for show in shows)
