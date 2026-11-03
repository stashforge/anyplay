"""Refresh batches save once and preserve current progress and provider identity."""
from copy import deepcopy

import pytest

from media_cli.core import storage


@pytest.fixture
def history(monkeypatch):
    entries = [dict(show=dict(_id=str(i), _provider="selected", name=f"Title {i}"),
                    episode="10", progress=10, timestamp=123, translation_type="sub")
               for i in range(34)]
    monkeypatch.setattr(storage, "_history_cache", deepcopy(entries))
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    writes = []
    monkeypatch.setattr(storage, "_atomic_write_json", lambda path, data, **kw: writes.append(deepcopy(data)))
    return entries, writes


def test_many_refresh_results_make_one_history_write(history):
    entries, writes = history
    updates = deepcopy(entries)
    for entry in updates:
        entry["show"]["name"] += " refreshed"
        entry.update(episode="1", progress=1, timestamp=1)
    assert storage.patch_history_entries_state(updates)
    assert len(writes) == 1
    saved = storage.load_history()
    assert len(saved) == 34
    for entry in saved:
        assert entry["show"]["name"].endswith(" refreshed")
        assert (entry["episode"], entry["progress"], entry["timestamp"]) == ("10", 10, 123)


def test_only_changed_entry_is_updated_and_noop_batch_does_not_write(history):
    entries, writes = history
    storage.patch_history_entries_state(deepcopy(entries))
    writes.clear()
    before = storage.load_history()
    update = deepcopy(before[7])
    update["show"]["name"] = "Changed title"
    assert storage.patch_history_entries_state([update])
    assert len(writes) == 1
    after = storage.load_history()
    assert after[7]["show"]["name"] == "Changed title"
    assert after[:7] == before[:7] and after[8:] == before[8:]
    writes.clear()
    assert storage.patch_history_entries_state([deepcopy(after[7])]) is False
    assert storage.patch_history_entries_state([]) is False
    assert not writes


def test_batch_matches_provider_and_translation_and_does_not_resurrect_deleted_entries(history):
    entries, writes = history
    entries = deepcopy(entries[:1])
    neighbor = deepcopy(entries[0])
    neighbor["show"]["_provider"] = "other"
    dub = deepcopy(entries[0])
    dub["translation_type"] = "dub"
    storage._history_cache = [neighbor, dub, deepcopy(entries[0])]
    before = storage.load_history()
    update = deepcopy(entries[0])
    update["show"]["name"] = "Refreshed"
    missing = deepcopy(update)
    missing["show"]["_id"] = "deleted"
    assert storage.patch_history_entries_state([update, missing])
    after = storage.load_history()
    assert len(after) == 3 and after[:2] == before[:2]
    assert after[2]["show"]["name"] == "Refreshed"
    assert len(writes) == 1
