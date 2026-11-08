import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ..services.http import UA as USER_AGENT
from ..services.network_affinity import ipv4_curl_options
from .shared.base import Provider
from .shared.models import normalize_episode_catalog, normalize_episode_sources, normalize_title

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}


def _unsafe_stream_uri(value: str) -> bool:
    decoded = urllib.parse.unquote(value)
    return any(ord(char) <= 32 or 127 <= ord(char) <= 159 or char == "\\"
               for char in value + decoded)


def _trusted_stream_url(url: Any, *, subtitle: bool = False) -> bool:
    if not isinstance(url, str) or not url or _unsafe_stream_uri(url) or "#" in url:
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if (parts.scheme != "https" or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None):
            return False
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            return False
        return (host in {"s.vidcloud.se", "cdn.vidcloud.se"}
                or host.endswith((".bcdn1.se", ".bcdn2.se", ".bcdn3.se"))
                or (subtitle and host == "s.anicdn.se"))
    except ValueError:
        return False


def _trusted_child_url(base: str, uri: Any) -> str | None:
    if not isinstance(uri, str) or not uri or _unsafe_stream_uri(uri):
        return None
    try:
        url = urllib.parse.urljoin(base, uri)
    except ValueError:
        return None
    return url if _trusted_stream_url(url) else None


def _check_playlist_operation(deadline, cancel):
    from ..extractors.senshi_runtime import SenshiRuntimeError

    if cancel is not None and cancel.is_set():
        raise SenshiRuntimeError("Senshi extraction cancelled")
    if time.monotonic() >= deadline:
        raise SenshiRuntimeError("Senshi extraction timed out")


