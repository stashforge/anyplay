import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from allmanga_cli.ui.modals import _show_needs_enrichment
from allmanga_cli.core import storage
from allmanga_cli.domain.metadata import apply_provider_metadata_to_history_show
from allmanga_cli.core.anilist import (
    get_show_anilist_id,
    refresh_history_anilist_airing_batch,
)
from allmanga_cli.state import paths


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

    with patch("allmanga_cli.services.anilist.fetch", return_value=mock_raw_media), \
         patch("allmanga_cli.core.anilist._provider_catalog_refresh_fn", None):
        changed = refresh_history_anilist_airing_batch(history_entries)
        assert changed is True

    s = history_entries[0]["show"]
    assert s["description"] == "Rimuru returns for season 4."
    assert s["genres"] == ["Action", "Adventure", "Comedy", "Fantasy"]
    assert s["aniListId"] == "182205"
    assert s["_title_enriched"] is True
    assert s["_next_airing_ep"] == 2
