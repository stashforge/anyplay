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
