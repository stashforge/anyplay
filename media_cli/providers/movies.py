"""Movies streaming provider (VidSrc, VidNest, etc)."""

import re
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urljoin

from media_cli.providers.shared.models import normalize_episode_sources

from ..media.source_entries import build_direct_source
from .shared.movie import MovieProvider


class MoviesProvider(MovieProvider):
    id = "movies"
    audio_mode = "embedded_multi_audio"

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
    }

    @property
    def base_url(self) -> str:
        return self.domains[0] if getattr(self, 'domains', None) else "https://vsembed.ru"

    @property
    def name(self) -> str:
        return self.metadata.get("name", "Movies")

    def _fetch_page(self, url: str, headers: dict | None = None) -> str | None:
        if url.startswith("//"):
            url = "https:" + url
        req_headers = headers or dict(self.HEADERS, Referer=f"{self.base_url}/")
        req = urllib.request.Request(url, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.read().decode("utf-8", errors="ignore")
        except Exception:
            return None

    def _fetch_vidsrc(self, media_type: str, tmdb_id: str, s: str = "1", e: str = "1") -> list[dict]:
        headers = {**self.HEADERS, "Referer": f"{self.base_url}/"}

        if media_type == "movie":
            url = f"{self.base_url}/embed/movie?tmdb={tmdb_id}"
        else:
            url = f"{self.base_url}/embed/tv?tmdb={tmdb_id}&season={s}&episode={e}"

        html1 = self._fetch_page(url, headers)
        if not html1: return []

        iframe_match = re.search(r'<iframe[^>]*\s+src=["\']([^"\']+)["\'][^>]*>', html1, re.IGNORECASE)
        if not iframe_match: return []

        second_url = iframe_match.group(1)
        html2 = self._fetch_page(second_url, headers)
        if not html2: return []

        rel_match = re.search(r'src:\s*[\'"]([^\'"]+)[\'"]', html2, re.IGNORECASE)
        if not rel_match: return []

        third_url = urljoin(second_url, rel_match.group(1))
        html3 = self._fetch_page(third_url, headers)
        if not html3: return []

        file_match = re.search(r'file\s*:\s*["\']([^"\']+)["\']', html3, re.IGNORECASE)
        if not file_match:
            file_match = re.search(r'var\s+master_urls\s*=\s*["\']([^"\']+)["\']', html3, re.IGNORECASE)

        if not file_match: return []

        raw_urls_str = file_match.group(1)
        raw_urls = re.split(r'\s+or\s+', raw_urls_str, flags=re.IGNORECASE)

        player_domains = {
            '{v1}': 'neonhorizonworkshops.com',
            '{v2}': 'wanderlynest.com',
            '{v3}': 'orchidpixelgardens.com',
            '{v4}': 'cloudnestra.com'
        }

        sources = []
        token = ""
        if "__TOKEN__" in raw_urls_str:
            m = re.search(r'https?://([^/]+)', raw_urls[0])
            if m:
                domain = m.group(1)
                token_html = self._fetch_page(f"https://{domain}/generate.php", headers)
                if token_html:
                    token = token_html.strip()

        for idx, tmpl in enumerate(raw_urls):
            url = tmpl
            for k, v in player_domains.items():
                url = url.replace(k, v)
            if token:
                url = url.replace("__TOKEN__", token)

            if '{' not in url and '}' not in url:
                name_suffix = f" {idx+1}" if len(raw_urls) > 1 else ""
                sources.append(
                    build_direct_source(
                        name=f"VidSrc{name_suffix} (Auto)",
                        stream_url=url,
                        stream_type="hls",
                        resolution="Auto",
                        headers={
                            "Referer": "https://cloudnestra.com/",
                            "Origin": "https://cloudnestra.com",
                            "User-Agent": self.HEADERS["User-Agent"]
                        }
                    )
                )
        return sources

    def _fetch_vidnest_endpoint(self, srv: str, media_type: str, tmdb_id: str, s: str = "1", e: str = "1") -> list[dict]:
        sources = []
        try:
            import json
            import urllib.request

            headers = {
                "User-Agent": self.HEADERS["User-Agent"],
                "Referer": "https://vidnest.fun/"
            }

            VIDNEST_ALPHABET = "RB0fpH8ZEyVLkv7c2i6MAJ5u3IKFDxlS1NTsnGaqmXYdUrtzjwObCgQP94hoeW+/="
            VIDNEST_REVERSE_MAP = {c: i for i, c in enumerate(VIDNEST_ALPHABET)}

            def decode_vidnest(input_str):
                padded = input_str + "=" * ((4 - len(input_str) % 4) % 4)
                bytes_arr = bytearray()
                for i in range(0, len(padded), 4):
                    chunk = padded[i:i+4]
                    c0 = VIDNEST_REVERSE_MAP.get(chunk[0], 64)
                    c1 = VIDNEST_REVERSE_MAP.get(chunk[1], 64)
                    c2 = 64 if chunk[2] == "=" else VIDNEST_REVERSE_MAP.get(chunk[2], 64)
                    c3 = 64 if chunk[3] == "=" else VIDNEST_REVERSE_MAP.get(chunk[3], 64)
                    bytes_arr.append(((c0 << 2) | (c1 >> 4)) & 0xff)
                    if c2 != 64: bytes_arr.append((((c1 & 15) << 4) | (c2 >> 2)) & 0xff)
                    if c3 != 64: bytes_arr.append((((c2 & 3) << 6) | c3) & 0xff)
                return bytes_arr.decode("utf-8")

            if media_type == "movie":
                url = f"https://new.vidnest.fun/{srv}/movie/{tmdb_id}"
            else:
                url = f"https://new.vidnest.fun/{srv}/tv/{tmdb_id}/{s}/{e}"

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as res:
                data = json.loads(res.read())
                if "data" in data:
                    dec = decode_vidnest(data["data"])
                    js = json.loads(dec)

                    # Allmovies / Hollymoviehd format
                    streams_list = js.get("streams", [])
                    for idx, stream in enumerate(streams_list):
                        stream_url = stream.get("url")
                        if stream_url:
                            suffix = f" {idx+1}" if len(streams_list) > 1 else ""
                            stream_headers = stream.get("headers", {})
                            stream_headers.update(headers)
                            sources.append(build_direct_source(
                                name=f"VidNest{suffix} ({srv.capitalize()})",
                                stream_url=stream_url,
                                stream_type="hls" if "hls" in stream.get("type", "").lower() or stream_url.endswith(".m3u8") else "mp4",
                                resolution="Auto",
                                headers=stream_headers
                            ))

                    # Moviebox format
                    url_list = js.get("url", [])
                    for idx, stream in enumerate(url_list):
                        stream_url = stream.get("link")
                        if stream_url:
                            suffix = f" {idx+1}" if len(url_list) > 1 else ""
                            sources.append(build_direct_source(
                                name=f"VidNest{suffix} ({srv.capitalize()})",
                                stream_url=stream_url,
                                stream_type="mp4",
                                resolution="Auto",
                                headers=headers
                            ))

                    # Klikxxi format
                    src_list = js.get("sources", [])
                    for idx, stream in enumerate(src_list):
                        stream_url = stream.get("url")
                        if stream_url:
                            suffix = f" {idx+1}" if len(src_list) > 1 else ""
                            stream_headers = stream.get("headers", {})
                            stream_headers.update(headers)
                            sources.append(build_direct_source(
                                name=f"VidNest{suffix} ({srv.capitalize()})",
                                stream_url=stream_url,
                                stream_type="hls" if "hls" in stream.get("type", "").lower() or stream_url.endswith(".m3u8") else "mp4",
                                resolution="Auto",
                                headers=stream_headers
                            ))
        except Exception:
            pass
        return sources

    def episode_sources(
        self, provider_id: str, episode: str, ttype: str = "sub"
    ) -> dict[str, Any] | None:
        if ":" in provider_id:
            media_type, tmdb_id = provider_id.split(":", 1)
        else:
            media_type, tmdb_id = "movie", provider_id

        s, e = "1", "1"
        if media_type != "movie":
            m = re.match(r"s(\d+)e(\d+)", episode, re.IGNORECASE)
            if m:
                s, e = m.group(1), m.group(2)

        import concurrent.futures
        import random

        # 3 highly reliable Primary sources
        primary_pool = [
            lambda: self._fetch_vidnest_endpoint("hollymoviehd", media_type, tmdb_id, s, e),
            lambda: self._fetch_vidsrc(media_type, tmdb_id, s, e),
            lambda: self._fetch_vidnest_endpoint("allmovies", media_type, tmdb_id, s, e)
        ]

        # 12 experimental Backup sources
        vidnest_backups = [
            "moviebox", "klikxxi", "vidsrc", "vidplay",
            "filemoon", "embed", "novaflow", "vidbinge",
            "smashystream", "mycloud", "upcloud", "superembed"
        ]
        backup_pool = [
            lambda ep=endpoint: self._fetch_vidnest_endpoint(ep, media_type, tmdb_id, s, e)
            for endpoint in vidnest_backups
        ]

        # Shuffle backups randomly, but DO NOT shuffle the primary pool!
        # The primary pool order dictates their strict UI display priority.
        random.shuffle(backup_pool)
        pool = primary_pool + backup_pool

        sources = []
        successful_apis = 0
        target_apis = 7  # We want at least 7 working APIs total

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            active_futures = set()
            primary_futures = set()
            future_indices = {}
            pool_idx = 0

            # fill pipeline initially up to 3 threads
            while pool_idx < len(pool) and len(active_futures) < 3:
                future = executor.submit(pool[pool_idx])
                active_futures.add(future)
                future_indices[future] = pool_idx
                if pool_idx < len(primary_pool):
                    primary_futures.add(future)
                pool_idx += 1

            while active_futures:
                done, active_futures = concurrent.futures.wait(
                    active_futures, return_when=concurrent.futures.FIRST_COMPLETED
                )

                for f in done:
                    if f in primary_futures:
                        primary_futures.remove(f)
                    try:
                        res = f.result()
                        if res:
                            # Attach strict UI display priority based on the API's rank in the pool
                            for r in res:
                                r["_priority"] = future_indices[f]
                            sources.extend(res)
                            successful_apis += 1
                    except Exception:
                        pass

                # Fill the gap back up to 3 active threads
                # ONLY launch backup tasks if we haven't reached our quota
                while pool_idx < len(pool) and len(active_futures) < 3:
                    if successful_apis >= target_apis and pool_idx >= len(primary_pool):
                        break  # Stop launching backup tasks!

                    future = executor.submit(pool[pool_idx])
                    active_futures.add(future)
                    future_indices[future] = pool_idx
                    if pool_idx < len(primary_pool):
                        primary_futures.add(future)
                    pool_idx += 1

                # We stop querying IF we hit our target quota AND all primary sources have finished
                if successful_apis >= target_apis and not primary_futures:
                    break

        # No cap on total mirrors! We return everything we scraped from the successful APIs.

        if not sources:
            return None

        # Strictly sort the mirrors so that VidSrc (0) is ALWAYS above MovieBox (1) in the UI
        sources.sort(key=lambda s: s.get("_priority", 999))

        # Format appropriately for media_cli schema
        payload = {"episode": {"sourceUrls": sources}}
        return normalize_episode_sources(
            payload,
            provider_id=self.id,
            provider_title_id=provider_id,
            episode=episode,
        )

PROVIDER_CLASS = MoviesProvider
