"""Bounded HTTP samples for diagnostic access checks, never media decoding."""

import re
import time
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

MANIFEST_LIMIT = 1024 * 1024
SAMPLE_LIMIT = 64 * 1024
HTTP_BUDGET = 60
MAX_REDIRECTS = 5


@dataclass(frozen=True)
class HTTPResult:
    url: str
    status: int
    body: bytes
    truncated: bool = False


class CheckIssue(Exception):
    def __init__(self, reason, status="inconclusive", code=None):
        self.reason, self.status, self.code = reason, status, code


def _usable(result):
    if not 200 <= result.status < 300:
        state = "failed" if result.status in (401, 403, 404, 410) else "inconclusive"
        raise CheckIssue(f"HTTP {result.status}; access was not verified", state, result.status)
    if not result.body:
        raise CheckIssue("Empty response; access was not verified", code=result.status)
    opening = result.body.lstrip()[:80].lower()
    if opening.startswith((b"<html", b"<!doctype html", b'{"error"')):
        raise CheckIssue("Unexpected error/HTML response instead of media", "failed", result.status)
    return result


def _range_headers(headers, specification):
    result = dict(headers)
    if specification:
        match = re.fullmatch(r"(\d+)(?:@(\d+))?", str(specification))
        if not match or int(match[1]) <= 0:
            raise CheckIssue("Invalid HLS byte range")
        start = int(match[2] or 0)
        length = min(int(match[1]), SAMPLE_LIMIT)
        result["Range"] = f"bytes={start}-{start+length-1}"
    return result


def _resource(url, headers, fetch, *, hls=False, subtitle=False, depth=0, audio_declarations=None):
    if depth > 3:
        raise CheckIssue("Playlist nesting limit reached")
    hint = re.search(r'[?&]hls_res=(\d+)p?(?:&|$)', url) if hls else None
    height = int(hint.group(1)) if hint else None
    target = re.sub(r'([?&])hls_res=[^&]*&?', lambda m: m.group(1), url).rstrip('?&') if hint else url
    result = _usable(fetch(target, headers, MANIFEST_LIMIT if hls else SAMPLE_LIMIT, sample=not hls))
    body = result.body
    if body.startswith(b"EM3U8v1:"):
        from ..services.requirements import require_any_python
        require_any_python(("cryptography.hazmat.primitives.ciphers.aead", "Cryptodome.Cipher.AES", "Crypto.Cipher.AES"),
                           "Health encrypted HLS check")
        from ..media.decryption import decrypt_em3u8
        text = decrypt_em3u8(body.decode("utf-8", errors="replace"))
        if not text:
            raise CheckIssue("Encrypted playlist could not be decoded")
        body = text.encode("utf-8")
    if body.lstrip(b"\xef\xbb\xbf\r\n ").startswith(b"#EXTM3U"):
        if result.truncated:
            raise CheckIssue("Manifest exceeds bounded read limit")
        from ..services.requirements import require_python
        parser = require_python("m3u8", "Health HLS access check")
        playlist = parser.loads(body.decode("utf-8-sig"), uri=result.url)
        if playlist.playlists:
            choices = [p for p in playlist.playlists if p.stream_info.resolution and p.stream_info.resolution[1] == height] if height else playlist.playlists
            if not choices:
                raise CheckIssue("Requested HLS quality is absent; a different quality was not substituted")
            selected = max(choices, key=lambda p: p.stream_info.bandwidth or 0)
            if audio_declarations is not None and selected.stream_info.audio:
                renditions = [item for item in playlist.media
                              if item.type == 'AUDIO' and item.group_id == selected.stream_info.audio]
                if renditions:
                    chosen = next((item for item in renditions if item.default == 'YES'), renditions[0])
                    if chosen.uri:
                        audio_url = urljoin(result.url, chosen.uri)
                        if audio_url not in audio_declarations:
                            audio_declarations.append(audio_url)
                else:
                    # The manifest references a group but supplied no usable rendition.
                    audio_declarations.append(None)
            return _resource(urljoin(result.url, selected.uri), headers, fetch, hls=True,
                             subtitle=subtitle, depth=depth+1, audio_declarations=audio_declarations)
        if not playlist.segments:
            raise CheckIssue("Manifest contains no sampleable segments")
        segment = playlist.segments[0]
        if segment.init_section:
            _usable(fetch(urljoin(result.url, segment.init_section.uri),
                          _range_headers(headers, segment.init_section.byterange), SAMPLE_LIMIT, sample=True))
        if segment.key and segment.key.method not in (None, "NONE"):
            if segment.key.method != "AES-128" or not segment.key.uri:
                raise CheckIssue("Encrypted stream method not supported by this HTTP check; not decoded")
            key = _usable(fetch(urljoin(result.url, segment.key.uri), headers, 64))
            if len(key.body) != 16 or key.truncated:
                raise CheckIssue("HLS key response is not a complete AES-128 key")
        result = _usable(fetch(urljoin(result.url, segment.uri), _range_headers(headers, segment.byterange), SAMPLE_LIMIT, sample=True))
        body = result.body
        reason = "HLS manifest and bounded segment bytes fetched; not decoded"
    elif hls:
        raise CheckIssue("Response is not a usable HLS manifest", "failed", result.status)
    else:
        reason = "Bounded media bytes fetched; not decoded"
    if subtitle:
        text = body.decode("utf-8-sig", errors="replace").lstrip()
        recognized = text.startswith("WEBVTT") or "[Script Info]" in text or "[Events]" in text or re.search(r"\d\d:\d\d:\d\d[,.]\d+\s*-->", text)
        if not recognized:
            raise CheckIssue("Subtitle bytes fetched but text format unrecognized; rendering unverified")
        reason = "Subtitle text sample accessible; rendering and quality not verified"
    return {"status": "passed", "reason": reason, "http_status": result.status,
            "host": urlsplit(result.url).hostname or "", "bytes_sampled": len(result.body)}


