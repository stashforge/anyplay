"""Failed checks retain known episodes and use a separate retry cooldown."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from media_cli.domain import history


@pytest.mark.parametrize("provider", ["allanime", "example"])
@pytest.mark.parametrize("raises", [True, False])
def test_failed_refresh_preserves_known_data_and_success_time(monkeypatch, provider, raises):
    now = 10_000
    show = dict(_id="one", _provider=provider, name="Example", status="RELEASING",
                availableEpisodes={"sub": 11}, _provider_catalog_checked_at=5_000,
                description="Cached", genres=["Drama"])
    entry = dict(show=show, episode="10", translation_type="sub", was_caught_up=True, has_new_release=True)
    original = deepcopy(show)

    def catalogue(*args, **kwargs):
        if raises:
            raise RuntimeError("Provider unavailable")
        return {"state": "unavailable", "ids": [], "error": "Provider unavailable"}

    metadata = {"availableEpisodes": {"sub": 1}, "status": "FINISHED"}
    monkeypatch.setattr(history.time, "time", lambda: now)
    monkeypatch.setattr("media_cli.services.catalog.get_allanime_show", lambda *a: metadata)
    monkeypatch.setattr("media_cli.services.catalog.fetch_episode_catalog", catalogue)
    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda *a: SimpleNamespace(
        get_title=lambda *a: metadata, episode_catalog=catalogue,
    ))
    assert history.refresh_history_entry_provider_catalog(entry)
    assert show["availableEpisodes"] == original["availableEpisodes"]
    assert show["status"] == original["status"]
    assert show["_provider_catalog_checked_at"] == 5_000
    assert show.get("_episode_catalog_state") != "loaded"
    assert show["_provider_catalog_failed_at"] == now
    assert show["_provider_catalog_refresh_error"] == "Provider unavailable"
    assert entry["has_new_release"]
    assert not history.history_entry_refresh_is_stale(entry, now=now + 59)
    assert history.history_entry_refresh_is_stale(entry, now=now + 60)


def test_failed_provider_can_retry_even_when_anilist_metadata_is_fresh():
    entry = {"show": {"_provider_catalog_failed_at": 1_000, "_anilist_airing_checked_at": 1_059}}
    assert not history.history_entry_refresh_is_stale(entry, now=1_059)
    assert history.history_entry_refresh_is_stale(entry, now=1_060)


def test_success_clears_failure_and_uses_normal_cooldown(monkeypatch):
    show = dict(_id="one", _provider="example", name="Example", availableEpisodes={"sub": 11},
                _provider_catalog_failed_at=9_900, _provider_catalog_refresh_error="Old error",
                description="Cached", genres=["Drama"])
    entry = dict(show=show, episode="10", translation_type="sub", was_caught_up=True, has_new_release=True)
    monkeypatch.setattr(history.time, "time", lambda: 10_000)
    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda *a: SimpleNamespace(
        episode_catalog=lambda *a, **kw: {"state": "loaded", "ids": ["10", "11"], "labels": {}},
    ))
    assert history.refresh_history_entry_provider_catalog(entry)
    assert show["_provider_catalog_checked_at"] == 10_000
    assert show["_provider_catalog_failed_at"] == 0
    assert show["_provider_catalog_refresh_error"] == ""
    assert show["_episode_catalog_state"] == "loaded"
    assert not history.history_entry_refresh_is_stale(entry, now=10_060)
    assert history.history_entry_refresh_is_stale(entry, now=11_800)


def test_linked_provider_retry_is_not_blocked_by_anilist_cooldown(monkeypatch):
    from media_cli.core import anilist

    entry = dict(show=dict(_id="one", _provider="example", aniListId="42", name="Example",
                          status="RELEASING", description="Cached", genres=["Drama"], thumbnail="cover",
                          availableEpisodes={"sub": 11}, _anilist_airing_checked_at=9_999,
                          _provider_catalog_failed_at=9_939), episode="10", translation_type="sub")
    calls = []

    def retry(target):
        calls.append(target)
        target["show"].update(_provider_catalog_failed_at=0, _provider_catalog_checked_at=10_000)
        return True

    monkeypatch.setattr(anilist._time, "time", lambda: 10_000)
    monkeypatch.setattr(anilist, "_provider_catalog_refresh_fn", retry)
    monkeypatch.setattr(anilist.anilist_service, "fetch", lambda *a, **kw: pytest.fail("Fresh AniList data refetched"))
    monkeypatch.setattr(anilist.storage, "patch_history_entry_state", lambda *a: True)
    assert anilist.refresh_history_anilist_airing_batch([entry])
    assert calls == [entry]


def test_failure_cooldown_survives_history_reload(monkeypatch, tmp_path):
    from media_cli.core import storage

    monkeypatch.setattr(storage.paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage, "_history_cache", None)
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    entry = dict(show=dict(_id="one", name="Example", availableEpisodes={"sub": 11},
                          _provider_catalog_checked_at=5_000), episode="10", translation_type="sub",
                 was_caught_up=True, has_new_release=True)
    storage.save_refreshed_history([entry])
    entry["show"].update(_provider_catalog_failed_at=10_000, _provider_catalog_attempted_at=10_000,
                         _provider_catalog_refresh_error="Unavailable")
    storage.patch_history_entry_state(entry)
    storage._history_cache = None
    loaded = storage.load_history()[0]
    assert loaded["show"]["availableEpisodes"] == {"sub": 11}
    assert loaded["has_new_release"]
    assert not history.history_entry_refresh_is_stale(loaded, now=10_059)
    assert history.history_entry_refresh_is_stale(loaded, now=10_060)