class Senshi(Provider):
    id = "senshi"

    def __init__(self, request_json_fn=None, *args, **kwargs):
        self._request_json = request_json_fn
        self._sources_cache = {}
        self._remote_sources_cache = {}
        if not hasattr(self, 'metadata'):
            self.metadata = {}
        if not hasattr(self, 'domains'):
            self.domains = []

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://senshi.to"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Senshi")

    def _fetch_json(self, url: str) -> Any:
        headers = dict(HEADERS)
        headers["Referer"] = f"{self.base_url}/"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=7) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return []
            logging.debug("Senshi HTTP error: %s", e)
            return []
        except Exception as e:
            logging.debug("Senshi fetch error: %s", e)
            return []

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        # Search only Senshi's catalog; external title IDs do not prove availability.
        try:
            payload = json.dumps({
                "searchTerm": query,
                "types": [],
                "genres": [],
                "status": [],
                "seasons": [],
                "year": "",
                "studios": [],
                "producers": [],
                "languages": [],
                "page": 1,
                "limit": 20,
                "sortBy": "score_desc",
                "languagePreference": "EN",
            }).encode("utf-8")
            headers = dict(HEADERS)
            headers["Referer"] = f"{self.base_url}/"
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(f"{self.base_url}/anime/filter", data=payload, headers=headers)
            with urllib.request.urlopen(req, timeout=7) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                media_list = data.get("data") or []
                if media_list:
                    results = []
                    for item in media_list:
                        mal_id = item.get("id")
                        if not mal_id:
                            continue
                        name = item.get("title_english") or item.get("title") or "Unknown"
                        pic = item.get("anime_picture") or ""
                        if pic.startswith("/"):
                            pic = f"{self.base_url}{pic}"
                        results.append(normalize_title(
                            {
                                "_id": str(mal_id),
                                "malId": mal_id,
                                "name": name,
                                "romajiName": item.get("title") or "",
                                "englishName": item.get("title_english") or "",
                                "nativeName": "",
                                "thumbnail": pic,
                                "type": "anime",
                                "format": item.get("type"),
                                "status": item.get("ani_status") or "Finished",
                            },
                            provider_id=self.id,
                            provider_name=self.name,
                        ))
                    if results:
                        return results
        except Exception as e:
            logging.debug("Senshi native search error: %s", e)

        return []

    def get_title(self, provider_id: str) -> dict[str, Any]:
        return normalize_title({
            "_id": str(provider_id),
            "malId": str(provider_id),
            "name": f"MAL ID: {provider_id}",
            "url": f"https://myanimelist.net/anime/{provider_id}",
        }, provider_id=self.id, provider_name=self.name)

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        episodes_data = self._fetch_json(f"{self.base_url}/episodes/{provider_id}")
        if not episodes_data:
            return normalize_episode_catalog(
                {"episodes": []},
                provider_id=self.id,
                provider_title_id=provider_id,
            )

        eps_formatted = []
        ids = []
        labels = {}

        for ep in episodes_data:
            ep_num = str(ep.get("ep_id"))
            if not ep_num or ep_num == "None":
                continue

            ids.append(ep_num)
            labels[ep_num] = ep_num
            eps_formatted.append({
                "id": ep_num,
                "label": ep_num
            })

        def _parse_ep(val):
            try:
                return float(val)
            except ValueError:
                return 0.0

        eps_formatted.sort(key=lambda x: _parse_ep(x["id"]))
        ids = [ep["id"] for ep in eps_formatted]

        return normalize_episode_catalog(
            {
                "state": "loaded",
                "ids": ids,
                "labels": labels,
                "episodes": {ttype: eps_formatted}
            },
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    @staticmethod
    def _cache_get(cache: dict, key):
        now = time.monotonic()
        for expired in [k for k, (expires, _) in cache.items() if expires <= now]:
            del cache[expired]
        value = cache.get(key)
        return value[1] if value else None

    @staticmethod
    def _cache_put(cache: dict, key, value, ttl: float, limit: int):
        cache.pop(key, None)
        cache[key] = (time.monotonic() + ttl, value)
        while len(cache) > limit:
            del cache[next(iter(cache))]

    @staticmethod
    def _source_candidates(entry: dict) -> list[dict]:
        raw = entry.get("source") or entry.get("file")
        if not isinstance(raw, list):
            raw = [raw]
        candidates = []
        for item in raw:
            if isinstance(item, str):
                item = {"src": item}
            if isinstance(item, dict):
                url = item.get("src") or item.get("file") or item.get("url")
                if _trusted_stream_url(url):
                    candidates.append({"label": entry.get("label"), **item, "src": url})
        return candidates

    def _resolve_remote_sources(self, remote_id) -> list[dict]:
        from ..extractors.senshi_runtime import SenshiRuntimeError, resolve_vidcloud
        from ..services.browser_access.operation import current_operation
        from ..services.browser_access.profiles import BrowserAccessCancelled

        cancel = current_operation().cancel
        if cancel is not None and cancel.is_set():
            raise BrowserAccessCancelled("Senshi extraction cancelled")
        key = (self.base_url, str(remote_id))
        cached = self._cache_get(self._remote_sources_cache, key)
        if cached is not None:
            return cached
        try:
            # The runtime owns the bounded retry and overall extraction deadline.
            entries = resolve_vidcloud(remote_id, timeout=30, cancel=cancel)
        except SenshiRuntimeError as error:
            if str(error) == "Senshi extraction cancelled" or (cancel is not None and cancel.is_set()):
                raise
            logging.debug("Senshi source resolution unavailable: %s", error)
            return []
        entries = [entry for entry in entries if isinstance(entry, dict) and self._source_candidates(entry)]
        if entries:
            self._cache_put(self._remote_sources_cache, key, entries, 240, 64)
        return entries

    def _read_playlist(self, url: str) -> tuple[str, str]:
        from curl_cffi.requests import Session

        from ..extractors.senshi_runtime import SenshiRuntimeError, fetch_bytes
        from ..services.browser_access.operation import current_operation

        if not _trusted_stream_url(url):
            raise SenshiRuntimeError("Senshi playlist destination not permitted")
        deadline = time.monotonic() + 10
        cancel = current_operation().cancel
        headers = {**HEADERS, "Referer": f"{self.base_url}/", "Origin": self.base_url}
        with Session(impersonate="chrome", trust_env=False, curl_options=ipv4_curl_options()) as session:
            for hop in range(4):
                _check_playlist_operation(deadline, cancel)
                response, content = fetch_bytes(
                    session, "GET", url, headers, None, deadline, cancel,
                    limit=1048576, allow_redirect_response=True,
                )
                _check_playlist_operation(deadline, cancel)
                final_url = response.url
                if not _trusted_stream_url(final_url):
                    raise SenshiRuntimeError("Senshi playlist destination not permitted")
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location") or response.headers.get("location")
                    target = _trusted_child_url(final_url, location)
                    if target is None or hop == 3:
                        raise SenshiRuntimeError("Senshi playlist redirect not permitted")
                    # Each request is credential-free, even after a CDN redirect.
                    session.cookies.clear()
                    url = target
                    continue
                if not 200 <= response.status_code < 300 or len(content) > 1048576:
                    raise SenshiRuntimeError("Senshi playlist request failed")
                text = content.decode("utf-8", errors="replace")
                break
        if text.startswith("EM3U8v1:"):
            from ..media.decryption import decrypt_em3u8
            text = decrypt_em3u8(text) or ""
        return final_url, text

    @staticmethod
    def _subtitles(tracks: list, is_dub: bool) -> list[dict]:
        usable = []
        for track in tracks:
            if not isinstance(track, dict):
                continue
            url = track.get("url") or track.get("file") or track.get("vtt_url")
            label = str(track.get("label") or "English")
            kind = str(track.get("kind") or "")
            if not _trusted_stream_url(url, subtitle=True):
                continue
            if any(word in (label + " " + kind).lower() for word in ("chapter", "thumb")):
                continue
            usable.append({"url": url, "file": url, "label": label, "default": False})
        matching = [
            track for track in usable
            if ("dub" in track["label"].lower() or "ai_dub" in track["url"].lower()) == is_dub
        ]
        subtitles = matching or usable
        subtitles.sort(key=lambda track: "english" not in track["label"].lower())
        if subtitles:
            subtitles[0]["default"] = True
        return subtitles

    def _playlist_variants(self, url: str, quality: str, is_dub: bool) -> list[dict]:
        import m3u8

        try:
            if not _trusted_stream_url(url):
                return []
            final_url, text = self._read_playlist(url)
            if not _trusted_stream_url(final_url) or text.splitlines()[:1] != ["#EXTM3U"]:
                return []
            # Reject broken URI attributes before the parser can discard them.
            if (any(line.count('"') % 2 for line in text.splitlines())
                    or any((ord(char) < 32 and char not in "\r\n") or 127 <= ord(char) <= 159
                           for char in text)):
                return []
            playlist = m3u8.loads(text, uri=final_url)
        except Exception as error:
            from ..services.browser_access.operation import current_operation
            from ..services.browser_access.profiles import BrowserAccessCancelled

            cancel = current_operation().cancel
            if (isinstance(error, BrowserAccessCancelled) or str(error) == "Senshi extraction cancelled"
                    or (cancel is not None and cancel.is_set())):
                raise
            logging.debug("Senshi playlist unavailable")
            return []
        if not playlist.playlists:
            return [{"url": final_url, "resolution": quality}] if playlist.segments else []

        language = "en" if is_dub else "ja"
        variants = []
        for item in playlist.playlists:
            audio_url = None
            group = item.stream_info.audio
            if group:
                audio = [
                    media for media in playlist.media
                    if media.type == "AUDIO" and media.group_id == group
                ]
                matching = [
                    media for media in audio
                    if str(media.language or "").lower().split("-")[0] == language
                ]
                if not matching:
                    # Never hand a detached video rendition to the player alone.
                    continue
                selected = next((media for media in matching if media.default == "YES"), matching[0])
                if selected.uri:
                    audio_url = _trusted_child_url(final_url, selected.uri)
                    if audio_url is None:
                        continue
            video_url = _trusted_child_url(final_url, item.uri)
            if video_url is None:
                continue
            resolution = item.stream_info.resolution
            variant = {
                "url": video_url,
                "resolution": f"{resolution[1]}p" if resolution else quality,
            }
            if audio_url:
                variant["audio_url"] = audio_url
            variants.append(variant)
        return variants

    def _extract_embed_sources(self, matching_embeds: list, is_dub: bool) -> list[dict]:
        from ..core.api import ProviderDependencyError

        all_sources = []
        dependency_error = None
        wanted = "dub" if is_dub else "sub"
        status_label = "Dub" if is_dub else "HardSub"
        headers = {
            "Referer": f"{self.base_url}/", "Origin": self.base_url,
            "User-Agent": HEADERS["User-Agent"],
        }
        for emb in matching_embeds:
            remote_id = emb.get("remote_source_id") or emb.get("remoteSourceId")
            if remote_id:
                from ..services.requirements import require_provider_operation

                try:
                    require_provider_operation(self.id, "remote_sources")
                    remote_entries = self._resolve_remote_sources(remote_id)
                except ProviderDependencyError as exc:
                    dependency_error = exc
                    remote_entries = []
                for entry in remote_entries:
                    candidates = self._source_candidates(entry)
                    source = next(
                        (src for src in candidates if str(src.get("label") or "").lower() == wanted),
                        None,
                    )
                    if source is None:
                        source = next(
                            (src for src in candidates if str(src.get("label") or "").lower() in {"", "both"}),
                            None,
                        )
                    if source is None:
                        continue
                    subtitles = self._subtitles(entry.get("tracks") or [], is_dub)
                    variants = self._playlist_variants(source["src"], source.get("quality") or "auto", is_dub)
                    seen = set()
                    for variant in variants:
                        identity = (variant["url"], variant.get("audio_url"))
                        if identity in seen:
                            continue
                        seen.add(identity)
                        res = variant["resolution"]
                        row = {
                            "sourceName": f"[{status_label}] {res}",
                            "link": variant["url"], "sourceUrl": variant["url"],
                            "streamUrl": variant["url"],
                            "resolution": res, "type": "hls",
                            "priority": 1 if res == "1080p" else 2,
                            "referer": f"{self.base_url}/", "headers": headers,
                            "requires_proxy": True, "subtitles": subtitles, "ip_version": 4,
                        }
                        if variant.get("audio_url"):
                            row["audio_url"] = variant["audio_url"]
                        all_sources.append(row)
            else:
                direct_url = emb.get("url") or emb.get("embed_url") or emb.get("embedUrl")
                if direct_url:
                    all_sources.append({
                        "sourceName": f"[{status_label}] auto",
                        "link": direct_url, "streamUrl": direct_url, "sourceUrl": direct_url,
                        "resolution": "auto",
                        "type": "hls" if ".m3u8" in str(direct_url) else "mp4",
                        "priority": 2, "referer": f"{self.base_url}/",
                        "headers": headers, "requires_proxy": True,
                    })

            for field, name, priority in (("server2", "StreamNin", 3), ("serverFM", "FileMoon", 4)):
                if emb.get(field):
                    all_sources.append({
                        "sourceName": f"{name} ({status_label})", "link": emb[field],
                        "resolution": "auto", "type": "embed", "priority": priority,
                        "headers": {"Referer": f"{self.base_url}/"},
                    })
        if not all_sources and dependency_error is not None:
            raise dependency_error
        if dependency_error is not None:
            from ..services.requirements import add_dependency_hints, dependency_failure_hint

            all_sources = [add_dependency_hints(source, [dependency_failure_hint(dependency_error)]) for source in all_sources]
        return all_sources

    def _episode_embeds(self, provider_id: str, episode: str, ttype: str):
        """Fetch/filter descriptors without resolving any remote embed."""
        embeds = self._fetch_json(f"{self.base_url}/episode-embeds/{provider_id}/{episode}")
        if not isinstance(embeds, list):
            return None
        want_dub = str(ttype).lower() == "dub"
        return [
            emb for emb in embeds if isinstance(emb, dict)
            and (str(emb.get("status") or "").lower() == "dub") == want_dub
        ]

    @staticmethod
    def _source_embed_routes(emb):
        routes = []
        remote = emb.get("remote_source_id") or emb.get("remoteSourceId")
        direct = emb.get("url") or emb.get("embed_url") or emb.get("embedUrl")
        if remote:
            routes.append(("Vidcloud", {"remote_source_id": remote}))
        elif direct:
            routes.append(("Direct link", {"url": direct}))
        for field, name in (("server2", "StreamNin"), ("serverFM", "FileMoon")):
            if emb.get(field):
                routes.append((name, {field: emb[field]}))
        return routes

    def discover_episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> list[str]:
        """Describe available source routes; do not extract or probe media."""
        return [name for emb in self._episode_embeds(provider_id, episode, ttype) or ()
                for name, _ in self._source_embed_routes(emb)]

    def extract_discovered_source(self, provider_id: str, episode: str, source: str, ttype: str = "sub") -> list[dict]:
        """Reuse native resolution for just the selected named source route."""
        selected = [route for emb in self._episode_embeds(provider_id, episode, ttype) or ()
                    for name, route in self._source_embed_routes(emb) if name == source]
        return self._extract_embed_sources(selected, is_dub=str(ttype).lower() == "dub") if selected else []

    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> dict[str, Any] | None:
        language = str(ttype).lower()
        cache_key = (self.base_url, str(provider_id), str(episode), language)
        cached = self._cache_get(self._sources_cache, cache_key)
        if cached is not None:
            return cached
        target_embeds = self._episode_embeds(provider_id, episode, language)
        if target_embeds is None:
            return None
        want_dub = language == "dub"
        sources = self._extract_embed_sources(target_embeds, is_dub=want_dub)
        if not sources:
            return None
        result = normalize_episode_sources(
            {"episode": {"sourceUrls": sources}},
            provider_id=self.id, provider_title_id=provider_id, episode=episode,
        )
        from ..services.requirements import add_dependency_hints, dependency_hint_messages

        result = add_dependency_hints(result, [hint for source in sources for hint in dependency_hint_messages(source)])
        self._cache_put(self._sources_cache, cache_key, result, 120, 32)
        return result

    def browser_url(self, *, provider_id: str = "", episode: str = "") -> str:
        if provider_id and episode:
            return f"{self.base_url}/watch/{provider_id}/{episode}"
        if provider_id:
            return f"{self.base_url}/watch/{provider_id}/1"
        return self.base_url

PROVIDER_CLASS = Senshi
