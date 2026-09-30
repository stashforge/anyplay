"""Resolve provider source entries into playable streams."""

import re
import urllib.request

from ..core.terminal import sanitize_terminal_text
from ..extractors import find_extractor
from ..providers.allanime import get_clock_links
from ..services.http import (
    REFERER,
    SSL_CTX,
    UA,
    get_size,
    is_alive,
    request_json,
)
from .dash import resolve_dash_raw_urls
from .proxy_rules import proxy_filtered_headers
from .sources import decrypt_url, expand_wixmp, source_priority
from .urls import validate_optional_referer, validate_stream_url
from .ytdlp import resolve_ytdlp_embed


def _info(message):
    return None
def _ok(message):
    return None
def _warn(message):
    return None


def configure_reporters(info, ok, warn):
    global _info, _ok, _warn
    _info = info
    _ok = ok
    _warn = warn


def generate_source_passes(sources, max_passes=3):
    pending = list(sources)
    for pass_idx in range(max_passes):
        failed = []
        is_final_pass = (pass_idx == max_passes - 1)
        for src in pending:
            yield src, failed, pass_idx > 0, is_final_pass
        pending = failed
        if not pending:
            break


def _pre_resolved_stream(source, name, priority, warn):
    # Only direct source entries should reach this path.  Embed/page mirrors
    # must use ``sourceUrl`` so extractor-specific logic can turn them into
    # player-ready streams first.
    stream_url = source.get("link") or source.get("streamUrl") or ""
    if not stream_url and source.get("sourceUrl"):
        s_type = str(source.get("type") or "").strip().lower()
        s_url = str(source.get("sourceUrl") or "")
        if s_type in {"mp4", "hls", "m3u8", "dash"} or any(ext in s_url for ext in (".m3u8", ".mp4", ".mkv", "/download/", "127.0.0.1")):
            stream_url = s_url
    if not stream_url:
        return None
    try:
        stream_url = validate_stream_url(stream_url)
    except ValueError:
        warn(f"[{name}] rejected an unsafe stream URL")
        return []

    headers = {
        key: value
        for key, value in proxy_filtered_headers(source.get("headers") or {}).items()
        if key.casefold() not in {"authorization", "cookie"}
    }
    referer = source.get("referer") or headers.get("Referer", "")
    try:
        referer = validate_optional_referer(referer)
    except ValueError:
        referer = ""
        headers = {
            key: value
            for key, value in headers.items()
            if key.casefold() != "referer"
        }

    stream_type = str(source.get("type") or "").strip().lower()
    if stream_type not in {"mp4", "hls", "external", "dash"}:
        stream_type = "hls" if ".m3u8" in stream_url else "mp4"

    resolution = str(source.get("resolution") or "Adaptive")
    android_safe = source.get("android_safe")
    if android_safe is None:
        android_safe = stream_type == "mp4" or (
            stream_type == "hls" and bool(referer or headers)
        )

    stream_dict = {
        "source_name": name,
        "link": stream_url,
        "type": stream_type,
        "resolution": resolution,
        "referer": referer,
        "headers": headers,
        "source_priority": priority,
        "android_safe": bool(android_safe),
    }
    if (
        source.get("requires_proxy")
        or "megap." in stream_url
        or "akirax.buzz" in stream_url
        or "owocdn.top" in stream_url
        or "uwucdn.top" in stream_url
        or "kwik." in referer
    ):
        stream_dict["requires_proxy"] = True
    if source.get("subtitle_url"):
        stream_dict["subtitle_url"] = source["subtitle_url"]
    if source.get("subtitles"):
        stream_dict["subtitles"] = source["subtitles"]
        if not stream_dict.get("subtitle_url"):
            subs = source["subtitles"]
            if isinstance(subs, list) and subs:
                def_sub = next((s.get("url") or s.get("file") for s in subs if s.get("default")), None)
                if not def_sub:
                    def_sub = next((s.get("url") or s.get("file") for s in subs if "eng" in str(s.get("label", "")).lower()), None)
                if not def_sub and isinstance(subs[0], dict):
                    def_sub = subs[0].get("url") or subs[0].get("file")
                if def_sub:
                    stream_dict["subtitle_url"] = def_sub

    return [stream_dict]



def _extract_mp4upload(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://youtu-chan.com/",
        },
    )
    with urllib.request.urlopen(
        request,
        context=SSL_CTX,
        timeout=12,
    ) as response:
        page = response.read(2 * 1024 * 1024).decode(
            "utf-8",
            errors="ignore",
        )
    match = re.search(
        r"""src\s*:\s*["']([^"']+)["']""",
        page,
        re.IGNORECASE,
    )
    if not match:
        return ""
    return validate_stream_url(
        match.group(1).replace(r"\/", "/")
    )


def resolve_source(source, silent=False):
    streams = _resolve_source_impl(source, silent=silent)
    raw_name = str(source.get("sourceName") or "")
    clean_name = sanitize_terminal_text(raw_name).title()
    for s in streams or []:
        if isinstance(s, dict):
            s.setdefault("source_parent_name", clean_name)
            s.setdefault("raw_source_name", raw_name)
    return streams


