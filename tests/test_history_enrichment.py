import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from media_cli.ui.modals import _show_needs_enrichment
from media_cli.core import storage
from media_cli.domain.metadata import apply_provider_metadata_to_history_show
from media_cli.domain.matching import is_same_show
from media_cli.core.anilist import (
    get_show_anilist_id,
    refresh_history_anilist_airing_batch,
)
from media_cli.state import paths


def test_show_needs_enrichment():
    # Sparse show missing description and genres
    show_sparse = {
        "_id": "182205",
        "name": "Slime S4",
        "_title_enriched": True, # erroneously set previously
    }
    assert _show_needs_enrichment(show_sparse) is True

    # Complete show
    show_complete = {
        "_id": "182205",
        "name": "Slime S4",
        "description": "Some description",
        "genres": ["Action", "Fantasy"],
        "aniListId": "182205",
    }
    assert _show_needs_enrichment(show_complete) is False

    # Attempted show that failed
    show_attempted = {
        "_id": "999999",
        "name": "Unknown",
        "_enrichment_attempted": True,
    }
    assert _show_needs_enrichment(show_attempted) is False


def test_get_show_anilist_id_numeric_provider():
    show_anikoto = {
        "_id": "182205",
        "name": "Slime S4",
        "_provider": "anikoto",
    }
    assert get_show_anilist_id(show_anikoto) == 182205

    show_miruro = {
        "_id": "100922",
        "name": "Grand Blue",
        "_provider": "miruro",
    }
    assert get_show_anilist_id(show_miruro) == 100922

    show_other = {
        "_id": "12345",
        "name": "Some Show",
        "_provider": "animexin",
    }
    assert get_show_anilist_id(show_other) is None


def test_torrent_provider_history_does_not_fuzzy_merge_with_normal_provider():
    torrent_show = {
        "_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "name": "[Group] Example Show S01E01 1080p",
        "_provider": "torrent",
        "aniListId": "123",
        "malId": "456",
    }
    normal_show = {
        "_id": "provider-example-show",
        "name": "Example Show",
        "_provider": "miruro",
        "aniListId": "123",
        "malId": "456",
    }

    assert is_same_show(torrent_show, normal_show) is False
    assert is_same_show(normal_show, torrent_show) is False


def test_torrent_provider_history_matches_same_infohash_only():
    first = {
        "_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "name": "[Group] Example Show S01E01 1080p",
        "_provider": "torrent",
    }
    second = {
        "_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "name": "[Other] Example Show S01E01 720p",
        "_provider": "torrent",
    }

    assert is_same_show(first, second) is True


def test_history_auto_refresh_policy_only_checks_stale_active_and_up_to_date():
    from media_cli.domain import history as history_domain

    now = 2_000
    stale = now - history_domain.HISTORY_REFRESH_COOLDOWN_SECONDS - 1
    fresh = now - 10

    active_entry = {
        "show": {
            "_id": "active",
            "name": "Active",
            "status": "RELEASING",
            "availableEpisodes": {"sub": 12},
            "_provider_catalog_checked_at": stale,
        },
        "episode": "5",
        "translation_type": "sub",
    }
    fresh_entry = {
        "show": {
            "_id": "fresh",
            "name": "Fresh",
            "status": "RELEASING",
            "availableEpisodes": {"sub": 12},
            "_provider_catalog_checked_at": fresh,
        },
        "episode": "5",
        "translation_type": "sub",
    }
    completed_entry = {
        "show": {
            "_id": "done",
            "name": "Done",
            "status": "FINISHED",
            "episodeCount": 12,
            "availableEpisodes": {"sub": 12},
            "_provider_catalog_checked_at": stale,
        },
        "episode": "12",
        "translation_type": "sub",
    }

    assert history_domain.history_entry_should_auto_refresh(active_entry, now=now)
    assert not history_domain.history_entry_should_auto_refresh(fresh_entry, now=now)
    assert not history_domain.history_entry_should_auto_refresh(completed_entry, now=now)
    assert history_domain.history_entry_refresh_is_stale(completed_entry, now=now)


