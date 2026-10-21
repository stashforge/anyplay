import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.core import enrichment
from media_cli.domain import history
from media_cli.ui.modals import _show_needs_enrichment


def unexpected_fetch(*args, **kwargs):
    raise AssertionError("Already loaded data must not be fetched again")


def test_public_anilist_search_requests_identity_aliases_and_airing_fallback_data(monkeypatch):
    from media_cli.core.anilist_fallback import search_anilist_public

    requests = []

    def request(req, **kwargs):
        requests.append(json.loads(req.data))
        return nullcontext(Mock(read=lambda: b'{"data":{"Page":{"media":[]}}}'))

    monkeypatch.setattr("urllib.request.urlopen", request)
    assert search_anilist_public("example") == {"data": {"Page": {"media": []}}}
    assert len(requests) == 1
    assert "synonyms" in requests[0]["query"]
    assert "nextAiringEpisode" in requests[0]["query"]


def test_ongoing_catalog_fallback_reuses_cached_airing_data(monkeypatch):
    from media_cli.providers.anikoto import AnikotoProvider

    provider = AnikotoProvider()
    provider._remember_media(
        "150672",
        {
            "id": 150672,
            "episodes": None,
            "nextAiringEpisode": {"episode": 6},
        },
    )
    request = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", request)
    monkeypatch.setattr(provider, "_get_anime_page", lambda *args, **kwargs: (None, None))
    assert provider.episode_catalog("150672")["ids"] == ["1", "2", "3", "4", "5"]
    request.assert_not_called()


@pytest.mark.parametrize("episode_ids", [("1", "2", "3"), ()])
def test_resume_uses_supplied_catalog_even_when_empty(episode_ids):
    entry = {"show": {"_id": "one"}, "episode": "1"}
    result = history.playback_ep_from_history_entry(
        entry,
        "sub",
        episode_ids=episode_ids,
        ensure_episode_ids_fn=unexpected_fetch,
        get_resume_time_fn=lambda *args: 0,
    )
    assert result == ("2" if episode_ids else 2)


def test_resume_uses_current_catalog_labels_without_changing_history():
    entry = {"show": {"_id": "one"}, "episode": "2"}
    before = deepcopy(entry)
    result = history.playback_ep_from_history_entry(
        entry,
        "sub",
        episode_ids=("first", "second", "third"),
        episode_labels={"first": "1", "second": "2", "third": "3"},
        ensure_episode_ids_fn=unexpected_fetch,
        get_resume_time_fn=lambda *args: 0,
    )
    assert result == "third"
    assert entry == before


def test_resume_without_supplied_catalog_still_fetches():
    entry = {"show": {"_id": "one"}, "episode": "1"}
    fetched = []

    def fetch(show, translation):
        fetched.append((show["_id"], translation))
        return ["1", "2"]

    assert (
        history.playback_ep_from_history_entry(
            entry,
            "sub",
            ensure_episode_ids_fn=fetch,
            get_resume_time_fn=lambda *args: 0,
        )
        == "2"
    )
    assert fetched == [("one", "sub")]


@pytest.mark.parametrize("description,genres", [(None, []), ("", []), ("Synopsis", ["Drama"])])
@pytest.mark.parametrize("loaded", [("description", "genres"), ["description", "genres"]])
def test_loaded_search_metadata_is_not_enriched_again(monkeypatch, description, genres, loaded):
    show = {
        "_id": "one",
        "aniListId": "1",
        "description": description,
        "genres": genres,
        "_metadata_fields_loaded": loaded,
    }
    provider = Mock(side_effect=unexpected_fetch)
    fetch = Mock(side_effect=unexpected_fetch)
    search = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr("media_cli.providers.get_provider", provider)
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", fetch)
    monkeypatch.setattr("media_cli.core.anilist.search_anilist", search)
    assert _show_needs_enrichment(show) is False
    assert enrichment.enrich_show_if_missing(show) is False
    provider.assert_not_called()
    fetch.assert_not_called()
    search.assert_not_called()


def test_loaded_search_metadata_does_not_schedule_background_refetch():
    show = {
        "_id": "one",
        "aniListId": "1",
        "description": None,
        "genres": [],
        "_metadata_fields_loaded": ("description", "genres"),
    }
    results, unmatched = enrichment.enrich_provider_results_fast([show])
    assert results == [show]
    assert unmatched == []


def test_partially_loaded_metadata_can_still_be_enriched():
    show = {"_id": "one", "aniListId": "1", "_metadata_fields_loaded": ("description",)}
    assert _show_needs_enrichment(show) is True
    _, unmatched = enrichment.enrich_provider_results_fast([show])
    assert unmatched == [show]


