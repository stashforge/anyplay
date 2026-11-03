"""Metadata refreshes must stay inside their provider namespace."""

from copy import deepcopy

import pytest

from media_cli.core import storage


@pytest.fixture
def history_store(monkeypatch, tmp_path):
    monkeypatch.setattr(storage.paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage, "_history_cache", None)
    monkeypatch.setattr(storage, "is_incognito", lambda: False)


@pytest.mark.parametrize("translation", ["sub", None])
@pytest.mark.parametrize("provider_field", ["_provider", "provider", None])
def test_metadata_patch_preserves_other_providers_and_translations(
    history_store, translation, provider_field
):
    provider = "selected" if provider_field else "allanime"
    entries = [
        dict(show=dict(_id="shared", _provider=owner, name="Original"),
             episode="3", translation_type=language)
        for owner, language in (
            ("other", "sub"), ("other", "dub"), (provider, "sub"), (provider, "dub")
        )
    ]
    assert storage.save_refreshed_history(entries)
    original = deepcopy(storage.load_history())
    updated = dict(_id="shared", name="Refreshed", description="Updated synopsis")
    if provider_field:
        updated[provider_field] = provider

    assert storage.patch_history_entry_show("shared", translation, updated)
    storage._history_cache = None
    remaining = storage.load_history()
    for before, after in zip(original, remaining):
        if (before["show"]["_provider"] == provider
                and (translation is None or before["translation_type"] == translation)):
            assert after["show"]["name"] == "Refreshed"
            assert after["show"]["description"] == "Updated synopsis"
            assert after["episode"] == before["episode"]
        else:
            assert after == before


def test_metadata_patch_does_not_modify_another_provider_when_target_is_absent(history_store):
    entries = [dict(show=dict(_id="shared", _provider="other", name="Original"),
                    episode="3", translation_type="sub")]
    assert storage.save_refreshed_history(entries)
    before = deepcopy(storage.load_history())

    assert storage.patch_history_entry_show(
        "shared", "sub", dict(_id="shared", _provider="missing", name="Refreshed")
    ) is False
    storage._history_cache = None
    assert storage.load_history() == before
