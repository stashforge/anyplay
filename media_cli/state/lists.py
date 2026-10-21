"""Persistence helpers for cached JSON lists."""

import json
import os


def load_json_list(path, cached=None):
    if cached is not None:
        return cached
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, list) else []


def delete_history_entry(entries, show_id, translation_type, *, provider_id=None):
    from ..domain.titles import title_provider_key

    provider = title_provider_key({"_provider": provider_id}) if provider_id is not None else None
    filtered = [
        entry
        for entry in entries
        if not (
            str(entry.get("show", {}).get("_id") or entry.get("show", {}).get("id")) == str(show_id)
            and entry.get("translation_type", "sub") == translation_type
            and (provider is None or title_provider_key(entry.get("show")) == provider)
        )
    ]
    return filtered, len(filtered) != len(entries)


def update_search_history(entries, query, limit):
    query = str(query or "").strip()
    if not query:
        return list(entries)
    updated = [entry for entry in entries if entry != query]
    updated.insert(0, query)
    return updated[:limit]
