"""Finished tracker segments must not suppress authoritative provider refreshes."""
from types import SimpleNamespace

import pytest

from media_cli.core import anilist
from media_cli.core.terminal import strip_ansi
from media_cli.domain import history
from media_cli.domain.metadata import anime_status_label


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("tracker_fresh", [False, True])
def test_provider_refresh_repairs_stale_finished_status_and_history_category(monkeypatch, force, tracker_fresh):
    now = 10_000
    entry = dict(show=dict(_id="https://provider.test/long-running/", _provider="animexin",
                          name="Long Running Show", type="ONA", format="ONA",
                          status="FINISHED", _anilist_status="FINISHED", aniListId="123",
                          episodeCount=219, _anilist_episode_count=52,
                          availableEpisodes={"sub": 216}, description="Cached", genres=["Action"],
                          thumbnail="https://provider.test/cover.jpg",
                          _provider_catalog_checked_at=1,
                          _anilist_airing_checked_at=now - 1 if tracker_fresh else 1),
                 episode="216", progress=216, translation_type="sub")
    calls = []
    provider = SimpleNamespace(
        get_title=lambda ident: dict(_id=ident, _provider="animexin", type="ONA",
                                    status="RELEASING", episodeCount=219, availableEpisodes={"sub": 216}),
        episode_catalog=lambda *a, **kw: {"state": "loaded", "ids": ["216"], "labels": {"216": "216"}},
    )
    monkeypatch.setattr(anilist._time, "time", lambda: now)
    monkeypatch.setattr(history.time, "time", lambda: now)
    monkeypatch.setattr(anilist.anilist_service, "fetch", lambda *a, **kw: [])
    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda show: provider)

    def refresh(target):
        calls.append(target)
        return history.refresh_history_entry_provider_catalog(target, enrich=False)

    monkeypatch.setattr(anilist, "_provider_catalog_refresh_fn", refresh)
    assert history.history_entry_category(entry) == "Active"
    assert anilist.refresh_history_anilist_airing_batch([entry], force=force, persist=False)
    assert len(calls) == 1
    assert entry["show"]["_anilist_status"] == "FINISHED"
    assert entry["show"]["status"] == "RELEASING"
    assert strip_ansi(anime_status_label(entry["show"])) == "AIRING"
    assert history.history_entry_category(entry) == "Up to date"


def test_fresh_provider_metadata_respects_cooldown(monkeypatch):
    now = 10_000
    entry = dict(show=dict(_id="one", _provider="animexin", type="ONA", aniListId="123",
                          status="RELEASING", _anilist_status="FINISHED", episodeCount=219,
                          availableEpisodes={"sub": 216}, description="Cached", genres=["Action"],
                          thumbnail="cover", _provider_catalog_checked_at=now - 1,
                          _anilist_airing_checked_at=1),
                 episode="216", progress=216, translation_type="sub")
    calls = []
    monkeypatch.setattr(anilist._time, "time", lambda: now)
    monkeypatch.setattr(anilist.anilist_service, "fetch", lambda *a, **kw: [])
    monkeypatch.setattr(anilist, "_provider_catalog_refresh_fn", lambda target: calls.append(target) or False)
    assert anilist.refresh_history_anilist_airing_batch([entry], persist=False) is False
    assert not calls
