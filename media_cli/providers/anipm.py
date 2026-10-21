"""AniPM title catalogue and short-lived streaming sessions."""

from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote, urlencode, urljoin

from ..services.http import UA, request_json
from .shared.anipm_sources import request_html, resolve
from .shared.models import normalize_episode_sources
from .shared.schema import build_catalog, build_episode, build_title

_logger = logging.getLogger(__name__)
_BASE_URL = "https://ani.pm"


def _positive_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    text = str(value or "").strip()
    return str(int(text)) if text.isascii() and text.isdecimal() and int(text) > 0 else None


def _title_id(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("mal-"):
        mal_id = _positive_id(value[4:])
        return f"mal-{mal_id}" if mal_id else None
    return _positive_id(value)


def _episode_id(value: Any) -> str | None:
    text = str(value if value is not None else "").strip()
    if not text or text in (".", "..") or isinstance(value, bool):
        return None
    if any(char in text for char in "/\\?#") or any(ord(char) < 32 for char in text):
        return None
    return text


def _episode_rows(records: list) -> dict[str, list[dict[str, Any]]]:
    episodes: dict[str, list[dict[str, Any]]] = {"sub": [], "dub": [], "raw": []}
    for record in records:
        if not isinstance(record, dict):
            continue
        number = record.get("number")
        if isinstance(number, bool) or not isinstance(number, (str, int, float)):
            continue
        episode_id = str(number).strip()
        if not episode_id:
            continue
        available = record.get("available")
        if not isinstance(available, dict):
            continue
        embed = record.get("embed")
        embed = embed if isinstance(embed, dict) else {}
        for ttype in ("sub", "dub"):
            if available.get(ttype) is not True:
                continue
            episodes[ttype].append(build_episode(
                episode_id=episode_id,
                label=episode_id,
                title=record.get("title"),
                url=embed.get(ttype),
                translation_type=ttype,
                duration=record.get("runtimeSeconds"),
                date=record.get("aired"),
                extra={"thumbnail": record.get("thumbnail") or ""},
            ))
    return episodes


def _catalog(provider_id: str, ttype: str, records: list | None) -> dict[str, Any]:
    episodes = _episode_rows(records) if records is not None else {}
    catalog = build_catalog(
        provider="anipm", provider_id=provider_id, translation_type=ttype,
        episodes=episodes,
        state="unavailable" if records is None else "loaded",
        error="AniPM episode list could not be loaded" if records is None else "",
    )
    # The shared index keeps navigation fields, not provider runtime/artwork.
    for language, rows in episodes.items():
        seen = set()
        for episode in rows:
            if episode["id"] in seen:
                continue
            seen.add(episode["id"])
            target = catalog["episodes_map_by_ttype"][language][episode["id"]]
            target.update({key: episode[key] for key in ("duration", "date", "thumbnail")})
    # A missing requested language must not silently select another catalogue.
    primary = catalog["episodes_map_by_ttype"].get(ttype, {})
    ids = list(primary)
    catalog.update(
        episodes_map=primary, by_number=catalog["by_number_by_ttype"].get(ttype, {}),
        ids=ids, _provider_episode_ids=ids,
        labels={eid: episode["label"] for eid, episode in primary.items()},
    )
    if records is not None and not ids:
        catalog["state"] = "empty"
    return catalog


def _title(record: Any) -> dict[str, Any] | None:
    if not isinstance(record, dict):
        return None
    name = record.get("title")
    anilist_id = _positive_id(record.get("anilistId"))
    mal_id = _positive_id(record.get("malId"))
    if not isinstance(name, str) or not name.strip() or not (anilist_id or mal_id):
        return None
    episodes = record.get("episodes")
    episodes = episodes if isinstance(episodes, dict) else {}
    native = record.get("native") or record.get("nativeTitle")
    aliases = record.get("providerTitles")
    aliases = [value.strip() for value in aliases if isinstance(value, str) and value.strip()] if isinstance(aliases, list) else []
    excluded = {name.strip().casefold(), str(native or "").strip().casefold()}
    preferred_alias = next((value for value in aliases if value.casefold() not in excluded), "")
    return build_title(
        provider="anipm",
        provider_name="AniPM",
        provider_id=anilist_id or f"mal-{mal_id}",
        name=name,
        native_name=native,
        romaji_name=record.get("romaji"),
        alt_names=aliases,
        thumbnail=urljoin(f"{_BASE_URL}/", record["poster"]) if record.get("poster") else "",
        banner=urljoin(f"{_BASE_URL}/", record["banner"]) if record.get("banner") else "",
        description=record.get("synopsis"),
        media_type=record.get("type") or record.get("format"),
        media_format=record.get("type") or record.get("format"),
        status=record.get("status"),
        season={"name": record.get("season"), "year": record.get("year")},
        episode_count=record.get("episodeCount", episodes.get("total")),
        available_sub=record.get("subCount", episodes.get("sub", 0)),
        available_dub=record.get("dubCount", episodes.get("dub", 0)),
        score=record.get("score"),
        genres=record.get("genres"),
        anilist_id=anilist_id,
        mal_id=mal_id,
        extra={
            "preferredAltName": preferred_alias,
            "_metadata_fields_loaded": [target for source, target in
                                        (("synopsis", "description"), ("genres", "genres")) if source in record],
            **{
            key: record[key]
            for key in ("studios", "rating", "durationMinutes", "adult", "url")
            if key in record
            },
        },
    )


class AniPMProvider:
    id = "anipm"
    name = "AniPM"

    def __init__(self, request_json_fn=request_json, *, request_html_fn=request_html, clock_fn=time.time):
        self._request_json = request_json_fn
        self._request_html = request_html_fn
        self._clock = clock_fn

    def _request(self, path: str, *, api: str = "partner/v1"):
        try:
            return self._request_json(
                f"{_BASE_URL}/api/{api}/{path}",
                extra_hdrs={
                    "User-Agent": UA,
                    "Origin": _BASE_URL,
                    "Referer": f"{_BASE_URL}/",
                    "Accept": "application/json",
                },
            )
        except Exception as exc:
            _logger.warning("AniPM catalogue request failed (%s)", type(exc).__name__)
            return None

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        params = urlencode({"q": query, "page": 1, "limit": 20})
        payload = self._request(f"catalog?{params}", api="anime")
        records = payload.get("items", payload.get("data")) if isinstance(payload, dict) else None
        if not isinstance(records, list):
            return []
        return [title for record in records if (title := _title(record)) is not None]

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        identity = _title_id(provider_id)
        if not identity:
            return None
        if identity.startswith("mal-"):
            payload = self._request(f"series/{identity}")
        else:
            payload = self._request(f"ani/{identity}?routes=e4", api="anime")
        record = payload.get("data", payload) if isinstance(payload, dict) else None
        # Only follow an explicit provider mapping that retains the tracker identity.
        mapped_id = _positive_id(record.get("settlarId")) if isinstance(record, dict) else None
        if mapped_id and not identity.startswith("mal-"):
            mapped = self._request(f"series/{mapped_id}?routes=e4", api="anime")
            mapped = mapped.get("data", mapped) if isinstance(mapped, dict) else None
            if isinstance(mapped, dict) and _positive_id(mapped.get("anilistId")) == identity and _title(mapped):
                record = mapped
        title = _title(record)
        if title is not None and title["_provider_id"] != identity:
            return None
        if title is not None and isinstance(record.get("episodeList"), list):
            catalog = _catalog(identity, "sub", record["episodeList"])
            title["availableEpisodesDetail"] = catalog["detail"]
            title["availableEpisodes"] = {key: len(ids) for key, ids in catalog["detail"].items()}
        return title

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        identity = _title_id(provider_id)
        ttype = str(ttype or "sub").strip().lower()
        if not identity or ttype not in ("sub", "dub", "raw"):
            return _catalog(identity or "", ttype, None)
        payload = self._request(f"titles/{identity}/episodes")
        records = payload.get("data") if isinstance(payload, dict) else None
        return _catalog(identity, ttype, records if isinstance(records, list) else None)

    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> dict[str, Any] | None:
        identity = _title_id(provider_id)
        episode = _episode_id(episode)
        language = str(ttype or "sub").strip().lower()
        if not identity or not episode or language not in ("sub", "dub"):
            return None
        try:
            payload = resolve(identity, episode, language, fetch_html=self._request_html,
                              fetch_json=self._request_json, clock=self._clock)
        except Exception as exc:
            _logger.warning("AniPM stream resolution failed (%s)", type(exc).__name__)
            return None
        if payload:
            payload = self._episode_mirrors(identity, episode, language, payload)
        return normalize_episode_sources(payload, provider_id=self.id,
                                         provider_title_id=identity, episode=episode)

    def _episode_mirrors(self, identity: str, episode: str, language: str, payload: dict) -> dict:
        from .shared.anipm_mirrors import collect_mirrors

        response = self._request(f"titles/{identity}/episodes")
        records = response.get("data") if isinstance(response, dict) else None
        rows = records if isinstance(records, list) else []
        record = next((row for row in rows if isinstance(row, dict) and _episode_id(row.get("number")) == episode), {})
        availability = record.get("available")
        availability = availability if isinstance(availability, dict) else {}

        def channel_source(channel):
            return resolve(identity, episode, "dub" if channel.startswith("dub") else "sub",
                           hardsub=channel.endswith("hard"), fetch_html=self._request_html,
                           fetch_json=self._request_json, clock=self._clock)

        return collect_mirrors(payload, language, availability, resolve_channel=channel_source,
                               fetch_playlist=self._request_html)

    def browser_url(
        self, provider_id: str, episode: str | None = None, ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        identity = _title_id(provider_id)
        episode_id = _episode_id("1" if episode is None else episode)
        language = str(ttype or "sub").strip().lower()
        if not identity or not episode_id or language not in ("sub", "dub"):
            return _BASE_URL
        return f"{_BASE_URL}/embed/ani/{identity}/{quote(episode_id, safe='')}/{language}"


PROVIDER_CLASS = AniPMProvider