def probe_entry(entry, fetch):
    from ..core.api import ProviderDependencyError

    headers = dict(entry.get("headers") or {})
    if entry.get("referer"):
        headers.setdefault("Referer", entry["referer"])
    tracks = [("video", entry.get("streamUrl") or entry.get("sourceUrl") or entry.get("link"), "")]
    audio_urls = [entry['audio_url']] if entry.get('audio_url') else []
    for audio in entry.get('audio_tracks') or []:
        if isinstance(audio, dict) and audio.get('url') and audio['url'] not in audio_urls:
            audio_urls.append(audio['url'])
    tracks.extend(('audio', url, '') for url in audio_urls or [None])
    for subtitle in entry.get("subtitles") or []:
        if isinstance(subtitle, dict):
            tracks.append(("subtitle", subtitle.get("url") or subtitle.get("file"), str(subtitle.get("label") or "")))
    if not any(track[0] == "subtitle" for track in tracks):
        tracks.append(("subtitle", None, ""))
    checks = []
    declared_audio = []
    for track, url, label in tracks:
        if track == 'audio' and not url and declared_audio:
            url = declared_audio[0]
            if url is None:
                checks.append({'track': 'audio', 'required': True, 'status': 'not_verified',
                               'reason': 'Selected HLS variant references external audio without a usable rendition; access unverified',
                               'elapsed_seconds': 0.0})
                continue
        before = time.monotonic()
        if entry.get("type") == "dash" and track == "video":
            checks.append({"track": track, "required": True, "status": "not_verified", "reason": "DASH segment probing is not implemented; fetching an MPD alone is insufficient", "elapsed_seconds": 0.0})
            continue
        if not url:
            checks.append({"track": track, "required": track == "video", "status": "not_verified", "reason": "No separate track supplied; embedded track presence is not confirmed by HTTP", "elapsed_seconds": 0.0})
            continue
        try:
            hls = entry.get("type") == "hls" if track != "subtitle" else urlsplit(url).path.lower().endswith(".m3u8")
            result = _resource(url, headers, fetch, hls=hls, subtitle=track == "subtitle",
                               audio_declarations=declared_audio if track == "video" else None)
        except ProviderDependencyError:
            raise
        except CheckIssue as issue:
            result = {"status": issue.status, "reason": issue.reason}
            if issue.code is not None:
                result["http_status"] = issue.code
        except Exception as error:
            result = {"status": "inconclusive", "reason": f"HTTP sample could not be verified ({type(error).__name__}); not proof of a dead mirror"}
        result.update(track=track, required=True, elapsed_seconds=time.monotonic()-before)
        if label:
            result["subtitle_label"] = label
        checks.append(result)
    return checks