def test_apply_provider_metadata_to_history_show():
    show = {
        "_id": "test-1",
        "name": "My Show",
    }
    provider_show = {
        "_id": "test-1",
        "description": "A great show",
        "genres": ["Comedy", "Sci-Fi"],
        "banner": "https://example.com/banner.jpg",
        "format": "TV",
        "aniListId": "54321",
        "malId": 1234,
    }
    changed = apply_provider_metadata_to_history_show(show, provider_show)
    assert changed is True
    assert show["description"] == "A great show"
    assert show["genres"] == ["Comedy", "Sci-Fi"]
    assert show["banner"] == "https://example.com/banner.jpg"
    assert show["format"] == "TV"
    assert show["aniListId"] == "54321"
    assert show["malId"] == 1234


def test_patch_history_entry_show_preserves_metadata(tmp_path):
    history_file = tmp_path / "history.json"
    paths.HISTORY_PATH = str(history_file)
    storage._history_cache = None

    initial_history = [
        {
            "show": {
                "_id": "test-show",
                "name": "Original Name",
                "_provider": "anikoto",
            },
            "episode": "1",
            "translation_type": "sub",
        }
    ]
    storage.save_refreshed_history(initial_history)

    # Patch with enriched fields
    updated_show = {
        "_id": "test-show",
        "name": "Original Name",
        "_provider": "anikoto",
        "description": "Enriched description",
        "genres": ["Action"],
        "banner": "https://example.com/banner.jpg",
        "format": "TV",
        "aniListId": "999",
    }
    res = storage.patch_history_entry_show("test-show", "sub", updated_show)
    assert res is True

    # Reload from disk and verify fields were preserved
    storage._history_cache = None
    loaded = storage.load_history()
    assert len(loaded) == 1
    s = loaded[0]["show"]
    assert s["description"] == "Enriched description"
    assert s["genres"] == ["Action"]
    assert s["banner"] == "https://example.com/banner.jpg"
    assert s["format"] == "TV"
    assert s["aniListId"] == "999"


def test_patch_history_entry_state_persists_refreshed_category_flags(tmp_path):
    from media_cli.domain.history import history_entry_category

    history_file = tmp_path / "history.json"
    paths.HISTORY_PATH = str(history_file)
    storage._history_cache = None

    stale_entry = {
        "show": {
            "_id": "animexin-show",
            "name": "Donghua",
            "_provider": "animexin",
            "availableEpisodes": {"sub": 14},
        },
        "episode": "https://animexin.dev/donghua-episode-13-indonesia-english-sub/",
        "translation_type": "sub",
        "was_caught_up": True,
        "has_new_release": True,
    }
    storage.save_refreshed_history([stale_entry])

    loaded = storage.load_history()
    entry = loaded[0]
    show = entry["show"]
    show["availableEpisodes"] = {"sub": 13}
    show["_episode_ids"] = [
        "https://animexin.dev/donghua-episode-12-indonesia-english-sub/",
        "https://animexin.dev/donghua-episode-13-indonesia-english-sub/",
    ]
    show["_episode_ids_ttype"] = "sub"

    assert history_entry_category(entry) == "Up to date"
    assert entry["was_caught_up"] is True
    assert entry["has_new_release"] is False
    assert storage.patch_history_entry_state(entry) is True

    storage._history_cache = None
    reloaded = storage.load_history()[0]
    assert reloaded["show"]["availableEpisodes"]["sub"] == 13
    assert "_episode_ids" not in reloaded["show"]
    assert reloaded["was_caught_up"] is True
    assert reloaded["has_new_release"] is False
    assert history_entry_category(reloaded) == "Up to date"


def test_history_refresh_counts_latest_numeric_episode_not_catalog_rows(monkeypatch):
    from media_cli.domain import history as history_domain

    class FakeProvider:
        def get_title(self, show_id):
            return {}

        def episode_catalog(self, show_id, ttype="sub"):
            return {
                "state": "loaded",
                "ids": [
                    "https://animexin.dev/btth-season-5-episode-214-indonesia-english-sub/",
                    "https://animexin.dev/battle-through-the-heavens-season-5-pv-indonesia-english-sub/",
                    "https://animexin.dev/battle-through-the-heavens-special-indonesia-english-sub/",
                ],
                "labels": {
                    "https://animexin.dev/btth-season-5-episode-214-indonesia-english-sub/": "Episode 214",
                    "https://animexin.dev/battle-through-the-heavens-season-5-pv-indonesia-english-sub/": "PV",
                    "https://animexin.dev/battle-through-the-heavens-special-indonesia-english-sub/": "Special",
                },
            }

    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda show: FakeProvider())
    monkeypatch.setattr("media_cli.core.enrichment.enrich_show_if_missing", lambda show: False)

    entry = {
        "show": {
            "_id": "https://animexin.dev/btth-season-5/",
            "name": "Battle Through The Heavens Season 5",
            "_provider": "animexin",
            "status": "RELEASING",
            "type": "ONA",
            "episodeCount": 219,
            "availableEpisodes": {"sub": 216},
        },
        "episode": "https://animexin.dev/btth-season-5-episode-214-indonesia-english-sub/",
        "translation_type": "sub",
        "was_caught_up": True,
        "has_new_release": True,
    }

    assert history_domain.refresh_history_entry_provider_catalog(entry) is True
    assert entry["show"]["availableEpisodes"]["sub"] == 214
    assert history_domain.history_entry_category(entry) == "Up to date"
    assert entry["has_new_release"] is False


