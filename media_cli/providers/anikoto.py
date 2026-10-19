import json
import logging
import re
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from threading import RLock
from typing import Any

from bs4 import BeautifulSoup

from media_cli.services import anilist
from media_cli.services import normalize as anilist_normalize

from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_title,
    normalize_titles,
)

_logger = logging.getLogger(__name__)
_MAX_IDENTITY_QUERIES = 6
_MAX_NATIVE_ID_PROBES = 4
_PUBLIC_MEDIA_FIELDS = frozenset({
    "id", "idMal", "title", "synonyms", "format", "type", "status", "episodes",
    "duration", "season", "seasonYear", "startDate", "endDate", "countryOfOrigin",
    "isAdult", "averageScore", "meanScore", "popularity", "genres", "description",
    "coverImage", "bannerImage", "nextAiringEpisode",
})

_MEGAPLAY_KEY = b"i?LMTAx0Q6,:}50U".ljust(32, b"\x00")
_MEGAPLAY_IV = b"W0;27ToaUpl_P%'c"

_EXCHANGE_KEY_1 = ["AP6GeR8H0lwUz1", "UAz8Gwl10P6ReH"]
_KEY_1 = "ItFKjuWokn4ZpB"
_KEY_2 = "fOyt97QWFB3"
_EXCHANGE_KEY_2 = ["1majSlPQd2M5", "da1l2jSmP5QM"]
_EXCHANGE_KEY_3 = ["CPYvHj09Au3", "0jHA9CPYu3v"]
_KEY_3 = "736y1uTJpBLUX"


def _slug_title_from_watch_href(href: str) -> str:
    path = urllib.parse.urlparse(href).path
    slug = path.split("/watch/", 1)[-1].split("/ep-", 1)[0].strip("/")
    slug = re.sub(r"-[a-z0-9]{4,}$", "", slug)
    return slug.replace("-", " ").strip()


def _explicit_season(title: str) -> int | None:
    title = unicodedata.normalize("NFKC", title)
    s_m = re.search(r'season\s*(\d+)|(\d+)(?:st|nd|rd|th)\s*season|第\s*(\d+)\s*[期季]', title, re.I)
    if s_m:
        return int(next(group for group in s_m.groups() if group is not None))
    trailing = re.search(r'\s([2-9]|[1-9]\d)\s*$', title.strip())
    if trailing:
        return int(trailing.group(1))
    return None


def _season_from_title(title: str) -> int:
    return _explicit_season(title) or 1


def _title_identity(title: str) -> str:
    title = unicodedata.normalize("NFKC", title).casefold()
    title = title.replace("\u2019", "'").replace("\u2018", "'")
    title = re.sub(r"(\d+)(?:st|nd|rd|th)\s*season", r"season\1", title)
    title = re.sub(r"season\s+(\d+)", r"season\1", title)
    title = re.sub(r"第\s*(\d+)\s*[期季]", r"season\1", title)
    title = re.sub(r"\s([2-9]|[1-9]\d)\s*$", r" season\1", title)
    title = re.sub(r"\bseason1\b", "", title)
    return "".join(char for char in title if char.isalnum() or char in "!?'")


def _matching_titles(
    expected: tuple[str, ...], actual: tuple[str, ...], season: int, media_format: str = "",
    primary_titles: tuple[str, ...] = (),
) -> bool:
    if not actual:
        return False
    explicit = {_explicit_season(title) for title in actual} - {None}
    if explicit and explicit != {season}:
        return False
    for mark in "!?":
        if max(title.count(mark) for title in actual) != max(title.count(mark) for title in expected):
            return False
    markers = {"movie", "special", "ova", "oad", "ona"}
    def kinds(titles: tuple[str, ...]) -> set[str]:
        return {word.removesuffix("s") for title in titles
                for word in re.findall(r"\w+", title.casefold())
                if word.removesuffix("s") in markers}
    allowed = kinds(expected) | {media_format.casefold()}
    if media_format == "SPECIAL":
        allowed.add("special")
    if kinds(actual) - allowed:
        return False
    # Retain non-Latin names. Stripping them to ASCII made season digits match
    # completely unrelated shows. Do not interpret a partial word match as ID proof.
    expected_keys = {_title_identity(title) for title in expected}
    actual_keys = {_title_identity(title) for title in actual}
    primary_keys = {_title_identity(title) for title in primary_titles}
    if season > 1 and not explicit and not (primary_keys & actual_keys):
        return False
    return bool((expected_keys - {""}) & actual_keys)


def _watch_url(base_url: str, href: str) -> str | None:
    url = urllib.parse.urljoin(base_url + "/", href)
    parsed = urllib.parse.urlsplit(url)
    base = urllib.parse.urlsplit(base_url)
    if parsed.scheme != base.scheme or parsed.netloc.lower() != base.netloc.lower():
        return None
    if not parsed.path.startswith("/watch/"):
        return None
    return urllib.parse.urlunsplit(parsed._replace(fragment=""))


def _page_titles(soup: BeautifulSoup) -> tuple[str, ...]:
    heading = soup.select_one("#watch-main h1, main h1, h1")
    if heading is None:
        return ()
    return tuple(dict.fromkeys(value for value in (
        heading.get_text(" ", strip=True), heading.get("data-jp"), heading.get("title"),
    ) if isinstance(value, str) and value.strip()))


def _page_id(soup: BeautifulSoup) -> str | None:
    primary = soup.select_one("#watch-main[data-id], main[data-id]")
    value = primary.get("data-id") if primary else None
    return value if isinstance(value, str) and value.isdigit() else None


def _same_site_response(response: Any, base_url: str) -> bool:
    final_url = response.geturl() if callable(getattr(response, "geturl", None)) else None
    if not isinstance(final_url, str):
        return True
    final, base = urllib.parse.urlsplit(final_url), urllib.parse.urlsplit(base_url)
    return final.scheme == base.scheme and final.netloc.lower() == base.netloc.lower()


def _catalog_mal_matches(soup: BeautifulSoup, media: dict[str, Any] | None) -> bool:
    expected = str((media or {}).get("idMal") or "")
    if not expected.isdigit():
        return True
    ids = {a.get("data-mal") for a in soup.select("a[data-num], a[data-number], a[data-slug]")}
    return ids == {expected}


