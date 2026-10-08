"""Torrent search backend primitives for AnyPlay."""

from .formatting import build_display_label
from .fallback import augment_episode_data_with_torrents, build_episode_torrent_queries
from .models import TorrentResult, TorrentSearchConfig
from .search import (
    merge_results,
    parse_nyaa_rss,
    parse_torrents_csv_rows,
    parse_tsukihime_detail,
    parse_tsukihime_rows,
    search_anime_torrents,
    search_general_torrents,
)
from .sources import build_torrent_source

__all__ = [
    "TorrentResult",
    "TorrentSearchConfig",
    "augment_episode_data_with_torrents",
    "build_display_label",
    "build_episode_torrent_queries",
    "build_torrent_source",
    "merge_results",
    "parse_nyaa_rss",
    "parse_torrents_csv_rows",
    "parse_tsukihime_detail",
    "parse_tsukihime_rows",
    "search_anime_torrents",
    "search_general_torrents",
]
