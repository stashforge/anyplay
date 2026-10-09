"""Short loading messages with a little personality."""

from __future__ import annotations


def episode_indexing(count: int | None = None) -> str:
    try:
        total = int(count or 0)
    except (TypeError, ValueError):
        total = 0
    if total > 0:
        return f"Indexing {total} episodes…"
    return "Indexing episodes…"


def local_file_scan() -> str:
    return "Checking local files…"