def test_provider_donghua_refresh_corrects_polluted_anilist_total_and_status(monkeypatch):
    from media_cli.domain import history as history_domain

    class FakeProvider:
        def get_title(self, show_id):
            return {
                "_id": show_id,
                "name": "Against the Gods Season 2",
                "_provider": "lucifer",
                "status": "RELEASING",
                "type": "DONGHUA",
                "episodeCount": 52,
                "availableEpisodes": {"sub": 29, "dub": 0, "raw": 0},
            }

        def episode_catalog(self, show_id, ttype="sub"):
            return {
                "state": "loaded",
                "ids": [
                    "https://luciferdonghua.in/against-the-gods-season-2-episode-28-58-lucifer-donghua/",
                    "https://luciferdonghua.in/against-the-gods-season-2-episode-29-59-lucifer-donghua/",
                ],
                "labels": {
                    "https://luciferdonghua.in/against-the-gods-season-2-episode-28-58-lucifer-donghua/": "28 [58]",
                    "https://luciferdonghua.in/against-the-gods-season-2-episode-29-59-lucifer-donghua/": "29 [59]",
                },
            }

    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda show: FakeProvider())
    monkeypatch.setattr("media_cli.core.enrichment.enrich_show_if_missing", lambda show: False)

    entry = {
        "show": {
            "_id": "https://luciferdonghua.in/anime/against-the-gods-season-2/",
            "name": "Against the Gods Season 2",
            "_provider": "lucifer",
            "_anilist_status": "FINISHED",
            "status": "FINISHED",
            "type": "DONGHUA",
            "episodeCount": 30,
            "availableEpisodes": {"sub": 29, "dub": 0, "raw": 0},
        },
        "episode": "https://luciferdonghua.in/against-the-gods-season-2-episode-28-58-lucifer-donghua/",
        "translation_type": "sub",
    }

    assert history_domain.refresh_history_entry_provider_catalog(entry) is True
    assert entry["show"]["status"] == "RELEASING"
    assert entry["show"]["episodeCount"] == 52
    assert entry["show"]["availableEpisodes"]["sub"] == 29
    assert history_domain.history_entry_category(entry) == "Active"


def test_provider_donghua_finished_cache_does_not_skip_provider_refresh():
    from media_cli.domain.history import history_can_skip_finished_provider_refresh

    polluted_provider_show = {
        "_provider": "lucifer",
        "status": "FINISHED",
        "type": "DONGHUA",
        "episodeCount": 30,
        "description": "Cached description",
        "genres": ["Action"],
    }
    normal_finished_show = {
        "_provider": "anikoto",
        "status": "FINISHED",
        "type": "TV",
        "episodeCount": 12,
        "description": "Cached description",
        "genres": ["Comedy"],
    }

    assert history_can_skip_finished_provider_refresh(polluted_provider_show) is False
    assert history_can_skip_finished_provider_refresh(polluted_provider_show, require_genres=True) is False
    assert history_can_skip_finished_provider_refresh(normal_finished_show) is True
    assert history_can_skip_finished_provider_refresh(normal_finished_show, require_genres=True) is True


