"""Provider-local ID collisions must not merge unrelated watch progress."""
from copy import deepcopy

import pytest

from media_cli.core import storage
from media_cli.domain.matching import is_same_show


def show(provider, name):
    return dict(_id="shared", _provider=provider, name=name)


@pytest.mark.parametrize("strict", [False, True])
def test_equal_provider_local_ids_are_not_cross_provider_identity(strict):
    assert not is_same_show(show("first", "Alpha"), show("second", "Zeta"), strict=strict)
    assert is_same_show(show("second", "Before"), show("second", "After"), strict=strict)
    assert is_same_show(dict(_id="shared"), show("allanime", "Legacy"), strict=strict)


def test_real_tracker_identity_can_still_match_across_providers():
    first, second = show("first", "Alpha"), show("second", "Zeta")
    first["aniListId"] = second["aniListId"] = "123"
    assert is_same_show(first, second, strict=True)


@pytest.fixture
def history(monkeypatch):
    first, second = show("first", "Alpha"), show("second", "Zeta")
    entries = [
        dict(show=first, episode="9", progress=9, timestamp=123,
             translation_type="sub", last_synced_progress=9),
        dict(show=second, episode="3", progress=3, timestamp=456,
             translation_type="sub", last_synced_progress=3),
    ]
    monkeypatch.setattr(storage, "_history_cache", deepcopy(entries))
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    return entries


def test_history_reads_choose_correct_provider_with_colliding_ids(history):
    target = history[1]["show"]
    assert storage.get_history_entry(target)["show"]["_provider"] == "second"
    assert storage.get_local_progress(target) == 3
    assert storage.get_local_episode_label(target) == "3"


@pytest.mark.parametrize("auto_merge", [False, True])
def test_progress_save_preserves_unrelated_provider_entry(history, monkeypatch, auto_merge):
    monkeypatch.setattr(storage, "load_config", lambda: {"auto_merge_history": auto_merge})
    target = deepcopy(history[1]["show"])
    storage.write_history_progress(target, 4, "sub")
    storage._history_cache = None
    remaining = {e["show"]["_provider"]: e for e in storage.load_history()}
    assert set(remaining) == {"first", "second"}
    assert remaining["first"] == history[0]
    assert remaining["second"]["episode"] == "4"
    assert remaining["second"]["last_synced_progress"] == 3
    assert remaining["second"]["timestamp"] == 456
