"""UniqueStream (AnimeStream) provider adapter (anime.uniquestream.net)."""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import http.server
import json
import logging
import math
import re
import threading
import urllib.parse
import urllib.request
from typing import Any

from ..media.sources import parse_resolution_height
from ..services.http import SSL_CTX, UA
from .shared.base import Provider
from .shared.models import (
    normalize_episode_catalog,
    normalize_episode_sources,
    normalize_titles,
)

_logger = logging.getLogger(__name__)


def _decrypt_aes_cbc(key: bytes, iv: bytes, ciphertext: bytes) -> bytes:
    """Decrypt AES-CBC ciphertext using cryptography or pycryptodome."""
    # 1. Try cryptography
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        if padded:
            pad_len = padded[-1]
            if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
                return padded[:-pad_len]
        return padded
    except ImportError:
        pass
    except Exception as e:
        _logger.debug("cryptography AES-CBC decryption failed: %s", e)

    # 2. Try pycryptodome / pycryptodomex
    for lib in ("Cryptodome", "Crypto"):
        try:
            AES = __import__(f"{lib}.Cipher", fromlist=["AES"]).AES
            cipher = AES.new(key, AES.MODE_CBC, iv)
            padded = cipher.decrypt(ciphertext)
            if padded:
                pad_len = padded[-1]
                if 1 <= pad_len <= 16 and padded.endswith(bytes([pad_len]) * pad_len):
                    return padded[:-pad_len]
            return padded
        except ImportError:
            continue
        except Exception as e:
            _logger.debug("%s AES-CBC decryption failed: %s", lib, e)
            continue

    raise RuntimeError(
        "Neither 'cryptography' nor 'pycryptodome' is installed. "
        "Install with: pip install pycryptodome (or pip install cryptography)"
    )

try:
    from curl_cffi import requests as cffi_requests
    CURL_CFFI_AVAILABLE = True
except ImportError:
    cffi_requests = None
    CURL_CFFI_AVAILABLE = False


LOCALE_NAMES: dict[str, str] = {
    "ja-JP": "Japanese",
    "en-US": "English",
    "es-419": "Spanish (LatAm)",
    "es-ES": "Spanish (Spain)",
    "pt-BR": "Portuguese",
    "fr-FR": "French",
    "de-DE": "German",
    "it-IT": "Italian",
    "ar-SA": "Arabic",
    "ru-RU": "Russian",
    "hi-IN": "Hindi",
    "ta-IN": "Tamil",
    "pl-PL": "Polish",
}

VARIANT_REGEX = re.compile(r"RESOLUTION=\d+x(\d+)[^\n]*\n([^\n#]+\.m3u8[^\n]*)")


class _ThreadedHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True