def test_refresh_history_anilist_airing_batch_enriches_full_metadata(tmp_path):
    history_file = tmp_path / "history.json"
    paths.HISTORY_PATH = str(history_file)
    storage._history_cache = None

    history_entries = [
        {
            "show": {
                "_id": "182205",
                "name": "Slime S4",
                "_provider": "anikoto",
            },
            "episode": "1",
            "translation_type": "sub",
        }
    ]

    mock_raw_media = [
        {
            "id": 182205,
            "idMal": 59888,
            "title": {"romaji": "Tensei Shitara Slime Datta Ken 4th Season", "english": "That Time I Got Reincarnated as a Slime Season 4"},
            "format": "TV",
            "status": "RELEASING",
            "episodes": 24,
            "genres": ["Action", "Adventure", "Comedy", "Fantasy"],
            "description": "Rimuru returns for season 4.",
            "averageScore": 85,
            "coverImage": {"large": "https://example.com/cover.jpg"},
            "nextAiringEpisode": {"episode": 2, "airingAt": 1789700000, "timeUntilAiring": 3600},
        }
    ]

    with patch("media_cli.services.anilist.fetch", return_value=mock_raw_media), \
         patch("media_cli.core.anilist._provider_catalog_refresh_fn", None):
        changed = refresh_history_anilist_airing_batch(history_entries)
        assert changed is True

    s = history_entries[0]["show"]
    assert s["description"] == "Rimuru returns for season 4."
    assert s["genres"] == ["Action", "Adventure", "Comedy", "Fantasy"]
    assert s["aniListId"] == "182205"
    assert s["_title_enriched"] is True
    assert s["_next_airing_ep"] == 2


def test_refresh_history_anilist_airing_batch_does_not_truncate_history(tmp_path):
    history_file = tmp_path / "history.json"
    paths.HISTORY_PATH = str(history_file)
    storage._history_cache = None

    history_entries = [
        {
            "show": {
                "_id": "182205",
                "name": "Slime S4",
                "_provider": "anikoto",
                "aniListId": "182205",
            },
            "episode": "1",
            "translation_type": "sub",
        },
        {
            "show": {
                "_id": "100922",
                "name": "Grand Blue",
                "_provider": "anikoto",
                "aniListId": "100922",
            },
            "episode": "1",
            "translation_type": "sub",
        },
    ]
    storage.save_refreshed_history(history_entries)

    mock_raw_media = [
        {
            "id": 182205,
            "title": {"romaji": "Tensei Shitara Slime Datta Ken 4th Season"},
            "format": "TV",
            "status": "RELEASING",
            "episodes": 24,
            "genres": ["Fantasy"],
            "description": "Fresh Slime metadata.",
            "coverImage": {"large": "https://example.com/slime.jpg"},
        }
    ]

    with patch("media_cli.services.anilist.fetch", return_value=mock_raw_media), \
         patch("media_cli.core.anilist._provider_catalog_refresh_fn", None):
        changed = refresh_history_anilist_airing_batch([history_entries[0]], force=True)
        assert changed is True

    storage._history_cache = None
    loaded = storage.load_history()
    assert len(loaded) == 2
    assert [entry["show"]["name"] for entry in loaded] == ["Slime S4", "Grand Blue"]
    assert loaded[0]["show"]["description"] == "Fresh Slime metadata."


def test_refresh_history_anilist_airing_batch_respects_cooldown_unless_forced():
    from media_cli.domain import history as history_domain

    now = 10_000
    history_entries = [
        {
            "show": {
                "_id": "182205",
                "name": "Slime S4",
                "_provider": "anikoto",
                "aniListId": "182205",
                "status": "RELEASING",
                "_anilist_status": "RELEASING",
                "_anilist_airing_checked_at": now - 10,
                "description": "Cached",
                "genres": ["Fantasy"],
                "thumbnail": "https://example.com/cover.jpg",
            },
            "episode": "1",
            "translation_type": "sub",
        }
    ]
    mock_raw_media = [
        {
            "id": 182205,
            "title": {"romaji": "Slime S4"},
            "format": "TV",
            "status": "RELEASING",
            "episodes": 24,
            "genres": ["Fantasy"],
            "description": "Fresh",
            "coverImage": {"large": "https://example.com/fresh.jpg"},
        }
    ]

    with patch("media_cli.core.anilist._time.time", return_value=now), \
         patch("media_cli.services.anilist.fetch", return_value=mock_raw_media) as fetch_mock, \
         patch("media_cli.core.anilist._provider_catalog_refresh_fn", None):
        assert refresh_history_anilist_airing_batch(history_entries) is False
        fetch_mock.assert_not_called()
        assert refresh_history_anilist_airing_batch(history_entries, force=True) is True
        fetch_mock.assert_called_once()

    assert history_domain.history_refresh_last_checked_at(history_entries[0]["show"]) == now