def _rc4(key: bytes, data: bytes) -> bytes:
    s = list(range(256))
    j = 0
    out = bytearray(len(data))
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256
        s[i], s[j] = s[j], s[i]
    i = j = 0
    for k in range(len(data)):
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        out[k] = data[k] ^ s[(s[i] + s[j]) % 256]
    return bytes(out)


def _b64_url_safe(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode("utf-8")


def _exchange(input_str: str, key1: str, key2: str) -> str:
    res = []
    for c in input_str:
        idx = key1.find(c)
        res.append(key2[idx] if idx != -1 else c)
    return "".join(res)


def _vrf_encrypt(input_val: str) -> str:
    if not input_val:
        return ""
    try:
        vrf = str(input_val)
        vrf = _exchange(vrf, _EXCHANGE_KEY_1[0], _EXCHANGE_KEY_1[1])
        vrf = _b64_url_safe(_rc4(_KEY_1.encode("utf-8"), vrf.encode("utf-8")))
        vrf = _b64_url_safe(_rc4(_KEY_2.encode("utf-8"), vrf.encode("utf-8")))
        vrf = _exchange(vrf, _EXCHANGE_KEY_2[0], _EXCHANGE_KEY_2[1])
        vrf = _exchange(vrf, _EXCHANGE_KEY_3[0], _EXCHANGE_KEY_3[1])
        vrf = vrf[::-1]
        vrf = _b64_url_safe(_rc4(_KEY_3.encode("utf-8"), vrf.encode("utf-8")))
        vrf = _b64_url_safe(vrf.encode("utf-8"))
        return urllib.parse.quote(vrf, safe="")
    except Exception as e:
        _logger.debug("Anikoto VRF encrypt error: %s", e)
        return ""


def _decrypt_megaplay_enc(enc: str) -> dict | None:
    if not enc:
        return None
    try:
        import base64
        enc_clean = enc.replace("-", "+").replace("_", "/")
        rem = len(enc_clean) % 4
        if rem:
            enc_clean += "=" * (4 - rem)
        ciphertext = base64.b64decode(enc_clean)

        plaintext = None
        try:
            from cryptography.hazmat.backends import default_backend
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            cipher = Cipher(algorithms.AES(_MEGAPLAY_KEY), modes.CBC(_MEGAPLAY_IV), backend=default_backend())
            decryptor = cipher.decryptor()
            plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        except Exception:
            for lib in ("Cryptodome", "Crypto"):
                try:
                    AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
                    cipher = AES.new(_MEGAPLAY_KEY, AES.MODE_CBC, _MEGAPLAY_IV)
                    plaintext = cipher.decrypt(ciphertext)
                    break
                except ImportError:
                    continue
                except Exception:
                    pass

        if not plaintext:
            return None

        pad_len = plaintext[-1]
        if 0 < pad_len <= 16:
            plaintext = plaintext[:-pad_len]
        return json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        _logger.debug("Failed to decrypt MegaPlay enc: %s", e)
        return None


class AnikotoProvider:
    id = "anikoto"
    audio_mode = "separate_catalogs"
    reuse_session_instance = True

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, 'metadata'):
            self.metadata = {}
        if not hasattr(self, 'domains'):
            self.domains = []
        self.anikoto_url = "https://anikototv.to"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
        }
        self._cache_lock = RLock()
        self._cache_generation = 0
        self._anime_page_cache: dict[str, tuple[str, str]] = {}
        self._media_cache: dict[str, dict[str, Any]] = {}
        self._cache_scope = (self.anikoto_url, self.base_url, self._request_json)

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://megaplay.buzz"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Anikoto")

    def _ensure_cache_scope(self) -> None:
        with self._cache_lock:
            scope = (self.anikoto_url, self.base_url, self._request_json)
            if scope[:2] != self._cache_scope[:2] or scope[2] is not self._cache_scope[2]:
                self._cache_generation += 1
                self._anime_page_cache.clear()
                self._media_cache.clear()
                self._cache_scope = scope

    def _cache_epoch(self) -> int:
        with self._cache_lock:
            self._ensure_cache_scope()
            return self._cache_generation

    def _remember_media(
        self, provider_id: str, media: dict[str, Any], *, generation: int | None = None,
    ) -> dict[str, Any]:
        # Only public fields survive session reuse, including across auth changes.
        public = {key: value for key, value in media.items() if key in _PUBLIC_MEDIA_FIELDS}
        with self._cache_lock:
            self._ensure_cache_scope()
            if generation is None or generation == self._cache_generation:
                self._media_cache[provider_id] = public
                if "id" in public:
                    self._media_cache[str(public["id"])] = public
        return public

    def _remember_anime_page(
        self, provider_id: str, anime_id: str, watch_url: str, generation: int,
        media: dict[str, Any] | None = None,
    ) -> None:
        with self._cache_lock:
            self._ensure_cache_scope()
            if generation == self._cache_generation:
                self._anime_page_cache[provider_id] = (anime_id, watch_url)
                if media is not None and "id" in media:
                    self._anime_page_cache[str(media["id"])] = (anime_id, watch_url)

    def _invalidate_anime_page(self, anime_id: str, *, generation: int | None = None) -> None:
        with self._cache_lock:
            self._ensure_cache_scope()
            if generation is not None and generation != self._cache_generation:
                return
            keys = [key for key, mapping in self._anime_page_cache.items() if mapping[0] == anime_id]
            if keys:
                self._cache_generation += 1
            for key in keys:
                self._anime_page_cache.pop(key, None)

    def invalidate_title_cache(self, provider_id: str) -> None:
        """Discard one title's public metadata and identity before a refresh."""
        with self._cache_lock:
            self._ensure_cache_scope()
            # Even a first lookup with no cached entry must not refill the cache
            # after a refresh has begun. Existing unrelated entries stay intact.
            self._cache_generation += 1
            keys = {str(provider_id).strip()}
            mapping = self._anime_page_cache.get(str(provider_id).strip())
            if mapping:
                keys.update(key for key, value in self._anime_page_cache.items() if value[0] == mapping[0])
            media_ids = {
                str(media["id"]) for key, media in self._media_cache.items()
                if key in keys and "id" in media
            }
            keys.update(key for key, media in self._media_cache.items() if str(media.get("id")) in media_ids)
            site_ids = {value[0] for key, value in self._anime_page_cache.items() if key in keys}
            keys.update(key for key, value in self._anime_page_cache.items() if value[0] in site_ids)
            for key in keys:
                self._media_cache.pop(key, None)
                self._anime_page_cache.pop(key, None)

    def _episode_page(
        self, anime_id: str, watch_url: str, media: dict[str, Any] | None, *, generation: int | None = None,
    ) -> BeautifulSoup | None:
        generation = self._cache_epoch() if generation is None else generation
        try:
            soup = self._request_episode_page(anime_id, watch_url, media)
        except Exception:
            self._invalidate_anime_page(anime_id, generation=generation)
            raise
        if soup is None:
            self._invalidate_anime_page(anime_id, generation=generation)
        return soup

    def _request_episode_page(self, anime_id: str, watch_url: str, media: dict[str, Any] | None) -> BeautifulSoup | None:
        ep_url = f"{self.anikoto_url}/ajax/episode/list/{anime_id}?vrf={_vrf_encrypt(anime_id)}"
        request = urllib.request.Request(ep_url, headers={**self.headers, "Referer": watch_url,
                                                          "X-Requested-With": "XMLHttpRequest"})
        response = urllib.request.urlopen(request, timeout=6)
        if not _same_site_response(response, self.anikoto_url):
            _logger.warning("Anikoto rejected off-site episode catalog redirect: site_id=%s", anime_id)
            return None
        data = json.loads(response.read().decode("utf-8"))
        html = data.get("result") if isinstance(data, dict) else None
        if not isinstance(html, str):
            _logger.warning("Anikoto malformed episode catalog: site_id=%s", anime_id)
            return None
        soup = BeautifulSoup(html, "html.parser")
        if not soup.select("a[data-num], a[data-number], a[data-slug]"):
            _logger.warning("Anikoto empty episode catalog: site_id=%s", anime_id)
            return None
        if not _catalog_mal_matches(soup, media):
            _logger.warning("Anikoto rejected MAL identity: AniList=%s expected_MAL=%s site_id=%s",
                            (media or {}).get("id"), (media or {}).get("idMal"), anime_id)
            return None
        return soup

    def _fetch_media(self, provider_id: str) -> dict[str, Any] | None:
        pid_clean = str(provider_id).strip()
        with self._cache_lock:
            self._ensure_cache_scope()
            generation = self._cache_generation
            cached = self._media_cache.get(pid_clean)
            if cached is not None:
                return cached

        token = ""
        try:
            from ..core.storage import load_config
            from ..services.anilist_auth import stored_anilist_token
            token = stored_anilist_token(load_config())
        except Exception:
            token = ""

        # Numeric ID: fetch directly from AniList
        if pid_clean.isdigit():
            if token:
                try:
                    res = anilist.fetch_one(urllib.request.urlopen, json.load, token, anilist_id=int(pid_clean))
                    if res:
                        return self._remember_media(pid_clean, res, generation=generation)
                except Exception as e:
                    _logger.debug("Anikoto fetch_one with token failed (likely expired/invalid): %s", e)

            try:
                res = anilist.fetch_one(urllib.request.urlopen, json.load, "", anilist_id=int(pid_clean))
                if res:
                    return self._remember_media(pid_clean, res, generation=generation)
            except Exception as e:
                _logger.debug("Anikoto fetch_one unauthenticated failed: %s", e)
        else:
            # Slug or title passed as provider_id: search AniList fallback
            try:
                from ..core.anilist_fallback import search_anilist_with_fallback
                fallback_res = search_anilist_with_fallback(pid_clean)
                media_list = fallback_res.get("data", {}).get("Page", {}).get("media", [])
                if media_list:
                    res = media_list[0]
                    return self._remember_media(pid_clean, res, generation=generation)
            except Exception as e:
                _logger.debug("Anikoto fallback title search failed: %s", e)

        with self._cache_lock:
            self._ensure_cache_scope()
            return self._media_cache.get(pid_clean)

    def _get_anime_page(
        self, provider_id: str, *,
        _verified_catalogs: dict[tuple[str, str], BeautifulSoup] | None = None,
    ) -> tuple[str | None, str | None]:
        pid_clean = str(provider_id).strip()
        with self._cache_lock:
            self._ensure_cache_scope()
            generation = self._cache_generation
            cached = self._anime_page_cache.get(pid_clean)
            if cached is not None:
                return cached

        # An explicit site URL is already a selection, not an AniList-ID guess.
        if pid_clean.startswith("/watch/") or pid_clean.startswith("http"):
            watch_url = _watch_url(self.anikoto_url, pid_clean)
            if not watch_url:
                return None, None
            try:
                req_watch = urllib.request.Request(watch_url, headers={**self.headers, "Referer": f"{self.anikoto_url}/"})
                response = urllib.request.urlopen(req_watch, timeout=6)
                if not _same_site_response(response, self.anikoto_url):
                    _logger.warning("Anikoto rejected off-site watch redirect: %s", watch_url)
                    return None, None
                w_html = response.read().decode("utf-8", errors="ignore")
                w_soup = BeautifulSoup(w_html, "html.parser")
                anime_id = _page_id(w_soup)
                if anime_id:
                    self._remember_anime_page(pid_clean, anime_id, watch_url, generation)
                    return anime_id, watch_url
            except (urllib.error.URLError, OSError, HTTPException) as e:
                _logger.debug("Anikoto direct watch_url resolve failed: %s", e)
            return None, None

        media = self._fetch_media(provider_id)
        if not media:
            return None, None

        names = media.get("title") or {}
        primary_titles = tuple(value for value in (
            (names.get(k) for k in ("english", "romaji", "native", "userPreferred"))
            if isinstance(names, dict) else (names,)
        ) if isinstance(value, str) and value.strip())
        titles = tuple(dict.fromkeys((*primary_titles, *(alt for alt in media.get("synonyms") or ()
                                                        if isinstance(alt, str) and alt.strip()))))
        if not titles:
            return None, None
        season = max(_season_from_title(title) for title in titles)
        queries: dict[str, str] = {}
        for title in titles:
            query = " ".join(re.sub(r"[^\w\s]", " ", title).split())
            if query:
                queries.setdefault(query.casefold(), query)
        # Exhausting every synonym can delay playback for minutes on no match.
        query_terms = tuple(queries.values())[:_MAX_IDENTITY_QUERIES]
        native_query = " ".join(re.sub(r"[^\w\s]", " ", names.get("native") or "").split()) if isinstance(names, dict) else ""
        checked_pages = set()
        for query in query_terms:
            if not query:
                continue
            search_url = f"{self.anikoto_url}/filter?keyword={urllib.parse.quote_plus(query)}"
            vrf = _vrf_encrypt(query)
            urls = (f"{search_url}&vrf={vrf}", search_url) if vrf else (search_url,)
            html_search = ""
            for url in urls:
                try:
                    req_search = urllib.request.Request(url, headers={**self.headers, "Referer": f"{self.anikoto_url}/"})
                    response = urllib.request.urlopen(req_search, timeout=6)
                    if not _same_site_response(response, self.anikoto_url):
                        _logger.warning("Anikoto rejected off-site search redirect: %s", url)
                        continue
                    html_search = response.read().decode('utf-8', errors='ignore')
                    break
                except (urllib.error.URLError, OSError, HTTPException) as search_err:
                    _logger.debug("Anikoto search URL failed (%s): %s", url, search_err)
            if not html_search:
                continue
            soup_search = BeautifulSoup(html_search, "html.parser")
            candidates: dict[str, tuple[set[str], str, str | None]] = {}
            for a in soup_search.find_all("a", href=True):
                href = a.get("href")
                if not isinstance(href, str):
                    continue
                watch_url = _watch_url(self.anikoto_url, href)
                if not watch_url:
                    continue
                # Poster and name anchors point to the same show. Merge their
                # aliases instead of discarding the original title on the latter.
                series_key = watch_url.split("/ep-", 1)[0].rstrip("/")
                aliases, first_url, site_id = candidates.get(series_key, (set(), watch_url, None))
                container = a.find_parent(class_="inner")
                poster = a.find_parent(class_="poster") or (container.select_one(".poster[data-tip]") if container else None)
                card_id = poster.get("data-tip") if poster else None
                if isinstance(card_id, str) and card_id.isdigit():
                    site_id = card_id
                candidates[series_key] = (aliases, first_url, site_id)
                image = a.find("img")
                visible = a.get_text(" ", strip=True)
                if re.match(r"^\d+(?:\s+\d+)*\s+(?:TV|Movie|Special|OVA|ONA)\b", visible, re.I):
                    visible = ""
                values = (a.get("data-jp"), a.get("title"),
                          image.get("alt") if image else visible,
                          _slug_title_from_watch_href(watch_url))
                aliases.update(value for value in values if isinstance(value, str) and value.strip())
            matches = [(url, tuple(aliases)) for aliases, url, _ in candidates.values()
                       if _matching_titles(titles, tuple(aliases), season, media.get("format") or "", primary_titles)]
            _logger.debug("Anikoto identity search: AniList=%s query=%r candidates=%d exact_matches=%d",
                          pid_clean, query, len(candidates), len(matches))
            if len(matches) > 1:
                _logger.warning("Anikoto ambiguous identity for AniList %s: %s", pid_clean,
                                tuple(url for url, _ in matches))
                return None, None
            verified_site_id = None
            verified_catalog = None
            if not matches and media.get("idMal") and query.casefold() == native_query.casefold():
                # Site translations can omit every AniList alias. A small native
                # search probe may establish identity by MAL ID, never word overlap.
                for aliases, candidate_url, candidate_id in tuple(candidates.values())[:_MAX_NATIVE_ID_PROBES]:
                    if not candidate_id:
                        continue
                    try:
                        soup = self._episode_page(candidate_id, candidate_url, media, generation=generation)
                    except (urllib.error.URLError, OSError, HTTPException, json.JSONDecodeError) as exc:
                        _logger.debug("Anikoto native identity probe failed: %s", exc)
                        continue
                    if soup is not None:
                        matches = [(candidate_url, tuple(aliases))]
                        verified_site_id = candidate_id
                        verified_catalog = soup
                        break
            if not matches:
                continue
            watch_url, matched_titles = matches[0]
            if watch_url in checked_pages:
                continue
            checked_pages.add(watch_url)
            try:
                req_watch = urllib.request.Request(watch_url, headers={**self.headers, "Referer": f"{self.anikoto_url}/"})
                response = urllib.request.urlopen(req_watch, timeout=6)
                if not _same_site_response(response, self.anikoto_url):
                    _logger.warning("Anikoto rejected off-site watch redirect: %s", watch_url)
                    continue
                w_html = response.read().decode('utf-8', errors='ignore')
                w_soup = BeautifulSoup(w_html, "html.parser")
                anime_id = _page_id(w_soup)
                title_matches = _matching_titles(titles, _page_titles(w_soup), season, media.get("format") or "", primary_titles)
                if anime_id and (title_matches or anime_id == verified_site_id):
                    if verified_site_id is not None and anime_id != verified_site_id:
                        continue
                    if media.get("idMal") and verified_site_id is None:
                        verified_catalog = self._episode_page(anime_id, watch_url, media, generation=generation)
                        if verified_catalog is None:
                            continue
                    self._remember_anime_page(pid_clean, anime_id, watch_url, generation, media)
                    if _verified_catalogs is not None and verified_catalog is not None:
                        _verified_catalogs[(anime_id, watch_url)] = verified_catalog
                    _logger.debug("Anikoto verified identity: AniList=%s MAL=%s site_id=%s page=%s titles=%r",
                                  pid_clean, media.get("idMal"), anime_id, watch_url, _page_titles(w_soup))
                    return anime_id, watch_url
                _logger.warning("Anikoto rejected watch identity: AniList=%s site_id=%s page=%s titles=%r",
                                pid_clean, anime_id, watch_url, _page_titles(w_soup))
            except (urllib.error.URLError, OSError, HTTPException, json.JSONDecodeError) as exc:
                _logger.debug("Anikoto watch identity request failed: %s", exc)
        _logger.warning("Anikoto could not verify a website identity for AniList %s; not guessing", pid_clean)
        return None, None

    def search(self, query: str, ttype: str = "sub") -> list[dict[str, Any]]:
        generation = self._cache_epoch()
        try:
            from ..core.anilist_fallback import search_anilist_with_fallback
            res = search_anilist_with_fallback(query)
            media_list = res.get("data", {}).get("Page", {}).get("media", [])
            results = []
            for media in media_list:
                if "id" in media:
                    self._remember_media(str(media["id"]), media, generation=generation)
                titles = media.get("title") or {}
                main_name = titles.get("romaji") or titles.get("english") or "Unknown"
                ep_count = media.get("episodes") or 0
                results.append({
                    "_id": str(media["id"]),
                    "name": main_name,
                    "romajiName": titles.get("romaji") or "",
                    "englishName": titles.get("english") or "",
                    "nativeName": titles.get("native") or "",
                    "altNames": list(media.get("synonyms") or []),
                    "type": media.get("format") or "TV",
                    "format": media.get("format"),
                    "status": media.get("status"),
                    "season": {
                        "year": media.get("seasonYear"),
                        "name": media.get("season"),
                    },
                    "episodeCount": media.get("episodes"),
                    "score": media.get("averageScore"),
                    "genres": media.get("genres", []),
                    "description": media.get("description", ""),
                    "_metadata_fields_loaded": tuple(key for key in ("description", "genres") if key in media),
                    "availableEpisodes": {
                        "sub": ep_count,
                        "dub": ep_count if ttype == "dub" else 0,
                        "raw": 0,
                    },
                    "thumbnail": (media.get("coverImage") or {}).get("large"),
                    "banner": media.get("bannerImage"),
                    "aniListId": str(media["id"]),
                    "malId": media.get("idMal"),
                })
            return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="_id")
        except Exception as e:
            _logger.debug("Anikoto search error: %s", e)
            return []

    def get_title(self, provider_id: str) -> dict[str, Any] | None:
        try:
            media = self._fetch_media(provider_id)
            if not media:
                return None
            title_data = anilist_normalize.normalize_media(media)
            if title_data:
                title_data["aniListId"] = str(media["id"])
                eps = title_data.get("availableEpisodes", {})
                eps["dub"] = eps.get("sub", 0)
                loaded = tuple(key for key in ("description", "genres") if key in media)
                if loaded:
                    title_data["_metadata_fields_loaded"] = loaded
            return normalize_title(title_data, provider_id=self.id, provider_name=self.name, id_key="_id")
        except Exception as e:
            _logger.debug("Anikoto get_title error: %s", e)
            return None

    def _episode_identity(
        self, provider_id: str,
        verified_catalogs: dict[tuple[str, str], BeautifulSoup] | None = None,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        direct = str(provider_id).strip().startswith(("/watch/", "http"))
        media = None if direct else self._fetch_media(provider_id)
        if media is None and not direct:
            self.invalidate_title_cache(provider_id)
            return None, None, None
        # Retain identity fields for this operation even if a concurrent refresh
        # removes or replaces the session's cached media before the response.
        media = dict(media) if media is not None else None
        if verified_catalogs is None:
            anime_id, watch_url = self._get_anime_page(provider_id)
        else:
            anime_id, watch_url = self._get_anime_page(provider_id, _verified_catalogs=verified_catalogs)
        return anime_id, watch_url, media

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict[str, Any]:
        try:
            # 1. Try resolving actual episode catalog from Anikoto website
            verified_catalogs: dict[tuple[str, str], BeautifulSoup] = {}
            anime_id, watch_url, media = self._episode_identity(provider_id, verified_catalogs)
            if anime_id and watch_url:
                # This handoff lives only for this call. Later catalog/source
                # requests must fetch and validate their own episode response.
                ep_soup = verified_catalogs.get((anime_id, watch_url))
                if ep_soup is None:
                    ep_soup = self._episode_page(anime_id, watch_url, media)
                if ep_soup is None:
                    # Continue through the existing metadata fallback, not an unverified catalog.
                    ep_soup = BeautifulSoup("", "html.parser")

                sub_ids = []
                dub_ids = []
                labels = {}
                for a in ep_soup.select("div.episodes ul > li > a") or ep_soup.find_all("a"):
                    num = a.get("data-num") or a.get("data-number") or a.get("data-slug")
                    if not num:
                        continue
                    num_str = str(num)
                    is_sub = a.get("data-sub") == "1"
                    is_dub = a.get("data-dub") == "1"
                    if not is_sub and not is_dub:
                        is_sub = True
                    if is_sub:
                        sub_ids.append(num_str)
                    if is_dub:
                        dub_ids.append(num_str)

                    title_span = a.find("span", class_="d-title")
                    tooltip = (a.parent or {}).get("title", "") if hasattr(a, "parent") and a.parent else ""
                    ep_name = title_span.text.strip() if title_span and title_span.text.strip() else ""
                    if not ep_name and tooltip:
                        ep_name = tooltip.split("Release:")[0].split("Softsub")[0].strip()
                    labels[num_str] = f"{num_str}. {ep_name}" if ep_name and ep_name != num_str else num_str

                if sub_ids or dub_ids:
                    active_ids = dub_ids if ttype.lower() == "dub" else sub_ids
                    if not active_ids:
                        active_ids = sub_ids or dub_ids
                    detail = {
                        "sub": sub_ids,
                        "dub": dub_ids,
                        "raw": [],
                    }
                    return normalize_episode_catalog({"ids": active_ids, "detail": detail, "labels": labels}, provider_id=self.id, provider_title_id=provider_id)
        except Exception as e:
            _logger.debug("Anikoto site episode catalog resolution failed: %s", e)

        # 2. Fallback to AniList metadata
        try:
            media = self._fetch_media(provider_id)
            if not media:
                return normalize_episode_catalog({"ids": []}, provider_id=self.id, provider_title_id=provider_id)

            total = media.get("episodes")
            if not total:
                next_airing = media.get("nextAiringEpisode")
                if next_airing and next_airing.get("episode"):
                    total = next_airing["episode"] - 1
                else:
                    total = 1

            ids = [str(ep) for ep in range(1, total + 1)]
            detail = {
                "sub": ids,
                "dub": ids,
                "raw": [],
            }
            return normalize_episode_catalog({"ids": ids, "detail": detail}, provider_id=self.id, provider_title_id=provider_id)
        except Exception as e:
            _logger.debug("Anikoto episode_catalog error: %s", e)
            return normalize_episode_catalog({"ids": []}, provider_id=self.id, provider_title_id=provider_id)

    def _extract_single_server(self, s_name: str, link_id: str, watch_url: str, cat_label: str = "") -> list[dict[str, Any]]:
        results = []
        try:
            emb_api = f"{self.anikoto_url}/ajax/server?get={urllib.parse.quote(link_id)}"
            req_emb_api = urllib.request.Request(emb_api, headers={**self.headers, "Referer": watch_url, "X-Requested-With": "XMLHttpRequest"})
            emb_res = json.loads(urllib.request.urlopen(req_emb_api, timeout=5).read().decode('utf-8'))
            embed_url = emb_res.get("result", {}).get("url")
            if not embed_url:
                return []

            parsed_emb = urllib.parse.urlparse(embed_url)
            host = parsed_emb.netloc
            qs = urllib.parse.parse_qs(parsed_emb.query)

            req_emb_page = urllib.request.Request(embed_url, headers={"User-Agent": self.headers["User-Agent"], "Referer": f"{self.anikoto_url}/"})
            emb_page_html = urllib.request.urlopen(req_emb_page, timeout=5).read().decode('utf-8', errors='ignore')
            m_did = re.search(r'data-id=[\"\'](\d+)[\"\']', emb_page_html)
            if not m_did:
                return []
            d_id = m_did.group(1)

            stream_url = None
            tracks = []
            embed_referer = f"https://{host}/"

            if "vidtube" in host:
                src_api = f"https://vidtube.site/stream/getSources?id={d_id}"
                req_src = urllib.request.Request(src_api, headers={
                    "User-Agent": self.headers["User-Agent"],
                    "Referer": embed_url,
                    "Origin": "https://vidtube.site",
                    "X-Requested-With": "XMLHttpRequest"
                })
                src_res = json.loads(urllib.request.urlopen(req_src, timeout=5).read().decode('utf-8'))
                sources = src_res.get("sources", {})
                stream_url = sources.get("file") if isinstance(sources, dict) else (sources[0].get("file") if sources else None)
                tracks = src_res.get("tracks", [])
            elif "megaplay" in host:
                s_param = qs.get("s", [""])[0]
                if not s_param:
                    s_param = "tcdn"

                def _fetch_megaplay_sources(target_s: str) -> dict | None:
                    api = f"https://megaplay.buzz/stream/getSourcesNew?id={d_id}"
                    if target_s:
                        api += f"&s={target_s}"
                    req = urllib.request.Request(api, headers={
                        "User-Agent": self.headers["User-Agent"],
                        "Referer": embed_url,
                        "Origin": "https://megaplay.buzz",
                        "X-Requested-With": "XMLHttpRequest"
                    })
                    try:
                        return json.loads(urllib.request.urlopen(req, timeout=6).read().decode('utf-8'))
                    except Exception:
                        api_old = f"https://megaplay.buzz/stream/getSources?id={d_id}"
                        if target_s:
                            api_old += f"&s={target_s}"
                        req_old = urllib.request.Request(api_old, headers={
                            "User-Agent": self.headers["User-Agent"],
                            "Referer": embed_url,
                            "Origin": "https://megaplay.buzz",
                            "X-Requested-With": "XMLHttpRequest"
                        })
                        try:
                            return json.loads(urllib.request.urlopen(req_old, timeout=6).read().decode('utf-8'))
                        except Exception:
                            return None

                src_res = None
                try:
                    src_res = _fetch_megaplay_sources(s_param)
                except Exception:
                    src_res = None

                if not src_res and s_param != "tcdn":
                    try:
                        src_res = _fetch_megaplay_sources("tcdn")
                    except Exception:
                        src_res = None

                if not src_res:
                    return []

                stream_url = None
                if src_res.get("enc"):
                    dec = _decrypt_megaplay_enc(src_res["enc"])
                    if dec and isinstance(dec, dict):
                        stream_url = dec.get("file") or dec.get("url")

                if not stream_url:
                    sources = src_res.get("sources", [])
                    if isinstance(sources, dict):
                        stream_url = sources.get("file")
                    elif isinstance(sources, list) and sources:
                        stream_url = sources[0].get("file") if isinstance(sources[0], dict) else None

                tracks = src_res.get("tracks", [])

                # If primary endpoint returned the blocked fetch.nexabloom.top domain,
                # retry with s=tcdn which yields working CDN hosts (megap.*)
                if not stream_url or "fetch.nexabloom.top" in stream_url:
                    try:
                        src_res_tcdn = _fetch_megaplay_sources("tcdn")
                        if src_res_tcdn and src_res_tcdn.get("enc"):
                            dec_tcdn = _decrypt_megaplay_enc(src_res_tcdn["enc"])
                            if dec_tcdn and isinstance(dec_tcdn, dict) and dec_tcdn.get("file"):
                                stream_url = dec_tcdn["file"]
                                if not tracks:
                                    tracks = src_res_tcdn.get("tracks", [])
                    except Exception:
                        pass

            if not stream_url:
                return []

            cat_l = (cat_label or "").lower()
            is_hardsub = "hardsub" in cat_l or "hsub" in cat_l

            subtitles = []
            if not is_hardsub:
                for track in tracks:
                    if isinstance(track, dict) and track.get("file"):
                        subtitles.append({
                            "url": track["file"],
                            "label": track.get("label", "Unknown"),
                            "kind": track.get("kind", "captions"),
                            "default": bool(track.get("default")),
                        })
            default_sub = next((s["url"] for s in subtitles if s.get("default")), None)
            if not default_sub and subtitles:
                default_sub = next((s["url"] for s in subtitles if "eng" in s.get("label", "").lower()), None)

            n = s_name.lower()
            if "hd-2" in n:
                priority_val = 1
            elif "vidstream-2" in n:
                priority_val = 3
            elif "vidstream" in n or "vidplay" in n:
                priority_val = 4
            elif "hd-1" in n:
                priority_val = 10
            else:
                priority_val = 5

            if is_hardsub:
                priority_val += 1

            display_name = f"[{cat_label}] {s_name}" if cat_label else s_name

            is_megaplay = (
                "megap." in stream_url
                or "akirax.buzz" in stream_url
                or "tiktokcdn" in stream_url
                or "megaplay" in host
                or "vidstream" in s_name.lower()
            )

            # Master playlist variant parsing
            parsed_variants = False
            if stream_url.endswith(".m3u8"):
                try:
                    req_m = urllib.request.Request(stream_url, headers={
                        "User-Agent": self.headers["User-Agent"],
                        "Referer": embed_referer
                    })
                    m_txt = urllib.request.urlopen(req_m, timeout=5).read().decode('utf-8')
                    if "#EXT-X-STREAM-INF" in m_txt:
                        lines = m_txt.splitlines()
                        variants = []
                        for i, line in enumerate(lines):
                            if line.startswith("#EXT-X-STREAM-INF"):
                                res_match = re.search(r'RESOLUTION=\d+x(\d+)', line)
                                res_int = int(res_match.group(1)) if res_match else 0
                                quality = f"{res_int}p" if res_int else "auto"
                                if i + 1 < len(lines):
                                    uri = lines[i+1].strip()
                                    if uri and not uri.startswith("#"):
                                        v_url = urllib.parse.urljoin(stream_url, uri)
                                        v_entry = {
                                            "sourceName": f"{display_name} ({quality})",
                                            "streamUrl": v_url,
                                            "type": "hls",
                                            "priority": priority_val,
                                            "resolution": str(res_int) if res_int else "auto",
                                            "sort_key": res_int,
                                            "referer": embed_referer,
                                            "headers": {
                                                "Referer": embed_referer,
                                                "Origin": embed_referer.rstrip("/"),
                                                "User-Agent": self.headers["User-Agent"]
                                            }
                                        }
                                        if is_megaplay or "megap." in v_url or "akirax.buzz" in v_url:
                                            v_entry["requires_proxy"] = True
                                        if subtitles:
                                            v_entry["subtitles"] = subtitles
                                        if default_sub:
                                            v_entry["subtitle_url"] = default_sub
                                        variants.append(v_entry)
                        if variants:
                            variants.sort(key=lambda x: x["sort_key"], reverse=True)
                            for v in variants:
                                del v["sort_key"]
                                results.append(v)
                            parsed_variants = True
                except Exception as parse_e:
                    _logger.debug("Failed to parse variants for %s: %s", s_name, parse_e)

            auto_entry = {
                "sourceName": f"{display_name} (Auto)" if parsed_variants else display_name,
                "streamUrl": stream_url,
                "type": "hls" if stream_url.endswith(".m3u8") else "mp4",
                "priority": priority_val,
                "resolution": "auto",
                "referer": embed_referer,
                "headers": {
                    "Referer": embed_referer,
                    "Origin": embed_referer.rstrip("/"),
                    "User-Agent": self.headers["User-Agent"]
                }
            }
            if is_megaplay:
                auto_entry["requires_proxy"] = True
            if subtitles:
                auto_entry["subtitles"] = subtitles
            if default_sub:
                auto_entry["subtitle_url"] = default_sub
            results.append(auto_entry)
        except Exception as s_err:
            _logger.debug("Anikoto server %s extraction error: %s", s_name, s_err)
        return results

    def episode_sources(
        self,
        provider_id: str,
        episode: str,
        ttype: str = "sub",
    ) -> dict[str, Any] | None:
        source_urls = []
        seen_urls = set()

        # 1. Multi-Server Extraction via anikototv.to
        try:
            anime_id, watch_url, media = self._episode_identity(provider_id)

            if anime_id and watch_url:
                ep_soup = self._episode_page(anime_id, watch_url, media)
                if ep_soup is None:
                    ep_soup = BeautifulSoup("", "html.parser")

                target_a = None
                for a in ep_soup.find_all("a"):
                    if a.get("data-number") == str(episode) or a.get("data-num") == str(episode) or a.get("data-slug") == str(episode):
                        target_a = a
                        break
                # List position is not an episode identity. Unlabelled links may
                # be controls or other content, so use the ID-based fallback.
                if target_a and target_a.get("data-ids"):
                    data_ids = target_a.get("data-ids")
                    sv_url = f"{self.anikoto_url}/ajax/server/list?servers={urllib.parse.quote(data_ids)}"
                    req_sv = urllib.request.Request(sv_url, headers={**self.headers, "Referer": watch_url, "X-Requested-With": "XMLHttpRequest"})
                    sv_res = json.loads(urllib.request.urlopen(req_sv, timeout=6).read().decode('utf-8'))
                    sv_soup = BeautifulSoup(sv_res.get("result", ""), "html.parser")

                    server_items = []
                    target_types = []
                    if ttype.lower() == "sub":
                        target_types = [("sub", "Softsub"), ("hsub", "Hardsub"), ("raw", "RAW")]
                    else:
                        target_types = [("dub", "Dub")]

                    found_types = False
                    for dtype, cat_label in target_types:
                        type_div = sv_soup.find("div", class_="type", attrs={"data-type": dtype})
                        if type_div:
                            found_types = True
                            for li in type_div.find_all("li"):
                                s_name = li.text.strip()
                                link_id = li.get("data-link-id")
                                if link_id:
                                    server_items.append((s_name, link_id, cat_label))

                    if not found_types or not server_items:
                        for li in sv_soup.find_all("li"):
                            s_name = li.text.strip()
                            link_id = li.get("data-link-id")
                            if link_id:
                                server_items.append((s_name, link_id, ttype.capitalize()))

                    def _server_rank(item):
                        n = item[0].lower()
                        cat = (item[2] if len(item) > 2 else "").lower()
                        base = 0
                        if "hd-2" in n:
                            base = 0
                        elif "vidstream-2" in n:
                            base = 10
                        elif "vidstream" in n or "vidplay" in n:
                            base = 12
                        elif "hd-1" in n:
                            base = 90
                        else:
                            base = 20
                        if "hardsub" in cat or "hsub" in cat:
                            base += 1
                        return base

                    server_items.sort(key=_server_rank)

                    if server_items:
                        try:
                            from media_cli import app_core
                            s_names = ", ".join(f"{s[0]} [{s[2]}]" if len(s) > 2 and s[2] else s[0] for s in server_items[:6])
                            app_core.info(f"[{self.name}] Found {len(server_items)} servers ({s_names}) • Extracting streams...")
                        except Exception:
                            pass

                        from concurrent.futures import ThreadPoolExecutor, as_completed
                        with ThreadPoolExecutor(max_workers=min(6, len(server_items))) as pool:
                            futures = {
                                pool.submit(self._extract_single_server, s_name, link_id, watch_url, cat_label): (f"[{cat_label}] {s_name}" if cat_label else s_name)
                                for s_name, link_id, cat_label in server_items
                            }
                            for fut in as_completed(futures):
                                s_display = futures[fut]
                                try:
                                    entries = fut.result()
                                    added = 0
                                    for entry in entries:
                                        dedup_key = (entry.get("sourceName"), entry.get("streamUrl"))
                                        if dedup_key not in seen_urls:
                                            seen_urls.add(dedup_key)
                                            source_urls.append(entry)
                                            added += 1
                                    if added > 0:
                                        try:
                                            from media_cli import app_core
                                            app_core.info(f"[{self.name}] Resolved {s_display} ({added} streams)")
                                        except Exception:
                                            pass
                                except Exception as err:
                                    _logger.debug("Server %s extraction failed: %s", s_display, err)

                    source_urls.sort(key=lambda s: s.get("priority", 10))
        except Exception as e:
            _logger.debug("Anikoto multi-server scraping error: %s", e)

        # 2. Fallback: Direct MegaPlay embed (if multi-server returned no sources)
        if not source_urls:
            try:
                embed_url = f"{self.base_url}/stream/ani/{provider_id}/{episode}/{ttype}"
                req_embed = urllib.request.Request(
                    embed_url,
                    headers={
                        **self.headers,
                        "Referer": f"{self.base_url}/",
                        "Accept": "text/html,application/json,text/plain,*/*"
                    }
                )
                html = urllib.request.urlopen(req_embed, timeout=7).read().decode('utf-8', errors='ignore')
                match = re.search(r'data-id=["\'](\d+)["\']', html)
                if match:
                    data_id = match.group(1)
                    api_url = f"{self.base_url}/stream/getSources?id={data_id}"
                    req_api = urllib.request.Request(
                        api_url,
                        headers={
                            **self.headers,
                            "Referer": embed_url,
                            "Origin": self.base_url,
                            "Accept": "application/json,text/plain,*/*",
                            "X-Requested-With": "XMLHttpRequest"
                        }
                    )
                    res_api = urllib.request.urlopen(req_api, timeout=7).read().decode('utf-8')
                    sources_data = json.loads(res_api)
                    enc_payload = sources_data.get("enc")
                    if enc_payload:
                        dec = _decrypt_megaplay_enc(enc_payload)
                        if isinstance(dec, dict):
                            if "tracks" in sources_data and "tracks" not in dec:
                                dec["tracks"] = sources_data["tracks"]
                            sources_data = dec
                        elif isinstance(dec, list):
                            sources_data = {"sources": dec, "tracks": sources_data.get("tracks", [])}

                    subtitles = []
                    for track in sources_data.get("tracks", []):
                        if isinstance(track, dict) and track.get("file"):
                            subtitles.append({
                                "url": track["file"],
                                "label": track.get("label", "Unknown"),
                                "kind": track.get("kind", "captions"),
                                "default": bool(track.get("default")),
                            })
                    default_sub = next((s["url"] for s in subtitles if s.get("default")), None)
                    if not default_sub and subtitles:
                        default_sub = next((s["url"] for s in subtitles if "eng" in s.get("label", "").lower()), None)

                    raw_sources = sources_data.get("sources", [])
                    if isinstance(raw_sources, dict):
                        raw_sources = [raw_sources]
                    for source in raw_sources:
                        url = source.get("file") or source.get("url")
                        fb_key = ("MegaPlay (Auto)", url)
                        if not url or fb_key in seen_urls:
                            continue
                        seen_urls.add(fb_key)
                        fb_entry = {
                            "sourceName": "MegaPlay (Auto)",
                            "streamUrl": url,
                            "type": "hls" if url.endswith(".m3u8") else "mp4",
                            "priority": 1,
                            "resolution": "auto",
                            "referer": f"{self.base_url}/",
                            "requires_proxy": True,
                            "headers": {
                                "Referer": f"{self.base_url}/",
                                "Origin": self.base_url,
                                "User-Agent": self.headers["User-Agent"]
                            }
                        }
                        if subtitles:
                            fb_entry["subtitles"] = subtitles
                        if default_sub:
                            fb_entry["subtitle_url"] = default_sub
                        source_urls.append(fb_entry)
            except Exception as fb_err:
                _logger.debug("Anikoto legacy fallback error: %s", fb_err)

        if not source_urls:
            return None

        return normalize_episode_sources(
            {"episode": {"sourceUrls": source_urls}},
            provider_id=self.id,
            provider_title_id=provider_id,
            episode=episode,
        )

    def browser_url(
        self,
        provider_id: str,
        episode: str | None = None,
        ttype: str = "sub",
        cfg: dict[str, Any] | None = None,
    ) -> str:
        if episode:
            return f"{self.base_url}/stream/ani/{provider_id}/{episode}/{ttype}"
        return f"https://anilist.co/anime/{provider_id}"

PROVIDER_CLASS = AnikotoProvider
