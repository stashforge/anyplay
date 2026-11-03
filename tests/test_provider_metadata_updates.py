"""Fresh provider metadata replaces stale values without blanking partial data."""
from copy import deepcopy

import pytest

from media_cli.domain.metadata import apply_provider_metadata_to_history_show
from media_cli.providers.animexin import AnimeXinProvider


@pytest.mark.parametrize("field,new_value", [
    ("name", "Updated title"), ("englishName", "New English"),
    ("romajiName", "New romaji"), ("nativeName", "New native"),
    ("description", "Corrected synopsis"), ("thumbnail", "https://example.test/new-cover.jpg"),
    ("banner", "https://example.test/new-banner.jpg"), ("format", "ONA"),
    ("genres", ["Adventure"]), ("altNames", ["New alias"]),
    ("airedEnd", {"year": 2027, "month": 1, "day": 1}),
])
def test_changed_provider_fields_replace_existing_values(field, new_value):
    show = {"_id": "one", "_provider": "example", field: "Old" if not isinstance(new_value, list) else ["Old"]}
    assert apply_provider_metadata_to_history_show(show, {field: new_value})
    assert show[field] == new_value
    assert not apply_provider_metadata_to_history_show(show, {field: deepcopy(new_value)})
    if isinstance(new_value, list):
        new_value.append("Provider mutated later")
        assert "Provider mutated later" not in show[field]


def test_sparse_refresh_preserves_saved_metadata_and_identity():
    show = dict(_id="one", _provider="example", name="Saved", description="Synopsis",
                thumbnail="cover", genres=["Drama"], altNames=["Alias"], aniListId="1", malId=2)
    original = deepcopy(show)
    apply_provider_metadata_to_history_show(show, dict(name="", description=None, thumbnail="",
                                                      genres=[], altNames=[], aniListId="99", malId=99))
    assert show == original


def test_wordpress_uses_actual_page_title_instead_of_url_slug():
    provider = AnimeXinProvider(fetch=lambda url: '<h1 class="entry-title" itemprop="name">Corrected Official Title</h1>')
    result = provider.get_title("https://animexin.dev/short-slug/")
    show = dict(name="Old title", description="Old synopsis")
    apply_provider_metadata_to_history_show(show, result)
    assert show["name"] == "Corrected Official Title"


def test_wordpress_missing_page_heading_cannot_overwrite_saved_title():
    provider = AnimeXinProvider(fetch=lambda url: '<div>Partial response without title</div>')
    result = provider.get_title("https://animexin.dev/short-slug/")
    show = dict(name="Saved actual title")
    apply_provider_metadata_to_history_show(show, result)
    assert show["name"] == "Saved actual title"


@pytest.mark.parametrize("patch_method", ["show", "state"])
def test_refreshed_fields_survive_history_reload(patch_method, monkeypatch):
    from media_cli.core import storage

    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    before = dict(_id="one", _provider="example", name="Old", description="Old",
                  romajiName="Old", genres=["Old"], altNames=["Old"])
    storage.save_refreshed_history([dict(show=before, episode="1", translation_type="sub")])
    updated = deepcopy(before)
    fresh = dict(name="New", description="New", romajiName="New", genres=["New"], altNames=["New"],
                 airedEnd={"year": 2027})
    apply_provider_metadata_to_history_show(updated, fresh)
    if patch_method == "show":
        storage.patch_history_entry_show("one", "sub", updated)
    else:
        storage.patch_history_entry_state(dict(show=updated, translation_type="sub"))
    storage._history_cache = None
    actual = storage.load_history()[0]["show"]
    assert all(actual.get(k) == v for k, v in fresh.items())


def test_saving_refreshed_provider_status_does_not_restore_finished_tracker_status(monkeypatch):
    from media_cli.core import storage

    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    show = dict(_id="one", _provider="animexin", type="ONA", status="RELEASING",
                _anilist_status="FINISHED", episodeCount=219, availableEpisodes={"sub": 216})
    storage.save_refreshed_history([dict(show=show, episode="216", progress=216, translation_type="sub")])
    storage._history_cache = None
    saved = storage.load_history()[0]["show"]
    assert saved["_anilist_status"] == "FINISHED"
    assert saved["status"] == "RELEASING"