def media_destination_allowed(provider_id, url):
    if provider_id == 'senshi':
        from ..providers.senshi import _trusted_stream_url
        return _trusted_stream_url(url, subtitle=True)
    if provider_id not in ('anipm', 'reanime', 'anikoto', 'anidb', 'anizone', 'animegg', 'animexin') or not isinstance(url, str) or any(ord(c) < 33 or ord(c) == 127 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        permitted = host == 'media.settlar.io' if provider_id == 'anipm' else (host == 'flixcloud.cc' or host.endswith('.flixcloud.cc') or host == 'dl.spacedl.top' or bool(re.fullmatch(r'vault-[0-9]+\.(?:cobaltcdn|soniccdn|rundowncdn)\.top', host)) or (host == 'enc-dec.app' and parsed.path == '/api/parse-flixcloud'))
        if provider_id == 'animexin':
            permitted = host == 'dmcdn.net' or host.endswith('.dmcdn.net') or (host == 'www.dailymotion.com' and parsed.path.startswith(('/subtitle/', '/cdn/subtitle/')))
        vidcache_port = None
        if provider_id == 'animegg':
            if host == 'vidcache.net':
                vidcache_port = 8161
            elif re.fullmatch(r's[0-9]+\.vidcache\.net', host):
                vidcache_port = 8166
            permitted = host in ('animegg.org', 'www.animegg.org') or (vidcache_port is not None and parsed.port == vidcache_port)
        if provider_id == 'anizone':
            permitted = host == 'seiryuu.vid-cdn.xyz'
        if provider_id == 'anidb':
            permitted = host == 'b1.xlsbox.com'
        if provider_id == 'anikoto':
            permitted = host in ('megap.shiora.top', 'p19-ad-site-sign-sg.tiktokcdn.com', 'de3bv.eclipseharbor.world', 'yaf6t.emeralddrift.world')
        legacy_http = provider_id == 'animegg' and host in ('animegg.org', 'www.animegg.org') and parsed.scheme == 'http'
        allowed_ports = (vidcache_port,) if vidcache_port is not None else (None, 80 if legacy_http else 443)
        return bool((parsed.scheme == 'https' or legacy_http) and permitted and
                    parsed.port in allowed_ports and not parsed.username and not parsed.password and not parsed.fragment)
    except ValueError:
        return False


class HTTPFetcher:
    """Credential-free session with validated redirects and bounded streamed reads."""
    def __init__(self, *, allowed=None):
        from ..services.requirements import require_python
        from ..services.network_affinity import ipv4_curl_options
        self.session = require_python("curl_cffi.requests", "Health HTTP checks").Session(
            impersonate="chrome", trust_env=False, curl_options=ipv4_curl_options())
        self.allowed = allowed if allowed is not None else lambda url: media_destination_allowed("senshi", url)
        self.deadline = time.monotonic() + HTTP_BUDGET
        self.cache = {}

    def __call__(self, url, headers, limit, sample=False):
        url = urldefrag(url)[0]
        if not self.allowed(url):
            raise CheckIssue("Media destination not permitted")
        cache_key = (url, tuple(sorted(headers.items())), limit, sample)
        if cache_key in self.cache:
            return self.cache[cache_key]
        current = url
        request_headers = dict(headers)
        if sample:
            request_headers.setdefault("Range", f"bytes=0-{limit-1}")
        for hop in range(MAX_REDIRECTS + 1):
            remaining = self.deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError("HTTP source budget exceeded")
            self.session.cookies.clear()
            if urlsplit(current).scheme == 'http' or urlsplit(current).netloc != urlsplit(url).netloc:
                # Only provider-scoped legacy media permits HTTP. Never send
                # credentials or arbitrary source headers on cleartext/cross-origin hops.
                public_headers = {'user-agent', 'referer', 'origin', 'range', 'accept',
                                  'accept-language', 'accept-encoding'}
                request_headers = {key: value for key, value in request_headers.items()
                                   if key.casefold() in public_headers}
            with self.session.stream("GET", current, headers=request_headers, allow_redirects=False, timeout=min(8, remaining)) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    target = urldefrag(urljoin(current, response.headers.get("Location") or response.headers.get("location") or ""))[0]
                    if not self.allowed(target):
                        raise CheckIssue("Media redirect not permitted")
                    if hop == MAX_REDIRECTS:
                        raise CheckIssue("HTTP health redirect limit reached; media access unverified")
                    current = target
                    continue
                data = bytearray()
                truncated = False
                if 200 <= response.status_code < 300:
                    for chunk in response.iter_content(chunk_size=16384):
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError("HTTP source budget exceeded")
                        room = limit-len(data)
                        data.extend(chunk[:room])
                        if len(chunk) >= room:
                            truncated = True
                            break
                result = HTTPResult(current, response.status_code, bytes(data), truncated)
                self.cache[cache_key] = result
                return result
        raise CheckIssue("Redirect limit reached")


def probe_entries(entries, *, allowed=None):
    fetch = HTTPFetcher(allowed=allowed) if allowed is not None else HTTPFetcher()
    checks = []
    try:
        for index, entry in enumerate(entries):
            for check in probe_entry(entry, fetch):
                check.update(mirror_index=index, mirror=str(entry.get("resolution") or "auto"))
                if entry.get("channel"):
                    check["channel"] = str(entry["channel"])
                checks.append(check)
    finally:
        fetch.session.close()
    return checks