def test_provider_details_with_known_empty_fields_do_not_trigger_anilist_refetch(monkeypatch):
    show = {"_id": "one", "_provider": "anikoto", "aniListId": "1"}
    details = {
        "description": None,
        "genres": [],
        "aniListId": "1",
        "_metadata_fields_loaded": ("description", "genres"),
    }
    monkeypatch.setattr("media_cli.providers.get_provider", lambda *args: SimpleNamespace(get_title=lambda _: details))
    fetch = Mock(side_effect=unexpected_fetch)
    search = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", fetch)
    monkeypatch.setattr("media_cli.core.anilist.search_anilist", search)
    enrichment.enrich_show_if_missing(show)
    assert show["_title_enriched"] is True
    fetch.assert_not_called()
    search.assert_not_called()


def test_sparse_normal_provider_gets_details_once_without_duplicate_anilist_lookup(monkeypatch):
    show = {"_id": "one", "_provider": "animexin"}
    details = Mock(return_value={"description": "Synopsis", "genres": ["Drama"], "aniListId": "1"})
    lookup = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr("media_cli.providers.get_provider", lambda *args: SimpleNamespace(get_title=details))
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", lookup)
    assert enrichment.enrich_show_if_missing(show) is True
    assert enrichment.enrich_show_if_missing(show) is False
    details.assert_called_once_with("one")
    lookup.assert_not_called()


def test_partial_loaded_fields_expand_when_provider_returns_empty_details(monkeypatch):
    show = {
        "_id": "one",
        "_provider": "anikoto",
        "aniListId": "1",
        "_metadata_fields_loaded": ("description",),
        "description": None,
    }
    details = {
        "description": None,
        "genres": [],
        "aniListId": "1",
        "_metadata_fields_loaded": ("description", "genres"),
    }
    lookup = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr("media_cli.providers.get_provider", lambda *args: SimpleNamespace(get_title=lambda _: details))
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", lookup)
    enrichment.enrich_show_if_missing(show)
    assert show["_metadata_fields_loaded"] == ("description", "genres")
    assert _show_needs_enrichment(show) is False
    lookup.assert_not_called()


def test_sparse_anikoto_enrichment_uses_one_metadata_request(monkeypatch):
    from media_cli.providers.anikoto import AnikotoProvider

    show = {"_id": "150672", "_provider": "anikoto"}
    provider = AnikotoProvider()
    request = Mock(
        return_value={
            "id": 150672,
            "idMal": 52034,
            "title": {"romaji": "Example"},
            "description": None,
            "genres": [],
        }
    )
    lookup = Mock(side_effect=unexpected_fetch)
    search = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr("media_cli.providers.get_provider", lambda *args: provider)
    monkeypatch.setattr("media_cli.services.anilist.fetch_one", request)
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", lookup)
    monkeypatch.setattr("media_cli.core.anilist.search_anilist", search)
    enrichment.enrich_show_if_missing(show)
    assert show.get("aniListId") == "150672"
    assert _show_needs_enrichment(show) is False
    assert request.call_count == 1
    lookup.assert_not_called()
    search.assert_not_called()


def test_loaded_metadata_still_merges_available_tracking_progress(monkeypatch):
    show = {"_id": "one", "aniListId": "1", "description": "Synopsis", "genres": ["Drama"]}
    tracking = {"_id": "1", "_anilist_progress": 4, "_anilist_list": {"status": "CURRENT"}}
    monkeypatch.setattr(enrichment, "save_source_anilist_matches", lambda *args: None)
    _, unmatched = enrichment.enrich_provider_results_fast([show], [tracking])
    assert show["_anilist_progress"] == 4
    assert unmatched == []


def test_background_enrichment_also_skips_already_loaded_metadata(monkeypatch):
    fetch = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", fetch)
    enrichment.enrich_provider_results_background(
        [
            {
                "_id": "one",
                "aniListId": "1",
                "description": "",
                "genres": [],
                "_metadata_fields_loaded": ("description", "genres"),
            }
        ]
    )
    fetch.assert_not_called()


@pytest.mark.parametrize("shows", [None, []])
def test_background_enrichment_accepts_no_results(monkeypatch, shows):
    fetch = Mock(side_effect=unexpected_fetch)
    monkeypatch.setattr(enrichment, "fetch_anilist_by_ids", fetch)
    enrichment.enrich_provider_results_background(shows)
    fetch.assert_not_called()


