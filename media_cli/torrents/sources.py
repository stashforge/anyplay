"""Adapters from torrent results to provider source dictionaries."""

from __future__ import annotations

from .formatting import build_display_label, quality_from_name
from .models import TorrentResult


def build_torrent_source(result: TorrentResult) -> dict:
    """Return a TUI/resolver-friendly source entry for a torrent result."""
    return {
        "sourceName": build_display_label(result),
        "sourceUrl": result.magnet,
        "type": "torrent",
        "resolution": quality_from_name(result.name) or "Unknown",
        "_source_kind": "torrent",
        "torrent": {
            "source": result.source,
            "name": result.name,
            "infohash": result.infohash,
            "size_bytes": result.size_bytes,
            "seeders": result.seeders,
            "leechers": result.leechers,
            "completed": result.completed,
            "file_count": result.file_count,
            "group": result.group,
            "episode": result.episode,
            "anime_title": result.anime_title,
            "anime_english_title": result.anime_english_title,
            "anilist_id": result.anilist_id,
            "mal_id": result.mal_id,
            "audio_langs": list(result.audio_langs),
            "sub_langs": list(result.sub_langs),
            "tracker_urls": list(result.tracker_urls),
            "tsukihime_id": result.tsukihime_id,
            "nyaa_id": result.nyaa_id,
            "nekobt_id": result.nekobt_id,
            "tt_id": result.tt_id,
        },
    }
