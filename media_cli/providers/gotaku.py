"""Gotaku streaming provider implementation.

Upstream reference: https://github.com/yuzono/anime-extensions (Apache-2.0)
License: Apache-2.0 (retained per upstream attribution requirements)
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from media_cli.media.provider_skips import normalize_provider_skips
from media_cli.services.http import SSL_CTX_SECURE

from .shared.gotaku_crypto import (
    DEFAULT_API_SEED,
    DEFAULT_API_VERSION,
    DEFAULT_HKDF_INFO,
    DEFAULT_MANIFEST_SEED,
    b64url_decode,
    build_manifest_handshake_request,
    decrypt_api_body,
    decrypt_manifest_response,
    decrypt_playlist_body,
    derive_playlist_key,
    extract_credentials_from_bundle,
    extract_playlist_token,
)
from .shared.hls import parse_master_playlist_variants
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)

_logger = logging.getLogger(__name__)

_BASE_URL = "https://gotaku.to"
_API_URL = f"{_BASE_URL}/api/v1"
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_MAL_LINK_REGEX = re.compile(r"myanimelist\.net/anime/(\d+)", re.I)
_AL_LINK_REGEX = re.compile(r"anilist\.co/anime/(\d+)", re.I)
_MANIFEST_BASE_REGEX = re.compile(r'data-manifest-base=["\']([^"\']+)["\']')
_MANIFEST_STAMP_REGEX = re.compile(r'data-manifest-stamp=["\']([^"\']+)["\']')
_MAIN_JS_REGEX = re.compile(r'src=["\'](/build/[^"\']+/assets/main-[^"\']+\.js)["\']')


def _api_headers(referer: str = _BASE_URL) -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Accept": "application/json",
        "Referer": referer,
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


def _extract_mal_id(links: dict[str, Any] | None) -> int | None:
    if not isinstance(links, dict):
        return None
    mal_val = links.get("mal") or ""
    if isinstance(mal_val, int):
        return mal_val
    if isinstance(mal_val, str):
        match = _MAL_LINK_REGEX.search(mal_val)
        if match:
            return int(match.group(1))
    return None


class GotakuProvider:
    id = "gotaku"
    name = "Gotaku"
    audio_mode = "dual"

    def __init__(self, request_json_fn=None, *, clock_fn=time.time):
        self._clock = clock_fn
        self._api_version = DEFAULT_API_VERSION
        self._api_seed = DEFAULT_API_SEED
        self._hkdf_info = DEFAULT_HKDF_INFO
        self._refresh_lock = threading.Lock()
        self._last_refresh_time = 0.0
        self._title_cache: dict[str, dict[str, Any]] = {}
        self._catalog_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._cache_generation: int = 0
        self._api_pacing_lock = threading.Lock()
        self._last_api_request_time = 0.0
        self._min_request_interval = 0.4

    @property
    def base_url(self) -> str:
        return _BASE_URL

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        slug = str(provider_id or "").strip()
        if not slug:
            return _BASE_URL
        if episode:
            ep_str = str(episode).strip()
            matched_id = ep_str
            try:
                cat = self.episode_catalog(slug, ttype=ttype)
                episodes_map = cat.get("episodes") if isinstance(cat.get("episodes"), dict) else {}
                ep_list = episodes_map.get(ttype) or episodes_map.get("sub") or []
                for ep in ep_list:
                    if str(ep.get("label")) == ep_str or str(ep.get("id")) == ep_str:
                        matched_id = str(ep.get("id"))
                        break
            except Exception:
                pass
            return f"{_BASE_URL}/title/{urllib.parse.quote(slug)}?ep={urllib.parse.quote(matched_id)}"
        return f"{_BASE_URL}/title/{urllib.parse.quote(slug)}"

    def invalidate_title_cache(self, title_id: str | None = None) -> None:
        with self._cache_lock:
            self._cache_generation += 1
            if title_id:
                self._title_cache.pop(str(title_id), None)
                for key in list(self._catalog_cache.keys()):
                    if key[0] == str(title_id):
                        self._catalog_cache.pop(key, None)
            else:
                self._title_cache.clear()
                self._catalog_cache.clear()

    # -------------------------------------------------------------------------
    # Credential Discovery & Refresh
    # -------------------------------------------------------------------------

    def refresh_credentials(self, force: bool = False) -> bool:
        """Dynamically discover API version and seed from Gotaku frontend bundle."""
        now = self._clock()
        with self._refresh_lock:
            if not force and (now - self._last_refresh_time) < 300:
                return False
            self._last_refresh_time = now

            try:
                req = urllib.request.Request(
                    f"{_BASE_URL}/",
                    headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
                )
                with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=10) as resp:
                    html = resp.read().decode("utf-8", errors="ignore")

                match = _MAIN_JS_REGEX.search(html)
                if not match:
                    _logger.debug("Gotaku: could not find main JS bundle path")
                    return False

                js_path = match.group(1)
                js_url = urllib.parse.urljoin(_BASE_URL, js_path)
                js_req = urllib.request.Request(
                    js_url,
                    headers={"User-Agent": _USER_AGENT, "Referer": f"{_BASE_URL}/"},
                )
                with urllib.request.urlopen(js_req, context=SSL_CTX_SECURE, timeout=15) as resp:
                    js_code = resp.read().decode("utf-8", errors="ignore")

                creds = extract_credentials_from_bundle(js_code)
                if not creds:
                    _logger.debug("Gotaku: regex failed on JS bundle")
                    return False

                ver, seed, info = creds
                # Verification oracle: test /api/v1/auth/me?k={ver}
                oracle_req = urllib.request.Request(
                    f"{_API_URL}/auth/me?k={ver}",
                    headers=_api_headers(f"{_BASE_URL}/browse"),
                )
                with urllib.request.urlopen(oracle_req, context=SSL_CTX_SECURE, timeout=10) as resp:
                    oracle_body = resp.read()

                decrypted = decrypt_api_body(oracle_body, version=ver, seed=seed, info=info)
                if decrypted is not None:
                    self._api_version = ver
                    self._api_seed = seed
                    self._hkdf_info = info
                    _logger.debug("Gotaku: refreshed credentials (k=%d, seed_len=%d)", ver, len(seed))
                    return True
            except Exception as exc:
                _logger.debug("Gotaku credential refresh error: %s", exc)

        return False

    def _decrypt_api_call(self, url: str, referer: str = _BASE_URL, is_retry: bool = False) -> Any:
        with self._api_pacing_lock:
            now = time.monotonic()
            elapsed = now - self._last_api_request_time
            if elapsed < self._min_request_interval:
                time.sleep(self._min_request_interval - elapsed)
            self._last_api_request_time = time.monotonic()

        sep = "&" if "?" in url else "?"
        target_url = f"{url}{sep}k={self._api_version}"
        headers = _api_headers(referer)

        try:
            req = urllib.request.Request(target_url, headers=headers)
            with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=15) as resp:
                body = resp.read()
        except urllib.error.HTTPError as err:
            if err.code == 426 and not is_retry:  # Upgrade Required
                _logger.debug("Gotaku: API rejected build k=%d with 426, refreshing...", self._api_version)
                if self.refresh_credentials(force=True):
                    return self._decrypt_api_call(url, referer=referer, is_retry=True)
            elif err.code == 403:
                body_sample = ""
                try:
                    body_sample = err.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                is_waf_challenge = (
                    err.headers.get("X-Waf") == "challenge"
                    or "captcha_required" in body_sample
                    or "turnstile" in body_sample.lower()
                )
                if is_waf_challenge:
                    _logger.warning("Gotaku: WAF challenge required (captcha_required). Turnstile verification at /waf/challenge needed.")
                else:
                    _logger.warning("Gotaku: HTTP 403 Forbidden on %s", url)
            return None
        except Exception as exc:
            _logger.debug("Gotaku API request failed for %s: %s", url, exc)
            return None

        decrypted = decrypt_api_body(
            body,
            version=self._api_version,
            seed=self._api_seed,
            info=self._hkdf_info,
        )
        if decrypted is None and not is_retry:
            _logger.debug("Gotaku: decrypt failed for k=%d, attempting resync...", self._api_version)
            if self.refresh_credentials(force=True):
                return self._decrypt_api_call(url, referer=referer, is_retry=True)

        return decrypted

    # -------------------------------------------------------------------------
    # Search & Metadata
    # -------------------------------------------------------------------------

    def search(
        self,
        query: str,
        ttype: str = "sub",
        *args: Any,
        page: int = 1,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        if isinstance(ttype, int):
            actual_page = ttype
            actual_ttype = str(args[0]) if args else "sub"
        else:
            actual_page = int(kwargs.get("page", page) or 1)
            actual_ttype = str(ttype or "sub")

        query = str(query or "").strip()
        if not query:
            return []

        params = [
            ("q", query),
            ("limit", "28"),
            ("page", str(max(1, actual_page))),
            ("sort", "trending_week"),
        ]
        lang = actual_ttype.strip().lower()
        if lang == "dub":
            params.append(("track", "dub"))

        url = f"{_API_URL}/titles?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=_api_headers(f"{_BASE_URL}/browse"))
        try:
            with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            _logger.debug("Gotaku search failed: %s", exc)
            return []

        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return []

        raw_titles = []
        for item in items:
            if not isinstance(item, dict):
                continue
            slug = item.get("id")
            if not slug:
                continue
            ep_info = item.get("episodes") or {}
            raw_titles.append({
                "_id": slug,
                "name": item.get("name") or "",
                "englishName": item.get("name") or "",
                "thumbnail": item.get("poster_url") or "",
                "banner": item.get("backdrop_url") or "",
                "format": item.get("format") or "TV",
                "is_adult": item.get("is_adult") is True,
                "availableEpisodes": {
                    "sub": ep_info.get("latest_sub") or 0,
                    "dub": ep_info.get("latest_dub") or 0,
                },
                "episodeCount": ep_info.get("total"),
            })

        return normalize_titles(raw_titles, provider_id=self.id, provider_name=self.name)

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        slug = str(provider_id or "").strip()
        if not slug:
            return None

        with self._cache_lock:
            if slug in self._title_cache:
                return dict(self._title_cache[slug])
            current_gen = self._cache_generation

        url = f"{_API_URL}/titles/{urllib.parse.quote(slug)}"
        req = urllib.request.Request(url, headers=_api_headers(f"{_BASE_URL}/title/{slug}"))
        try:
            with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            _logger.debug("Gotaku get_title failed for %s: %s", slug, exc)
            return None

        item = data.get("data", {}).get("title") if isinstance(data, dict) else None
        if not isinstance(item, dict):
            return None

        ep_info = item.get("episodes") or {}
        links = item.get("links") or {}
        raw = {
            "_id": slug,
            "name": item.get("name") or "",
            "englishName": item.get("name") or "",
            "altNames": [x for x in item.get("alt_names") or [] if isinstance(x, str) and x.strip()],
            "thumbnail": item.get("poster_url") or "",
            "banner": item.get("backdrop_url") or "",
            "description": item.get("synopsis") or "",
            "type": item.get("format") or "TV",
            "format": item.get("format") or "TV",
            "status": item.get("status") or "",
            "airedStart": item.get("aired"),
            "airedEnd": item.get("ended"),
            "episodeCount": ep_info.get("total"),
            "availableEpisodes": {
                "sub": ep_info.get("latest_sub") or 0,
                "dub": ep_info.get("latest_dub") or 0,
            },
            "genres": [g["name"] for g in item.get("genres", []) if isinstance(g, dict) and g.get("name")],
            "tags": [t["name"] for t in item.get("themes", []) if isinstance(t, dict) and t.get("name")],
            "aniListId": item.get("anilist_id"),
            "malId": _extract_mal_id(links),
            "score": item.get("mal_score") or item.get("ratings_avg"),
        }

        normalized = normalize_title(raw, provider_id=self.id, provider_name=self.name)
        if normalized:
            with self._cache_lock:
                if self._cache_generation == current_gen:
                    self._title_cache[slug] = normalized
        return normalized

    # -------------------------------------------------------------------------
    # Episode Catalogue
    # -------------------------------------------------------------------------

    def episode_catalog(self, provider_id: str, ttype: str = "sub", cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        slug = str(provider_id or "").strip()
        lang = str(ttype or "sub").strip().lower()
        if not slug:
            return normalize_episode_catalog({}, provider_id=self.id, provider_title_id="")

        with self._cache_lock:
            cache_key = (slug, lang)
            if cache_key in self._catalog_cache:
                return dict(self._catalog_cache[cache_key])
            current_gen = self._cache_generation

        watch_ref = f"{_BASE_URL}/title/{slug}"
        payload = self._decrypt_api_call(f"{_API_URL}/titles/{slug}/episodes", referer=watch_ref)
        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            return normalize_episode_catalog(
                {"episodes": {"sub": [], "dub": []}, "state": "error", "error": "No episodes returned"},
                provider_id=self.id,
                provider_title_id=slug,
            )

        sub_eps = []
        dub_eps = []
        labels: dict[str, str] = {}

        for ep in items:
            if not isinstance(ep, dict):
                continue
            ep_id = ep.get("id")
            if not ep_id:
                continue
            num_val = ep.get("number")
            num_label = str(ep.get("label") or (int(num_val) if num_val is not None and int(num_val) == num_val else num_val or ""))
            labels[ep_id] = num_label

            ep_row = {
                "id": ep_id,
                "label": num_label,
                "name": ep.get("name") or "",
                "is_filler": ep.get("is_filler") is True,
                "thumbnail": ep.get("thumbnail_url") or "",
            }
            if ep.get("sub"):
                sub_eps.append(ep_row)
            if ep.get("dub"):
                dub_eps.append(ep_row)

        catalog_data = {
            "episodes": {
                "sub": sub_eps,
                "dub": dub_eps,
            },
            "labels": labels,
            "translation_type": lang,
            "state": "loaded",
        }

        normalized = normalize_episode_catalog(catalog_data, provider_id=self.id, provider_title_id=slug)
        with self._cache_lock:
            if self._cache_generation == current_gen:
                self._catalog_cache[cache_key] = normalized
        return normalized

    # -------------------------------------------------------------------------
    # Episode Sources & Playback Extraction
    # -------------------------------------------------------------------------

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        slug = str(provider_id or "").strip()
        ep_query = str(episode or "").strip()
        lang = str(ttype or "sub").strip().lower()
        if not slug or not ep_query:
            return None

        # Resolve episode ID: ep_query could be a native slug or a label/number
        ep_id = ep_query
        catalog = self.episode_catalog(slug, ttype=lang)
        episodes_map = catalog.get("episodes") if isinstance(catalog.get("episodes"), dict) else {}
        ep_list = episodes_map.get(lang) or episodes_map.get("sub") or []

        matched_ep = next((e for e in ep_list if e.get("id") == ep_query), None)
        if matched_ep is None:
            matched_ep = next(
                (
                    e for e in ep_list
                    if str(e.get("display_number")) == ep_query
                    or str(e.get("primary_number")) == ep_query
                    or str(e.get("label")) == ep_query
                    or str(e.get("number")) == ep_query
                    or str(e.get("label", "")).lower() == f"episode {ep_query}".lower()
                ),
                None,
            )
        if matched_ep is not None and matched_ep.get("id"):
            ep_id = matched_ep["id"]

        watch_ref = f"{_BASE_URL}/title/{slug}?ep={ep_id}"

        # 1. Fetch Audio Flags
        flags_data = self._decrypt_api_call(f"{_API_URL}/episodes/{ep_id}", referer=watch_ref)
        flags = flags_data.get("data") if isinstance(flags_data, dict) else {}
        if not isinstance(flags, dict):
            _logger.debug("Gotaku: could not retrieve flags for episode %s", ep_id)
            return None

        # Determine target audio channel
        if lang == "dub":
            if not flags.get("dub"):
                _logger.debug("Gotaku: dub unavailable for episode %s", ep_id)
                return None
            channel = "dub"
        else:
            if flags.get("soft_sub"):
                channel = "soft_sub"
            elif flags.get("hard_sub"):
                channel = "hard_sub"
            else:
                _logger.debug("Gotaku: no sub/hardsub flag for episode %s", ep_id)
                return None

        # 2. Fetch Embed URL and skip times
        embed_data = self._decrypt_api_call(
            f"{_API_URL}/episodes/{ep_id}/embed?type={channel}",
            referer=watch_ref,
        )
        embed_payload = embed_data.get("data") if isinstance(embed_data, dict) else None
        if not isinstance(embed_payload, dict):
            _logger.debug("Gotaku: could not retrieve embed for %s (%s)", ep_id, channel)
            return None

        embed_url = embed_payload.get("url")
        if not embed_url:
            return None
        if not embed_url.startswith("http"):
            embed_url = urllib.parse.urljoin(_BASE_URL, embed_url)

        # Parse Native Intro/Outro Chapters
        skip_obj = embed_payload.get("skip") or {}
        native_skips = []
        if isinstance(skip_obj, dict):
            intro = skip_obj.get("intro")
            outro = skip_obj.get("outro")
            skip_payload = {}
            if isinstance(intro, list) and len(intro) >= 2 and intro[1] > intro[0]:
                skip_payload["op"] = {"start": float(intro[0]), "end": float(intro[1])}
            if isinstance(outro, list) and len(outro) >= 2 and outro[1] > outro[0]:
                skip_payload["ed"] = {"start": float(outro[0]), "end": float(outro[1])}
            if skip_payload:
                native_skips = normalize_provider_skips(skip_payload)

        # 3. Scrape embed HTML for manifest attributes
        try:
            req = urllib.request.Request(
                embed_url,
                headers={"User-Agent": _USER_AGENT, "Referer": watch_ref},
            )
            with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=12) as resp:
                embed_html = resp.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            _logger.debug("Gotaku embed page fetch failed: %s", exc)
            return None

        m_base = _MANIFEST_BASE_REGEX.search(embed_html)
        m_stamp = _MANIFEST_STAMP_REGEX.search(embed_html)
        if not m_base or not m_stamp:
            _logger.debug("Gotaku: data-manifest-base or stamp missing on embed page")
            return None

        manifest_base = m_base.group(1).strip()
        manifest_stamp = m_stamp.group(1).strip()

        # 4. Manifest Handshake
        try:
            handshake_url, nonce = build_manifest_handshake_request(manifest_base, manifest_stamp)
            m_req = urllib.request.Request(
                handshake_url,
                headers={
                    "User-Agent": _USER_AGENT,
                    "Accept": "text/plain",
                    "Referer": embed_url,
                },
            )
            with urllib.request.urlopen(m_req, context=SSL_CTX_SECURE, timeout=12) as resp:
                enc_response = resp.read().decode("ascii", errors="ignore").strip()

            manifest = decrypt_manifest_response(enc_response, nonce)
        except Exception as exc:
            _logger.debug("Gotaku manifest handshake failed: %s", exc)
            return None

        if not isinstance(manifest, dict) or not manifest.get("source"):
            _logger.debug("Gotaku: decrypted manifest invalid or missing source")
            return None

        stream_url = manifest["source"]
        key_seed_b64 = manifest.get("keySeed") or ""
        obf = manifest.get("obf") or {}
        segment_bytes = int(obf.get("segmentBytes", 8192)) if isinstance(obf, dict) else 8192

        # Subtitles (if soft subtitles were present)
        subtitles = []
        for s in manifest.get("subtitles") or []:
            if isinstance(s, dict) and s.get("url"):
                subtitles.append({
                    "url": s["url"],
                    "label": s.get("label") or s.get("language") or "English",
                    "lang": s.get("lang") or "en",
                    "default": True,
                })

        source_entries = []
        try:
            req = urllib.request.Request(
                stream_url,
                headers={"User-Agent": _USER_AGENT, "Referer": f"{_BASE_URL}/", "Accept": "*/*"},
            )
            with urllib.request.urlopen(req, context=SSL_CTX_SECURE, timeout=10) as resp:
                final_stream_url = resp.geturl()
                master_body = resp.read()

            token = extract_playlist_token(final_stream_url) or extract_playlist_token(stream_url)
            raw_seed = b64url_decode(key_seed_b64) if isinstance(key_seed_b64, str) else key_seed_b64
            pl_key = derive_playlist_key(raw_seed, token)
            dec_master = decrypt_playlist_body(master_body, pl_key)
            if dec_master:
                variants = parse_master_playlist_variants(final_stream_url, dec_master)
                variants.sort(key=lambda v: (v.get("height") or 0, v.get("bandwidth") or 0), reverse=True)
                for var in variants:
                    v_url = var["url"]
                    res_label = var["resolution"]
                    source_entries.append({
                        "sourceName": f"Gotaku ({'Dub' if channel == 'dub' else 'Sub' if channel == 'soft_sub' else 'Hardsub'}) - {res_label}",
                        "streamUrl": v_url,
                        "type": "hls",
                        "resolution": res_label,
                        "requires_proxy": True,
                        "allow_ytdlp_expansion": False,
                        "referer": f"{_BASE_URL}/",
                        "headers": {
                            "User-Agent": _USER_AGENT,
                            "Referer": f"{_BASE_URL}/",
                            "Accept": "*/*",
                        },
                        "gotaku_key_seed": key_seed_b64,
                        "gotaku_segment_bytes": segment_bytes,
                        "subtitles": subtitles,
                        "subtitle_url": subtitles[0]["url"] if subtitles else "",
                        "skip_intervals": native_skips,
                    })
        except Exception as exc:
            _logger.debug("Gotaku variant expansion failed: %s", exc)

        # Retain Adaptive (auto) master stream as fallback
        source_entries.append({
            "sourceName": f"Gotaku ({'Dub' if channel == 'dub' else 'Sub' if channel == 'soft_sub' else 'Hardsub'}) - Auto",
            "streamUrl": stream_url,
            "type": "hls",
            "resolution": "auto",
            "requires_proxy": True,
            "allow_ytdlp_expansion": False,
            "referer": f"{_BASE_URL}/",
            "headers": {
                "User-Agent": _USER_AGENT,
                "Referer": f"{_BASE_URL}/",
                "Accept": "*/*",
            },
            "gotaku_key_seed": key_seed_b64,
            "gotaku_segment_bytes": segment_bytes,
            "subtitles": subtitles,
            "subtitle_url": subtitles[0]["url"] if subtitles else "",
            "skip_intervals": native_skips,
        })

        payload = {"episode": {"sourceUrls": source_entries}}
        return normalize_episode_sources(
            payload,
            provider_id=self.id,
            provider_title_id=slug,
            episode=str(matched_ep["label"] if matched_ep else ep_query),
        )


PROVIDER_CLASS = GotakuProvider
