"""Concurrent refresh/progress saves preserve independent history updates."""

from copy import deepcopy
from threading import Event, Thread, current_thread

import pytest

from media_cli.core import storage


@pytest.fixture
def history(monkeypatch):
    entries = [dict(show=dict(_id=str(i), name=f"Title {i}", availableEpisodes={"sub": 10}),
                    episode="10", translation_type="sub", was_caught_up=True, has_new_release=False)
               for i in range(3)]
    monkeypatch.setattr(storage, "_history_cache", deepcopy(entries))
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    return entries


def refreshed(entry):
    result = deepcopy(entry)
    result["show"]["availableEpisodes"]["sub"] = 11
    result["has_new_release"] = True
    return result


def test_delayed_save_cannot_overwrite_other_titles(history, monkeypatch):
    paused, release, others_done = Event(), Event(), Event()
    writes, errors = [], []

    def persist(path, data, **kwargs):
        if current_thread().name == "delayed":
            paused.set()
            if not release.wait(3):
                raise RuntimeError("Test did not release history save")
        writes.append(deepcopy(data))

    def run(entries):
        try:
            for entry in entries:
                storage.patch_history_entry_state(refreshed(entry))
        except BaseException as error:
            errors.append(error)
        finally:
            if current_thread().name != "delayed":
                others_done.set()

    monkeypatch.setattr(storage, "_atomic_write_json", persist)
    first = Thread(target=run, args=(history[:1],), name="delayed")
    other = Thread(target=run, args=(history[1:],), name="other")
    first.start()
    try:
        assert paused.wait(3)
        other.start()
        others_done.wait(0.1)
    finally:
        release.set()
        first.join(3)
        if other.ident is not None:
            other.join(3)
    assert not errors
    assert not first.is_alive() and not other.is_alive()
    assert [entry["show"]["availableEpisodes"]["sub"] for entry in writes[-1]] == [11, 11, 11]
    assert all(entry["has_new_release"] for entry in writes[-1])
    assert storage.load_history() == writes[-1]


def test_history_readers_cannot_mutate_shared_state(history):
    snapshot = storage.load_history()
    snapshot[0]["show"]["availableEpisodes"]["sub"] = 1
    assert storage.load_history()[0]["show"]["availableEpisodes"]["sub"] == 10


def test_refresh_of_old_snapshot_does_not_rewind_playback_progress(history, monkeypatch):
    stale = refreshed(history[0])
    latest = deepcopy(history)
    latest[0]["episode"] = "11"
    monkeypatch.setattr(storage, "_history_cache", latest)
    monkeypatch.setattr(storage, "_atomic_write_json", lambda *a, **kw: None)
    storage.patch_history_entry_state(stale)
    entry = storage.load_history()[0]
    assert entry["episode"] == "11"
    assert not entry["has_new_release"]


def test_stale_flags_cannot_erase_a_known_new_release(history, monkeypatch):
    latest = deepcopy(history)
    latest[0] = refreshed(history[0])
    monkeypatch.setattr(storage, "_history_cache", latest)
    monkeypatch.setattr(storage, "_atomic_write_json", lambda *a, **kw: None)
    stale = refreshed(history[0])
    stale.update(was_caught_up=False, has_new_release=False)
    storage.patch_history_entry_state(stale)
    assert storage.load_history()[0]["has_new_release"]


def test_episode_mapping_does_not_hold_history_lock(history, monkeypatch, tmp_path):
    def mapping(*args):
        finished = Event()
        def refresh():
            try:
                storage.patch_history_entry_state(refreshed(history[1]))
            finally:
                finished.set()
        worker = Thread(target=refresh)
        worker.start()
        try:
            assert finished.wait(2), "Provider work blocked another history update"
        finally:
            worker.join(2)
        return "11"

    monkeypatch.setattr(storage, "episode_id_for_progress", mapping)
    monkeypatch.setattr(storage.paths, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "load_config", lambda: {})
    monkeypatch.setattr(storage, "_atomic_write_json", lambda *a, **kw: None)
    storage.write_history_progress(history[0]["show"], 11, "sub")
    entries = {entry["show"]["_id"]: entry for entry in storage.load_history()}
    assert entries["0"]["episode"] == "11"
    assert entries["1"]["show"]["availableEpisodes"]["sub"] == 11


def test_enrichment_only_patches_owned_fields(history, monkeypatch):
    from media_cli.core.enrichment import _persist_enrichment_state

    monkeypatch.setattr(storage, "_atomic_write_json", lambda *a, **kw: None)
    storage.patch_history_entry_state(refreshed(history[0]))
    stale_show = deepcopy(history[0]["show"])
    stale_show["_enrichment_attempted"] = True
    _persist_enrichment_state(stale_show)
    entry = storage.load_history()[0]
    assert entry["show"]["availableEpisodes"]["sub"] == 11
    assert entry["show"]["_enrichment_attempted"]