@pytest.mark.parametrize("openings", [1, 2])
@pytest.mark.parametrize("requested_episode", [None, "1", "404"])
@pytest.mark.parametrize("identity_field", ["_id", "id"])
@pytest.mark.parametrize("available", [True, False])
def test_search_selection_fetches_catalog_once_with_separate_history_show(monkeypatch, openings, requested_episode, identity_field, available):
    from media_cli import app_core
    from media_cli.app import search
    from media_cli.context import CliFlags, MachineState, UiState

    show = {
        "_id": "150672",
        "_provider": "anikoto",
        "name": "Example",
        "aniListId": "150672",
        "description": "Synopsis",
        "genres": ["Drama"],
    }
    entry = {"show": {"_id": "150672", "_provider": "anikoto"}, "episode": "1"}
    if identity_field == "id":
        show["id"] = show.pop("_id")
    fetches = []

    def ensure(target, translation, **kwargs):
        if target.get("_episode_ids_ttype") != translation:
            fetches.append(target.get("_id") or target.get("id"))
            target.update(_episode_ids=["1", "2"] if available else [], _episode_ids_ttype=translation,
                          _episode_catalog_state="loaded" if available else "unavailable")
        return target["_episode_ids"]

    def pick(*args, **kwargs):
        from media_cli.app.search_results_controller import SearchResultsResult

        assert kwargs["select"](show)
        return SearchResultsResult("selected", show)

    monkeypatch.setattr(search, "run_search_results", pick)
    monkeypatch.setattr(
        app_core,
        "_cached_search_results",
        lambda *args: (
            None,
            lambda: [show],
            lambda: "Loading",
            lambda: "",
        ),
    )
    monkeypatch.setattr(app_core, "ensure_episode_ids", ensure)
    monkeypatch.setattr("media_cli.services.catalog.ensure_episode_ids", ensure)
    monkeypatch.setattr(app_core, "get_history_entry", lambda *args: entry)
    monkeypatch.setattr("media_cli.core.storage.get_resume_time", lambda *args: 0)
    monkeypatch.setattr(app_core, "prepare_show_display_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_core, "batch_prepare_shows_display_state", lambda *args: None)
    monkeypatch.setattr(app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(app_core, "with_loading", lambda message, fn, *args: fn(*args))
    monkeypatch.setattr(app_core, "_clear_streams", lambda: None)
    ms = MachineState(query_str="example")
    for _ in range(openings):
        result = search.handle_search_state(
            CliFlags(plain_mode=True, incognito_mode=True),
            UiState(),
            ms,
            {},
            SimpleNamespace(provider="anikoto", episode=requested_episode, download=False),
            "sub",
            lambda *args: False,
        )
        assert result == ("DETAILS" if not available or requested_episode is None else "PLAY" if requested_episode == "1" else "EPISODE")
        if available:
            assert ms.current_ep == ("2" if requested_episode is None else "1")
        assert ms.show_id == "150672"
    assert fetches == ["150672"]


def test_explicit_history_refresh_invalidates_provider_cache_before_fetch(monkeypatch):
    events = []
    provider = SimpleNamespace(
        invalidate_title_cache=lambda title: events.append(("invalidate", title)),
        get_title=lambda title: events.append(("metadata", title)) or {},
        episode_catalog=lambda title, **kwargs: (
            events.append(("catalog", title))
            or {
                "state": "loaded",
                "ids": ["1"],
            }
        ),
    )
    entry = {
        "show": {
            "_id": "one",
            "_provider": "anikoto",
            "description": "Synopsis",
            "genres": ["Drama"],
            "aniListId": "1",
        },
        "translation_type": "sub",
    }
    monkeypatch.setattr("media_cli.services.catalog._provider_for_title", lambda show: provider)
    history.refresh_history_entry_provider_catalog(entry)
    assert events == [("invalidate", "one"), ("metadata", "one"), ("catalog", "one")]


def test_history_metadata_merge_retains_known_empty_field_state():
    from media_cli.domain.metadata import apply_provider_metadata_to_history_show

    show = {"_id": "one", "aniListId": "1", "_metadata_fields_loaded": ("description",)}
    assert (
        apply_provider_metadata_to_history_show(
            show,
            {
                "description": None,
                "genres": [],
                "_metadata_fields_loaded": ("description", "genres"),
            },
        )
        is True
    )
    assert show["_metadata_fields_loaded"] == ("description", "genres")
    assert _show_needs_enrichment(show) is False


@pytest.mark.parametrize("patch_method", ["show", "state"])
def test_patching_existing_history_retains_loaded_metadata_fields(patch_method):
    from media_cli.core import storage

    storage.save_refreshed_history(
        [{"show": {"_id": "one", "_provider": "anikoto"}, "translation_type": "sub", "episode": "0"}]
    )
    updated = {
        "_id": "one",
        "_provider": "anikoto",
        "aniListId": "1",
        "_metadata_fields_loaded": ("description", "genres"),
    }
    if patch_method == "show":
        assert storage.patch_history_entry_show("one", "sub", updated) is True
    else:
        assert storage.patch_history_entry_state({"show": updated, "translation_type": "sub"}) is True
    storage.reset_caches()
    saved = storage.load_history()[0]["show"]
    assert saved["_metadata_fields_loaded"] == ["description", "genres"]
    assert _show_needs_enrichment(saved) is False