class _UniqueStreamHlsServer:
    """Local decrypting HLS proxy for UniqueStream's AES-128 encrypted streams.

    UniqueStream serves HLS media where segments are encrypted using AES-128-CBC.
    The 16-byte decryption key matches the hex bytes of the stream's media_id.
    This server rewrites the playlists and decrypts segments on-the-fly in ~2ms,
    streaming standard unencrypted MPEG-TS chunks (video/mp2t) to MPV.
    """

    def __init__(self):
        self._server: _ThreadedHTTPServer | None = None
        self._port: int = 0
        self._lock = threading.Lock()
        self._key_cache: dict[str, bytes] = {}

    @property
    def port(self) -> int:
        self.ensure_started()
        return self._port

    def ensure_started(self) -> int:
        if self._server is not None:
            return self._port

        with self._lock:
            if self._server is not None:
                return self._port

            handler_cls = self._create_handler()
            self._server = _ThreadedHTTPServer(("127.0.0.1", 0), handler_cls)
            self._port = self._server.server_port
            t = threading.Thread(target=self._server.serve_forever, daemon=True, name="UniqueStreamHlsServer")
            t.start()
            _logger.debug("UniqueStream HLS proxy server started on 127.0.0.1:%d", self._port)
            return self._port

    def _fetch_real_key(self, key_url: str, mid: str) -> bytes:
        cache_key = f"{mid}|{key_url}"
        if cache_key in self._key_cache:
            return self._key_cache[cache_key]

        req = urllib.request.Request(key_url, headers={
            "User-Agent": UA,
            "x-am-media-id": mid,
            "Referer": "https://anime.uniquestream.net/",
        })
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=12) as resp:
            body = resp.read().decode("utf-8").strip()

        ciphertext = base64.b64decode(body)
        k = hashlib.sha256(f"key{mid}".encode()).digest()[:16]
        iv = hashlib.sha256(f"iv{mid}".encode()).digest()[:16]

        recovered = _decrypt_aes_cbc(k, iv, ciphertext)

        if len(self._key_cache) > 64:
            self._key_cache.clear()
        self._key_cache[cache_key] = recovered
        return recovered

    def local_playlist_url(self, playlist_url: str, media_id: str, height: str | int | None = None) -> str:
        port = self.ensure_started()
        params = {
            "url": playlist_url,
            "mid": media_id,
        }
        if height is not None:
            params["vheight"] = str(height).rstrip("p")
        return f"http://127.0.0.1:{port}/m3u8?{urllib.parse.urlencode(params)}"

    def _create_handler(self):
        server_inst = self

        class HlsHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                parsed = urllib.parse.urlsplit(self.path)
                params = urllib.parse.parse_qs(parsed.query)

                if parsed.path == "/m3u8":
                    url = params.get("url", [None])[0]
                    mid = params.get("mid", [None])[0]
                    vheight = params.get("vheight", [None])[0]
                    if not url or not mid:
                        self.send_error(400, "Missing url or mid")
                        return

                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": UA})
                        with urllib.request.urlopen(req, context=SSL_CTX, timeout=12) as resp:
                            content = resp.read().decode("utf-8", errors="replace")

                        base_url = url.rsplit("/", 1)[0] + "/"
                        rewritten_lines = []

                        if "#EXT-X-STREAM-INF" in content:
                            lines = content.splitlines()
                            pending_stream_inf = None
                            for line in lines:
                                sline = line.strip()
                                if sline.startswith("#EXT-X-STREAM-INF:"):
                                    pending_stream_inf = sline
                                elif sline.startswith("#EXT-X-MEDIA:"):
                                    def repl_uri(m):
                                        resolved = urllib.parse.urljoin(base_url, m.group(1))
                                        local_sub = f"http://127.0.0.1:{server_inst._port}/m3u8?{urllib.parse.urlencode({'url': resolved, 'mid': mid})}"
                                        return f'URI="{local_sub}"'
                                    rewritten_lines.append(re.sub(r'URI="([^"]*)"', repl_uri, sline))
                                elif pending_stream_inf is not None and not sline.startswith("#") and sline:
                                    res_m = re.search(r"RESOLUTION=\d+x(\d+)", pending_stream_inf)
                                    h = res_m.group(1) if res_m else None
                                    if vheight is None or vheight == h:
                                        resolved = urllib.parse.urljoin(base_url, sline)
                                        local_var = f"http://127.0.0.1:{server_inst._port}/m3u8?{urllib.parse.urlencode({'url': resolved, 'mid': mid})}"
                                        rewritten_lines.append(pending_stream_inf)
                                        rewritten_lines.append(local_var)
                                    pending_stream_inf = None
                                elif pending_stream_inf is not None:
                                    continue
                                else:
                                    rewritten_lines.append(sline)
                        else:
                            media_seq = 0
                            seq = 0
                            current_iv = None
                            current_key_url = None

                            for line in content.splitlines():
                                sline = line.strip()
                                if sline.startswith("#EXT-X-MEDIA-SEQUENCE:"):
                                    try:
                                        media_seq = int(sline.split(":", 1)[1].strip())
                                        seq = media_seq
                                    except Exception:
                                        pass
                                    rewritten_lines.append(sline)
                                elif sline.startswith("#EXT-X-KEY:"):
                                    # Strip EXT-X-KEY since segments are delivered in plain MPEG-TS
                                    m_uri = re.search(r'URI="([^"]*)"', sline)
                                    m_iv = re.search(r'IV=0x([0-9a-fA-F]+)', sline, re.IGNORECASE)
                                    current_key_url = urllib.parse.urljoin(base_url, m_uri.group(1)) if m_uri else None
                                    current_iv = m_iv.group(1) if m_iv else None
                                elif not sline.startswith("#") and sline:
                                    seg_url = urllib.parse.urljoin(base_url, sline)
                                    iv_hex = current_iv if current_iv else f"{seq:032x}"
                                    qparams = {
                                        "url": seg_url,
                                        "mid": mid,
                                        "iv": iv_hex,
                                    }
                                    if current_key_url:
                                        qparams["key"] = current_key_url
                                    local_seg = f"http://127.0.0.1:{server_inst._port}/segment.ts?{urllib.parse.urlencode(qparams)}"
                                    rewritten_lines.append(local_seg)
                                    seq += 1
                                else:
                                    rewritten_lines.append(sline)

                        out_body = "\n".join(rewritten_lines).encode("utf-8")
                        self.send_response(200)
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Content-Length", str(len(out_body)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "no-store, no-cache")
                        self.end_headers()
                        self.wfile.write(out_body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    except Exception as e:
                        try:
                            self.send_error(500, str(e))
                        except Exception:
                            pass

                elif parsed.path == "/segment.ts":
                    url = params.get("url", [None])[0]
                    mid = params.get("mid", [None])[0]
                    key_url = params.get("key", [None])[0]
                    iv_hex = params.get("iv", [None])[0]
                    if not url or not mid:
                        self.send_error(400, "Missing parameters")
                        return

                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://anime.uniquestream.net/"})
                        with urllib.request.urlopen(req, context=SSL_CTX, timeout=15) as resp:
                            ciphertext = resp.read()

                        if key_url:
                            real_key = server_inst._fetch_real_key(key_url, mid)
                            iv_clean = (iv_hex or "0").rjust(32, "0")
                            iv = bytes.fromhex(iv_clean)
                            plaintext = _decrypt_aes_cbc(real_key, iv, ciphertext)
                        else:
                            plaintext = ciphertext

                        self.send_response(200)
                        self.send_header("Content-Type", "video/mp2t")
                        self.send_header("Content-Length", str(len(plaintext)))
                        self.send_header("Access-Control-Allow-Origin", "*")
                        self.send_header("Cache-Control", "public, max-age=3600")
                        self.end_headers()
                        self.wfile.write(plaintext)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    except Exception as e:
                        try:
                            self.send_error(500, str(e))
                        except Exception:
                            pass
                else:
                    self.send_error(404, "Not found")

        return HlsHandler


_HLS_SERVER = _UniqueStreamHlsServer()


def _uniquestream_src_sort_key(s: dict, ttype: str = "sub") -> tuple:
    """Sort UniqueStream sources: preferred audio first, highest resolution first."""
    res_str = s.get("resolution") or s.get("sourceName", "")
    h = parse_resolution_height(res_str)
    sname = (s.get("sourceName") or "").lower()
    audio = (s.get("audio") or "").lower()
    is_dub = "dub" in audio or "[dub]" in sname
    audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
    prio = s.get("priority", 2)
    return (audio_penalty, prio, -h)


class UniqueStreamProvider(Provider):
    """UniqueStream / AnimeStream provider using REST API and AES-128 HLS proxy."""

    id = "uniquestream"
    audio_mode = "embedded_multi_audio"

    def __init__(self, request_json_fn=None):
        self._request_json = request_json_fn
        if not hasattr(self, "metadata"):
            self.metadata = {}
        if not hasattr(self, "domains") or not self.domains:
            self.domains = [
                "https://anime.uniquestream.net",
            ]
        self._session = None

    @property
    def base_url(self) -> str:
        return self.domains[0] if self.domains else "https://anime.uniquestream.net"

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/v1"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "UniqueStream")

    def _get_session(self):
        if self._session is None and CURL_CFFI_AVAILABLE and cffi_requests is not None:
            self._session = cffi_requests.Session(impersonate="chrome")
        return self._session

    def _fetch_json(self, url: str, headers: dict[str, str] | None = None, timeout: int = 12) -> Any:
        req_headers = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{self.base_url}/",
        }
        if headers:
            req_headers.update(headers)

        sess = self._get_session()
        if sess is not None:
            try:
                resp = sess.get(url, headers=req_headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()
            except Exception as e:
                _logger.debug("UniqueStream cffi fetch failed on %s: %s", url, e)

        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="ignore"))

    def _fetch_text(self, url: str, headers: dict[str, str] | None = None, timeout: int = 12) -> str:
        req_headers = {
            "User-Agent": UA,
            "Accept": "*/*",
            "Referer": f"{self.base_url}/",
        }
        if headers:
            req_headers.update(headers)

        sess = self._get_session()
        if sess is not None:
            try:
                resp = sess.get(url, headers=req_headers, timeout=timeout)
                if resp.status_code == 200:
                    return resp.text
            except Exception as e:
                _logger.debug("UniqueStream cffi fetch failed on %s: %s", url, e)

        req = urllib.request.Request(url, headers=req_headers)
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="ignore")

    def search(self, query: str, ttype: str = "sub") -> list[dict]:
        """Search anime titles on UniqueStream."""
        q_enc = urllib.parse.quote(query.strip())
        url = f"{self.api_url}/search?query={q_enc}&page=1&t=all&limit=20"
        try:
            data = self._fetch_json(url)
        except Exception as e:
            _logger.debug("UniqueStream search failed for %r: %s", query, e)
            return []

        series_list = data.get("series") or []
        movies_list = data.get("movies") or []
        combined = series_list + movies_list

        results = []
        for item in combined:
            cid = item.get("content_id")
            if not cid:
                continue
            is_movie = (item.get("type") == "movie")
            prefix = "movie" if is_movie else "series"
            provider_id = f"{prefix}/{cid}"
            title = item.get("title") or "Unknown"
            image = item.get("image") or ""
            subbed = bool(item.get("subbed", True))
            dubbed = bool(item.get("dubbed", False))
            ep_count = item.get("episodes_count") or 1

            results.append({
                "_id": provider_id,
                "name": title,
                "englishName": title,
                "thumbnail": image,
                "type": "MOVIE" if is_movie else "TV",
                "availableEpisodes": {
                    "sub": ep_count if subbed else 0,
                    "dub": ep_count if dubbed else 0,
                    "raw": 0,
                },
            })

        return normalize_titles(results, provider_id=self.id, provider_name=self.name, id_key="_id")

    def get_title(self, provider_id: str) -> dict:
        """Fetch title details for an anime from UniqueStream."""
        url = f"{self.api_url}/{provider_id}"
        try:
            details = self._fetch_json(url)
        except Exception as e:
            _logger.debug("UniqueStream get_title failed for %r: %s", provider_id, e)
            return {
                "id": provider_id,
                "name": provider_id.split("/")[-1],
                "provider": self.id,
                "provider_id": self.id,
            }

        title = details.get("title") or provider_id.split("/")[-1]
        desc = details.get("description") or ""
        images = details.get("images") or []
        poster = ""
        banner = ""
        for img in images:
            itype = img.get("type", "")
            iurl = img.get("url", "")
            if itype == "poster_tall" and not poster:
                poster = iurl
            elif "banner" in itype and not banner:
                banner = iurl
            elif not poster:
                poster = iurl

        genres = [g.get("title") for g in (details.get("genre") or []) if g.get("title")]
        studio = details.get("studio") or ""

        return {
            "id": provider_id,
            "name": title,
            "english_name": title,
            "description": desc,
            "thumbnail": poster,
            "banner": banner or poster,
            "genres": genres,
            "studio": studio,
            "provider": self.id,
            "provider_id": self.id,
        }

    def episode_catalog(self, provider_id: str, ttype: str = "sub") -> dict:
        """Fetch episode catalog for a title."""
        if provider_id.startswith("movie/"):
            ep_id = f"{provider_id}|ja-JP"
            return normalize_episode_catalog(
                {
                    "state": "loaded",
                    "ids": [ep_id],
                    "labels": {ep_id: "Movie"},
                    "items": [{"id": ep_id, "num": "1", "name": "Movie"}],
                    "episodes": {
                        "sub": [{"id": ep_id, "label": "Movie"}],
                        "dub": [{"id": ep_id, "label": "Movie"}],
                        "raw": [],
                    },
                },
                provider_id=self.id,
                provider_title_id=provider_id,
            )

        url = f"{self.api_url}/{provider_id}"
        try:
            details = self._fetch_json(url)
        except Exception as e:
            _logger.debug("UniqueStream catalog details failed for %s: %s", provider_id, e)
            return normalize_episode_catalog({"state": "empty", "ids": [], "items": []}, provider_id=self.id, provider_title_id=provider_id)

        seasons = [s for s in (details.get("seasons") or []) if (s.get("episode_count") or 0) > 0]
        if not seasons:
            return normalize_episode_catalog({"state": "empty", "ids": [], "items": []}, provider_id=self.id, provider_title_id=provider_id)

        season_pages: list[tuple[dict, int]] = []
        for s in seasons:
            ep_count = s.get("episode_count", 0)
            total_pages = max(1, math.ceil(ep_count / 20.0))
            for p in range(1, total_pages + 1):
                season_pages.append((s, p))

        episodes_by_order: list[dict] = []

        def fetch_season_page(sp: tuple[dict, int]) -> list[dict]:
            season, page = sp
            s_cid = season.get("content_id")
            s_disp = season.get("display_number") or str(season.get("season_number", 1))
            ep_url = f"{self.api_url}/season/{s_cid}/episodes?page={page}&limit=20"
            try:
                eps = self._fetch_json(ep_url)
                ret = []
                for ep in eps:
                    ep_cid = ep.get("content_id")
                    if not ep_cid:
                        continue
                    locales = ep.get("audio_locales") or ["ja-JP"]
                    pref_loc = "en-US" if (ttype == "dub" and "en-US" in locales) else (locales[0] if locales else "ja-JP")
                    ep_id = f"episode/{ep_cid}|{pref_loc}"
                    ep_num_val = ep.get("episode_number") or ep.get("episode") or 0
                    try:
                        ep_num_float = float(ep_num_val)
                        ep_num_str = str(int(ep_num_float) if ep_num_float.is_integer() else ep_num_float)
                    except Exception:
                        ep_num_str = str(ep_num_val)

                    title_str = ep.get("title") or ""
                    ep_name = f"S{s_disp} E{ep.get('episode', ep_num_str)}"
                    if title_str:
                        ep_name += f" - {title_str}"

                    ret.append({
                        "id": ep_id,
                        "num": ep_num_str,
                        "name": ep_name,
                        "subbed": "ja-JP" in locales or any(l != "en-US" for l in locales),
                        "dubbed": "en-US" in locales,
                        "_sort": (int(s_disp) if str(s_disp).isdigit() else 1, float(ep_num_val) if isinstance(ep_num_val, (int, float)) else 0),
                    })
                return ret
            except Exception as e:
                _logger.debug("UniqueStream season page %s:%d failed: %s", s_cid, page, e)
                return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future_to_page = {executor.submit(fetch_season_page, sp): sp for sp in season_pages}
            for future in concurrent.futures.as_completed(future_to_page):
                try:
                    res = future.result()
                    episodes_by_order.extend(res)
                except Exception as e:
                    _logger.debug("Failed page: %s", e)

        # Sort episodes ascending by season and episode number
        episodes_by_order.sort(key=lambda x: x.get("_sort", (0, 0)))

        items = []
        ids = []
        labels = {}
        sub_eps = []
        dub_eps = []
        for ep in episodes_by_order:
            ep_dict = {
                "id": ep["id"],
                "num": ep["num"],
                "name": ep["name"],
            }
            items.append(ep_dict)
            ids.append(ep["id"])
            labels[ep["id"]] = ep["name"]
            if ep.get("subbed", True):
                sub_eps.append({"id": ep["id"], "label": ep["name"]})
            if ep.get("dubbed", False):
                dub_eps.append({"id": ep["id"], "label": ep["name"]})

        return normalize_episode_catalog(
            {
                "state": "loaded",
                "ids": ids,
                "labels": labels,
                "items": items,
                "episodes": {
                    "sub": sub_eps if sub_eps else [{"id": eid, "label": labels[eid]} for eid in ids],
                    "dub": dub_eps if dub_eps else [{"id": eid, "label": labels[eid]} for eid in ids],
                    "raw": [],
                },
            },
            provider_id=self.id,
            provider_title_id=provider_id,
        )

    def episode_sources(self, provider_id: str, episode: str, ttype: str = "sub") -> list[dict]:
        """Resolve stream sources for an episode with local AES-128 HLS decrypting proxy."""
        if not str(episode).startswith("episode/") and not str(episode).startswith("movie/"):
            cat = self.episode_catalog(provider_id, ttype)
            items = cat.get("items") or []
            found = None
            for it in items:
                if str(it.get("num")) == str(episode) or str(it.get("id")) == str(episode):
                    found = it.get("id")
                    break
            if not found:
                try:
                    idx = int(episode) - 1
                    if 0 <= idx < len(items):
                        found = items[idx].get("id")
                except ValueError:
                    pass
            if found:
                episode = found

        if "|" in episode:
            media_path, default_locale = episode.split("|", 1)
        else:
            media_path = episode

        media_url = f"{self.api_url}/{media_path}/media/hls/ja-JP"
        try:
            media = self._fetch_json(media_url)
        except Exception as e:
            _logger.debug("UniqueStream media fetch failed for %s: %s", media_path, e)
            return []

        media_id = media.get("media_id")
        if not media_id:
            _logger.debug("UniqueStream media returned no media_id: %s", media)
            return []

        raw_tracks: list[dict] = []

        # 1. Main Japanese audio track
        hls_main = media.get("hls")
        if hls_main and hls_main.get("playlist"):
            raw_tracks.append({
                "playlist": hls_main["playlist"],
                "locale": hls_main.get("locale") or "ja-JP",
                "type": "main",
                "subtitles": hls_main.get("subtitles") or [],
            })

        # 2. Hard subs
        for hs in (hls_main.get("hard_subs") if hls_main else []) or []:
            if hs.get("playlist"):
                raw_tracks.append({
                    "playlist": hs["playlist"],
                    "locale": hs.get("locale"),
                    "type": "hardsub",
                    "subtitles": [],
                })

        # 3. Dub audio tracks
        for v in (media.get("versions", {}).get("hls") or []):
            if v.get("playlist"):
                raw_tracks.append({
                    "playlist": v["playlist"],
                    "locale": v.get("locale"),
                    "type": "dub",
                    "subtitles": v.get("subtitles") or [],
                })

        sources: list[dict] = []
        cached_variants: list[str] | None = None

        for track in raw_tracks:
            playlist_url = track["playlist"]
            locale = track.get("locale")
            track_type = track.get("type")

            is_dub = (track_type == "dub") or (locale and locale != "ja-JP")
            audio_tag = "dub" if is_dub else "sub"

            locale_name = LOCALE_NAMES.get(locale, locale or "Original")
            if track_type == "hardsub":
                display_label = f"{locale_name} HardSub"
            elif is_dub:
                display_label = f"{locale_name} Dub"
            else:
                display_label = f"{locale_name} Original"

            # Parse subtitles
            subs_list = []
            for s in track.get("subtitles", []):
                s_url = s.get("url")
                if not s_url:
                    continue
                s_lang = s.get("language") or "en-US"
                s_label = LOCALE_NAMES.get(s_lang, s_lang)
                subs_list.append({
                    "label": s_label,
                    "language": s_lang.split("-")[0],
                    "url": s_url,
                    "type": "vtt",
                    "default": s_lang == "en-US",
                })

            # Inspect master playlist for resolutions once and reuse across tracks
            if cached_variants is None:
                found_variants = []
                try:
                    master_text = self._fetch_text(playlist_url, timeout=8)
                    for match in VARIANT_REGEX.finditer(master_text):
                        found_variants.append(match.group(1))
                except Exception as e:
                    _logger.debug("Master inspection failed on %s: %s", playlist_url, e)
                cached_variants = found_variants or ["1080", "720"]

            found_variants = cached_variants

            # Emit stream for each resolution
            for h_str in found_variants:
                local_url = _HLS_SERVER.local_playlist_url(playlist_url, media_id, height=h_str)
                sources.append({
                    "link": local_url,
                    "streamUrl": local_url,
                    "sourceUrl": local_url,
                    "url": local_url,
                    "priority": 1 if audio_tag == ttype else 2,
                    "sourceName": f"UniqueStream ({display_label} · {h_str}p [{audio_tag.upper()}])",
                    "type": "m3u8",
                    "resolution": f"{h_str}p",
                    "format": "hls",
                    "audio": audio_tag,
                    "subtitles": subs_list,
                    "headers": {"Referer": f"{self.base_url}/", "User-Agent": UA},
                    "requires_proxy": False,
                })

        sources.sort(key=lambda s: _uniquestream_src_sort_key(s, ttype))
        return normalize_episode_sources(
            {"episode": {"sourceUrls": sources}},
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
        return f"{self.base_url}/{provider_id}"


PROVIDER_CLASS = UniqueStreamProvider

