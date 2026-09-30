"""yt-dlp based embed extraction."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import time
import urllib.parse

from ..core.processes import read_bounded_process_stdout
from .dailymotion import is_dailymotion_url, stream_type_from_url
from .sources import format_source_label, quality_from_dimensions
from .urls import validate_stream_url


def resolve_ytdlp_embed(url: str, *, name: str, priority: int, ok, warn, subtitles: list[dict] | None = None) -> list[dict]:
    if not shutil.which("yt-dlp"):
        warn(f"[{name}] yt-dlp not found, skipping embed")
        return []

    attempts = 5
    command = ["yt-dlp", "-j", "--no-warnings"]
    if "ok.ru" not in url and "odnoklassniki.ru" not in url:
        command.extend(["--impersonate", "chrome"])
    command.append(url)
    data = None
    last_error = ""
    is_unsupported = False

    PERMANENT_ERRORS = [
        b"Unsupported URL",
        b"Cloudflare anti-bot",
        b"Geo-restricted",
    ]

    for attempt in range(1, attempts + 1):
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            output = read_bounded_process_stdout(process, timeout=20)
            if process.returncode != 0:
                if b"Impersonate target" in output and b"not available" in output:
                    command = ["yt-dlp", "-j", "--no-warnings", url]
                    continue

                if any(err in output for err in PERMANENT_ERRORS):
                    is_unsupported = True
                    last_error = "Unsupported/Blocked URL"
                    break

                err_msg = output.strip()[:100].decode("utf-8", errors="replace")
                last_error = f"yt-dlp exited with {process.returncode}: {err_msg}"
                continue
            data = json.loads(output)
            break
        except subprocess.TimeoutExpired:
            last_error = "yt-dlp timed out"
        except Exception as exc:
            last_error = f"yt-dlp failed: {exc}"
        if attempt < attempts and not is_unsupported:
            warn(f"[{name}] {last_error}; retrying")
            time.sleep(0.5 + random.random() * 0.5)
    if data is None:
        if last_error:
            warn(f"[{name}] {last_error}")
        return None if is_unsupported else []

    streams = streams_from_ytdlp_data(data, url=url, name=name, priority=priority, parent_subtitles=subtitles)
    if streams:
        ok(f"[{name}] yt-dlp found {len(streams)} stream(s)")
    return streams


def _stream_score(stream: dict) -> tuple[int, int]:
    return int(stream.get("_quality_rank") or 0), int(stream.get("_bitrate") or 0)


def _stream_type(item: dict, stream_url: str) -> str:
    protocol = str(item.get("protocol") or "").casefold()
    manifest_url = str(item.get("manifest_url") or "")
    parsed = urllib.parse.urlparse(str(stream_url or ""))
    query = urllib.parse.unquote(parsed.query).casefold()
    path = urllib.parse.unquote(parsed.path).casefold()
    if "m3u8" in protocol or ".m3u8" in path or ".m3u8" in query or manifest_url:
        return "hls"
    return stream_type_from_url(stream_url)


def _resolution_label(item: dict) -> str:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width or height:
        label, _ = quality_from_dimensions(width, height)
        return label
    resolution = str(item.get("resolution") or "")
    if resolution and resolution != "audio only":
        dim_m = re.search(r"(\d+)\s*[x×]\s*(\d+)", resolution)
        if dim_m:
            w, h = int(dim_m.group(1)), int(dim_m.group(2))
            lbl, _ = quality_from_dimensions(w, h)
            return lbl
        return resolution
    return "Adaptive"


def _is_useful_quality(item: dict) -> bool:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width and height:
        return width >= 1280
    return height >= 720


def _quality_rank(item: dict) -> int:
    width = int(item.get("width") or 0)
    height = int(item.get("height") or 0)
    if width or height:
        _, rank = quality_from_dimensions(width, height)
        return rank
    return 0


def _bitrate(item: dict) -> int:
    return int(float(item.get("tbr") or item.get("vbr") or item.get("abr") or 0))


def _find_best_audio(formats: list[dict]) -> dict | None:
    """Find the best audio-only format."""
    audio_formats = [
        f for f in formats
        if f.get("vcodec") == "none"
        and (
            f.get("acodec") not in (None, "none")
            or f.get("audio_ext") not in (None, "none")
            or f.get("resolution") == "audio only"
        )
        and f.get("url")
    ]
    if not audio_formats:
        return None
    return max(audio_formats, key=lambda f: int(float(f.get("abr") or f.get("tbr") or 0)))


def _find_all_audio(formats: list[dict]) -> list[dict]:
    """Find all audio-only formats."""
    return [
        f for f in formats
        if f.get("vcodec") == "none"
        and (
            f.get("acodec") not in (None, "none")
            or f.get("audio_ext") not in (None, "none")
            or f.get("resolution") == "audio only"
        )
        and f.get("url")
    ]


def _is_video_format(item: dict) -> bool:
    """True if this format entry is a video (not audio-only, has video stream)."""
    vcodec = item.get("vcodec")
    acodec = item.get("acodec")
    # explicitly audio-only
    if vcodec == "none":
        return False
    # no codec info and no video_ext — skip (timeline thumbnails etc.)
    video_ext = item.get("video_ext")
    if vcodec is None and video_ext in (None, "none"):
        return bool(item.get("width") or item.get("height"))
    # has acodec but no vcodec/video_ext — pure audio
    if acodec not in (None, "none") and vcodec is None and not video_ext:
        return False
    return True


def _stream_from_format(
        item: dict,
        data: dict,
        *,
        name: str,
        priority: int,
        audio_format: dict | None = None,
        audio_formats: list[dict] | None = None,
        is_dailymotion: bool = False) -> dict | None:
    stream_url = item.get("url")
    if not stream_url:
        return None
    try:
        stream_url = validate_stream_url(stream_url)
    except ValueError:
        return None

    stream_type = _stream_type(item, stream_url)
    resolution = _resolution_label(item)
    label = format_source_label(name, resolution)

    needs_audio = item.get("acodec") == "none"
    stream: dict = {
        "source_name": label,
        "source_parent_name": name,
        "link": stream_url,
        "type": stream_type,
        "resolution": resolution,
        "referer": "",
        "headers": {},
        "source_priority": priority,
        "android_safe": not needs_audio and (
            stream_type in {"mp4", "hls"}
        ),
        "_quality_rank": _quality_rank(item),
        "_bitrate": _bitrate(item),
    }

    if needs_audio and audio_formats:
        audio_tracks = []
        for af in audio_formats:
            a_url = af.get("url")
            if not a_url:
                continue
            try:
                a_url = validate_stream_url(a_url)
            except ValueError:
                continue
            lang = str(af.get("language") or "").lower()
            track_label = af.get("format_note") or af.get("format_id") or lang or "Audio"
            audio_tracks.append({
                "url": a_url,
                "label": str(track_label),
                "language": lang,
                "default": lang in ("ja", "jp", "jpn", "japanese"),
            })

        if audio_tracks and not any(t.get("default") for t in audio_tracks):
            audio_tracks[0]["default"] = True

        if audio_tracks:
            def_track = next((t for t in audio_tracks if t.get("default")), audio_tracks[0])
            stream["audio_tracks"] = audio_tracks
            stream["audio_url"] = def_track["url"]
            stream["android_safe"] = True
            stream["split_video_url"] = stream_url
            stream["split_audio_url"] = def_track["url"]
            stream["split_width"] = item.get("width") or 1280
            stream["split_height"] = item.get("height") or 720
            stream["split_bandwidth"] = item.get("tbr") or item.get("vbr") or 2400
            if is_dailymotion:
                stream["dailymotion_video"] = stream_url
                stream["dailymotion_audio"] = def_track["url"]
                stream["dailymotion_width"] = item.get("width") or 1280
                stream["dailymotion_height"] = item.get("height") or 720
                stream["dailymotion_bandwidth"] = item.get("tbr") or item.get("vbr") or 2400
    elif needs_audio and audio_format:
        try:
            audio_url = validate_stream_url(audio_format.get("url"))
        except ValueError:
            audio_url = None
        if audio_url:
            stream["audio_url"] = audio_url
            stream["android_safe"] = True
            stream["split_video_url"] = stream_url
            stream["split_audio_url"] = audio_url
            stream["split_width"] = item.get("width") or 1280
            stream["split_height"] = item.get("height") or 720
            stream["split_bandwidth"] = item.get("tbr") or item.get("vbr") or 2400
            if is_dailymotion:
                stream["dailymotion_video"] = stream_url
                stream["dailymotion_audio"] = audio_url
                stream["dailymotion_width"] = item.get("width") or 1280
                stream["dailymotion_height"] = item.get("height") or 720
                stream["dailymotion_bandwidth"] = item.get("tbr") or item.get("vbr") or 2400

    return stream


def _manifest_stream_from_format(
        item: dict,
        *,
        name: str,
        priority: int) -> dict | None:
    manifest_url = item.get("manifest_url")
    if not manifest_url:
        return None
    try:
        manifest_url = validate_stream_url(manifest_url)
    except ValueError:
        return None
    if ".m3u8" not in urllib.parse.urlparse(manifest_url).path.casefold():
        return None
    return {
        "source_name": f"{name} [HLS] Adaptive",
        "link": manifest_url,
        "type": "hls",
        "resolution": "Adaptive",
        "referer": "",
        "headers": {},
        "source_priority": priority,
        "android_safe": True,
        "_quality_rank": 0,
        "_bitrate": 0,
    }


def streams_from_ytdlp_data(data: dict, *, url: str, name: str, priority: int, parent_subtitles: list[dict] | None = None) -> list[dict]:
    formats = data.get("formats", [])
    _is_dm = is_dailymotion_url(url)

    # Extract soft subtitles from yt-dlp data if present
    subtitles = []
    raw_subs = data.get("subtitles") or data.get("automatic_captions") or {}
    if isinstance(raw_subs, dict):
        for lang, sub_list in raw_subs.items():
            if isinstance(sub_list, list):
                for s in sub_list:
                    s_url = s.get("url")
                    s_ext = s.get("ext", "").lower()
                    if s_url and (s_ext in ("vtt", "srt", "ass") or ".vtt" in s_url or ".srt" in s_url):
                        subtitles.append({
                            "label": lang.title(),
                            "url": s_url,
                            "default": lang.lower() in ("en", "eng", "english", "en-us"),
                        })
    if not subtitles and parent_subtitles:
        subtitles = list(parent_subtitles)

    # find all audio-only formats for multi-audio support
    all_audios = _find_all_audio(formats) if formats else []
    best_audio = _find_best_audio(formats) if formats else None

    streams = []
    seen_urls: set[str] = set()

    for item in formats:
        if not _is_video_format(item):
            continue
        if not _is_useful_quality(item):
            continue

        stream = _stream_from_format(
            item,
            data,
            name=name,
            priority=priority,
            audio_format=best_audio if item.get("acodec") == "none" else None,
            audio_formats=all_audios if item.get("acodec") == "none" else None,
            is_dailymotion=_is_dm,
        )
        if not stream:
            continue
        link = stream.get("link", "")
        if link in seen_urls:
            continue
        seen_urls.add(link)
        if subtitles:
            stream["subtitles"] = list(subtitles)
            def_sub = next((s["url"] for s in subtitles if s.get("default")), subtitles[0]["url"])
            stream["subtitle_url"] = def_sub
        streams.append(stream)

    streams.sort(key=_stream_score, reverse=True)
    for item in formats:
        if not _is_video_format(item):
            continue
        stream = _manifest_stream_from_format(
            item,
            name=name,
            priority=priority,
        )
        if not stream:
            continue
        link = stream.get("link", "")
        if link in seen_urls:
            continue
        seen_urls.add(link)
        if subtitles:
            stream["subtitles"] = list(subtitles)
            def_sub = next((s["url"] for s in subtitles if s.get("default")), subtitles[0]["url"])
            stream["subtitle_url"] = def_sub
        streams.append(stream)

    _is_okru = "ok.ru" in url or "odnoklassniki.ru" in url or name.casefold() == "ok"
    if _is_okru:
        has_adaptive = any(s.get("resolution") == "Adaptive" for s in streams)
        if has_adaptive:
            streams = [s for s in streams if s.get("resolution") == "Adaptive"]

    return streams