def _resolve_source_impl(source, silent=False):
    name = sanitize_terminal_text(source.get("sourceName", "?")).title()
    name = re.sub(
        r"(?i)\b(mp4|hls|cdn|hd|tv)\b",
        lambda match: match.group(1).upper(),
        name,
    )
    name = re.sub(r"(?i)mp4upload", "Mp4Upload", name)
    url = source.get("sourceUrl", "")
    priority = source_priority(source)

    def info(message):
        if not silent:
            _info(message)

    def ok(message):
        if not silent:
            _ok(message)

    def warn(message):
        if not silent:
            _warn(message)

    result = []
    pre_resolved = _pre_resolved_stream(source, name, priority, warn)
    if pre_resolved is not None:
        return pre_resolved

    if not url:
        return result
    if not url.startswith("--"):
        try:
            url = validate_stream_url(url)
        except ValueError:
            warn(f"[{name}] rejected an unsafe stream URL")
            return result

    if "fast4speed" in url or "wixstatic" in url:
        info(f"[{name}] testing direct CDN ...")
        referer = "" if "wixstatic" in url else REFERER
        if is_alive(url, referer=referer):
            size = get_size(url, referer=referer)
            size_text = f"~{size // (1024 * 1024)} MB" if size else "?"
            ok(f"[{name}] direct mp4  size:{size_text}")
            result.append({
                "source_name": name,
                "link": url,
                "type": "mp4",
                "resolution": "1080p",
                "referer": referer,
                "headers": {},
                "source_priority": priority,
                "android_safe": True,
            })
        return result

    if url.startswith("--"):
        decrypted_path = decrypt_url(url[2:])
        info(f"[{name}] decrypting -> clock ...")
        try:
            items = get_clock_links(request_json, decrypted_path)
        except Exception as e:
            warn(f"[{name}] clock API error: {e}")
            items = []
        if not items:
            warn(f"[{name}] clock returned no links")
            return result

        for item in items:
            dash_streams = resolve_dash_raw_urls(item, name, priority)
            if dash_streams:
                ok(
                    f"[{name}] DASH video + audio "
                    f"({dash_streams[0]['resolution']})"
                )
                result.extend(dash_streams)
                continue

            link = item.get("link", "")
            resolution = item.get("resolutionStr", "Adaptive")
            if not link:
                continue
            if "repackager.wixmp.com" in link:
                for label, mp4_url in expand_wixmp(link).items():
                    info(f"[{name}] testing wixmp {label} ...")
                    if is_alive(mp4_url, referer=""):
                        size = get_size(mp4_url, referer="")
                        size_text = (
                            f"~{size // (1024 * 1024)} MB"
                            if size
                            else "~340 MB"
                        )
                        ok(f"[{name}] wixmp-mp4 ({label})  size:{size_text}")
                        result.append({
                            "source_name": f"{name} wixmp ({label})",
                            "link": mp4_url,
                            "type": "mp4",
                            "resolution": label,
                            "referer": "",
                            "headers": {},
                            "source_priority": priority,
                            "android_safe": True,
                        })
                continue

            info(f"[{name}] testing {resolution} ...")
            working_referer = None
            for referer in (
                "",
                REFERER,
                "https://gogoanime.tel/",
                "https://anitaku.pe/",
                "https://yugenanime.tv/",
            ):
                if is_alive(link, referer=referer, timeout=6):
                    working_referer = referer
                    break
            if working_referer is not None:
                stream_type = "hls" if ".m3u8" in link else "mp4"
                ok(
                    f"[{name}] {stream_type} ({resolution})  "
                    f"ref:'{working_referer or 'none'}'"
                )
                result.append({
                    "source_name": f"{name} ({resolution})",
                    "link": link,
                    "type": stream_type,
                    "resolution": resolution,
                    "referer": working_referer,
                    "headers": {},
                    "source_priority": priority,
                    "android_safe": stream_type == "mp4",
                })
        return result

    extractor = find_extractor(url)
    if extractor:
        info(f"[{name}] extracting via {extractor.name} ...")
        try:
            native_streams = extractor.extract(
                url,
                name=name,
                priority=priority,
                subtitles=source.get("subtitles"),
                headers=source.get("headers"),
                referer=source.get("referer"),
            )
            if native_streams:
                ok(f"[{name}] {extractor.name} found {len(native_streams)} stream(s)")
                return native_streams
            warn(f"[{name}] {extractor.name} found no streams; falling back to yt-dlp ...")
        except Exception as exc:
            warn(f"[{name}] {extractor.name} failed: {exc}; falling back to yt-dlp ...")

    if "mp4upload.com" in url.casefold():
        info(f"[{name}] extracting direct mp4 ...")
        try:
            stream_url = _extract_mp4upload(url)
        except Exception as exc:
            warn(f"[{name}] direct extraction failed: {exc}")
            stream_url = ""
        referer = "https://mp4upload.com/"
        if stream_url and is_alive(stream_url, referer=referer):
            size = get_size(stream_url, referer=referer)
            size_text = f"~{size // (1024 * 1024)} MB" if size else "?"
            ok(f"[{name}] direct mp4  size:{size_text}")
            result.append({
                "source_name": name,
                "link": stream_url,
                "type": "mp4",
                "resolution": "Adaptive",
                "referer": referer,
                "headers": {},
                "source_priority": priority,
                "android_safe": True,
            })
            return result

    info(f"[{name}] extracting via yt-dlp ...")
    return resolve_ytdlp_embed(url, name=name, priority=priority, ok=ok, warn=warn, subtitles=source.get("subtitles"))

