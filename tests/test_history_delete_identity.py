"""History deletion respects provider namespaces and translations."""

from copy import deepcopy

from media_cli.core import storage


def test_storage_deletes_only_the_selected_provider_translation(monkeypatch, tmp_path):
    entries = [dict(show=dict(_id="shared", _provider=provider, name="Example"),
                    episode="1", translation_type=translation)
               for provider, translation in (("first", "sub"), ("second", "sub"), ("second", "dub"))]
    monkeypatch.setattr(storage.paths, "HISTORY_PATH", str(tmp_path / "history.json"))
    monkeypatch.setattr(storage, "_history_cache", deepcopy(entries))
    monkeypatch.setattr(storage, "is_incognito", lambda: False)
    assert storage.delete_history_entry("shared", "sub", provider_id="second") is True
    storage._history_cache = None
    remaining = storage.load_history()
    assert [(entry["show"]["_provider"], entry["translation_type"]) for entry in remaining] == [
        ("first", "sub"), ("second", "dub"),
    ]
    assert storage.delete_history_entry("shared", "sub", provider_id="second") is False
