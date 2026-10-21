"""Never substitute another title's or provider's playable episode identifiers."""

import pytest

from media_cli.domain import matching
from media_cli.services import catalog


def test_matching_cache_does_not_confuse_reused_object_ids(monkeypatch):
    matching.reset_title_cache()
    monkeypatch.setattr(matching, "id", lambda item: 7, raising=False)
    first = {"name": "Alpha Movie"}
    second = {"name": "Unrelated Series"}
    assert matching.match_score_details(first, second)["exact_title"] is False


def test_matching_cache_tracks_title_changes():
    matching.reset_title_cache()
    show = {"name": "Alpha"}
    other = {"name": "Alpha"}
    assert matching.match_score_details(show, other)["exact_title"]
    other["name"] = "Completely Different"
    assert not matching.match_score_details(show, other)["exact_title"]


def test_matching_cache_is_bounded():
    matching.reset_title_cache()
    for index in range(600):
        matching._cached_match_titles({"name": f"Title {index}"})
    assert len(matching._title_cache) == 512


@pytest.mark.parametrize("same_provider", [False, True])
def test_download_catalog_requires_provider_title_identity(monkeypatch, same_provider):
    show = {"_id": "movie", "_provider": "example", "name": "Alpha", "aniListId": "42"}
    saved = {"_id": "other", "_provider": "example" if same_provider else "other",
             "name": "Alpha", "aniListId": "42", "_episode_ids": ["1", "2"], "_episode_ids_ttype": "sub"}
    monkeypatch.setattr("media_cli.core.storage.load_downloads_db", lambda: {"shows": {"saved": {"metadata": saved}}})
    assert catalog._download_catalog_for_show(show, "sub")[0] == []


def test_already_mislabeled_saved_catalog_does_not_replace_live_catalog(monkeypatch):
    movie = "https://example.test/movie/"
    correct = "https://example.test/movie-full/"
    show = {"_id": movie, "_provider": "example", "name": "Movie"}
    saved = {**show, "_episode_ids": ["https://example.test/other-1/", "https://example.test/other-2/"],
             "_episode_ids_ttype": "sub"}
    monkeypatch.setattr("media_cli.core.storage.load_downloads_db", lambda: {"shows": {"saved": {"metadata": saved}}})
    catalog.update_available_count_from_episode_ids(show, "sub", [correct])
    assert catalog._use_download_catalog_if_better(show, "sub", [correct]) == [correct]
    assert show["_episode_ids"] == [correct]
