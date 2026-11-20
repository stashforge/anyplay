"""Free-form torrent provider backed by torrent search APIs."""

from __future__ import annotations

from typing import Callable

from ..torrents import build_torrent_source
from ..torrents.formatting import human_size
from ..torrents.models import TorrentResult, torrent_search_config_from_app_config
from .shared.models import normalize_episode_catalog, normalize_episode_sources, normalize_titles


TorrentSearchFn = Callable[[str], list[TorrentResult]]


class TorrentProvider:
    id = "torrent"
    audio_mode = "embedded_multi_audio"

    def __init__(self, request_json_fn=None, *, search_fn: TorrentSearchFn | None = None):
        del request_json_fn
        self._search_fn = search_fn
        self._result_cache: dict[str, TorrentResult] = {}
        self._file_cache = {}
        self._query_cache = {}
        if not hasattr(self, "metadata"):
            self.metadata = {}
        if not hasattr(self, "domains"):
            self.domains = []

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Torrents")

    def _remember(self, result: TorrentResult) -> None:
        key = str(result.infohash or "").casefold()
        if key:
            self._result_cache[key] = result

    def _get_result(self, provider_id: str) -> TorrentResult:
        key = str(provider_id or "").casefold()
        cached = self._result_cache.get(key)
        if cached is not None:
            return cached
        return TorrentResult(
            source="magnet",
            name=key or "Torrent",
            infohash=key,
        )

    def _description(self, result: TorrentResult) -> str:
        parts = []
        if result.source:
            parts.append(f"Source: {result.source}")
        if result.group:
            parts.append(f"Group: {result.group}")
        if result.seeders is not None:
            parts.append(f"Seeders: {result.seeders}")
        else:
            parts.append("Seeders: Unknown")
        if result.leechers is not None:
            parts.append(f"Leechers: {result.leechers}")
        if result.completed is not None:
            parts.append(f"Completed: {result.completed}")
        if result.size_bytes:
            parts.append(f"Size: {human_size(result.size_bytes)}")
        if result.file_count:
            parts.append(f"Files: {result.file_count}")
        if result.audio_langs:
            parts.append("Audio: " + ", ".join(result.audio_langs))
        if result.sub_langs:
            parts.append("Subs: " + ", ".join(result.sub_langs))
        return " | ".join(parts)

    def _summary(self, result: TorrentResult) -> str:
        parts = []
        if result.group:
            parts.append(result.group)
        if result.seeders is None:
            parts.append("Unknown Seeders")
        else:
            parts.append(f"{result.seeders} Seeders")
        if result.leechers is not None:
            parts.append(f"{result.leechers} Leechers")
        if result.completed is not None:
            parts.append(f"{result.completed} Completed")
        if result.size_bytes:
            parts.append(human_size(result.size_bytes))
        if result.audio_langs:
            parts.append("Audio " + ",".join(result.audio_langs[:3]))
        if result.sub_langs:
            parts.append("Subs " + ",".join(result.sub_langs[:3]))
        if result.source:
            parts.append(result.source)
        return " | ".join(parts)

    def _title_from_result(self, result: TorrentResult, query=None) -> dict:
        self._remember(result)
        if query is not None:
            self._query_cache[result.infohash] = query
        from ..torrents.ranking import assess
        assessment = assess(result.name, seeders=result.seeders, leechers=result.leechers,
                            trusted=result.trusted, query=self._query_cache.get(result.infohash, ''))
        summary = self._summary(result)
        return {
            "_id": result.infohash,
            "name": result.name,
            "englishName": result.anime_english_title or "",
            "romajiName": result.anime_title or "",
            "description": self._description(result),
            "_torrent_summary": summary,
            "type": "TORRENT",
            "format": "TORRENT",
            "status": "UNKNOWN",
            "episodeCount": 1,
            "availableEpisodes": {"sub": 1, "dub": 1, "raw": 1},
            "availableEpisodesDetail": {"sub": ["1"], "dub": ["1"], "raw": ["1"]},
            "score": None,
            "genres": ["Torrent"],
            "aniListId": result.anilist_id or None,
            "malId": result.mal_id or None,
            "_torrent": {
                "infohash": result.infohash,
                "source": result.source,
                "name": result.name,
                "size_bytes": result.size_bytes,
                "seeders": result.seeders,
                "leechers": result.leechers,
                "completed": result.completed,
                "group": result.group,
                "file_count": result.file_count,
                "audio_langs": list(result.audio_langs),
                "sub_langs": list(result.sub_langs),
                "tracker_urls": list(result.tracker_urls),
                "trusted": result.trusted,
                "ranking": assessment,
            },
        }

    def search(self, query: str, ttype: str = "sub", *, progress=None) -> list[dict]:
        del ttype
        try:
            from media_cli.core.storage import load_config
            config = torrent_search_config_from_app_config(load_config())
        except Exception:
            config = torrent_search_config_from_app_config({})
        outcomes = {}
        if self._search_fn is None:
            from ..torrents.public_search import search_public_torrents
            def publish(batch, sources):
                if progress:
                    records = [self._title_from_result(result, query) for result in batch]
                    for record in records:
                        record["_torrent_sources"] = sources
                    progress(normalize_titles(records, provider_id=self.id, provider_name=self.name, id_key="_id"))
            results, outcomes = search_public_torrents(query, config=config, progress=publish)
        else:
            try:
                results = self._search_fn(query, config=config)
            except TypeError:
                results = self._search_fn(query)
        titles = [self._title_from_result(result, query) for result in results]
        for title in titles:
            title["_torrent_sources"] = outcomes
        return normalize_titles(titles, provider_id=self.id, provider_name=self.name, id_key="_id")

    def get_title(self, provider_id: str) -> dict | None:
        result = self._result_cache.get(str(provider_id or "").casefold())
        if result is None:
            return None
        return normalize_titles(
            [self._title_from_result(result)],
            provider_id=self.id,
            provider_name=self.name,
            id_key="_id",
        )[0]

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict:
        key = str(provider_id).casefold()
        if key not in self._file_cache:
            from ..torrents.metadata import fetch_video_inventory
            self._file_cache[key] = fetch_video_inventory(self._get_result(provider_id).magnet)
        episodes = [{"id": str(file["index"] + 1), "label": file["path"].replace("\\", "/").rsplit("/", 1)[-1],
                     "title": file["path"], "size_bytes": file["size"]} for file in self._file_cache[key]]
        return normalize_episode_catalog(
            {
                "episodes": {
                    "sub": [dict(item, translationType="sub") for item in episodes],
                    "dub": [dict(item, translationType="dub") for item in episodes],
                    "raw": [dict(item, translationType="raw") for item in episodes],
                },
                "translation_type": ttype,
            },
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> dict | None:
        del ttype
        result = self._get_result(provider_id)
        source = build_torrent_source(result)
        key = str(provider_id).casefold()
        if key in self._file_cache:
            selected = next((file for file in self._file_cache[key] if str(file["index"] + 1) == str(episode)), None)
            if selected is None:
                raise ValueError("Selected video is not in this torrent")
            source["torrent"].update(file_index=selected["index"], file_path=selected["path"], file_size=selected["size"])
        elif str(episode) != "1":
            raise ValueError("Load torrent files before selecting a video")
        return normalize_episode_sources(
            {"episode": {"sourceUrls": [source]}},
            provider_id=self.id,
            provider_title_id=provider_id,
            episode=str(episode),
        )

    def browser_url(self, *, provider_id: str = "", episode: str = "") -> str:
        result = self._get_result(provider_id)
        if result.nyaa_id:
            return f"https://nyaa.si/view/{result.nyaa_id}"
        if result.tsukihime_id:
            return f"https://tsukihime.org/view/{result.tsukihime_id}"
        return result.magnet if result.infohash else "https://torrents-csv.com/"


PROVIDER_CLASS = TorrentProvider
