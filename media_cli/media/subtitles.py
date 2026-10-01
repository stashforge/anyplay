"""Download and save subtitles in native format."""

import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
from typing import Any

from .languages import filter_tracks_by_language, normalize_lang_code

_EXT_MAP = {
    "vtt": ".vtt",
    "webvtt": ".vtt",
    "srt": ".srt",
    "ass": ".ass",
    "ssa": ".ssa",
}


def detect_subtitle_format(url: str, content: bytes) -> str:
    """Detect subtitle file extension (.vtt, .srt, .ass) from URL or header bytes."""
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    for ext_key, ext_val in _EXT_MAP.items():
        if path.endswith(f".{ext_key}"):
            return ext_val

    # Inspect content header
    prefix = content[:512].decode("utf-8", errors="ignore").strip()
    if prefix.startswith("WEBVTT"):
        return ".vtt"
    if prefix.startswith("[Script Info]"):
        return ".ass"
    if re.search(r'\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}', prefix):
        return ".srt"

    return ".vtt"


def fetch_subtitle_bytes(url: str, headers: dict | None = None, referer: str | None = None) -> bytes:
    """Fetch raw subtitle bytes from URL with headers."""
    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
    }
    if headers:
        req_headers.update(headers)
    if referer and "Referer" not in req_headers:
        req_headers["Referer"] = referer

    req = urllib.request.Request(url, headers=req_headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def download_sidecar_subtitles(
    subtitles: list[dict],
    target_base_path: str,
    headers: dict | None = None,
    referer: str | None = None,
    requested_langs: Any = "multi",
    quiet: bool = False,
) -> list[str]:
    """Download subtitle streams as sidecar files alongside the video file.

    target_base_path: full path without extension, e.g. '/path/to/Show - Episode 01'
    Returns list of saved sidecar file paths.
    """
    if not subtitles:
        return []

    # If requested_langs is 'none' or False, user opted out of subtitles
    if requested_langs in ("none", "no", False, None):
        return []

    matched = filter_tracks_by_language(subtitles, requested_langs, is_audio=False)
    if not matched:
        return []

    saved_paths: list[str] = []
    used_langs: dict[str, int] = {}

    for sub in matched:
        sub_url = sub.get("url")
        if not sub_url:
            continue

        raw_lang = sub.get("language") or sub.get("lang") or sub.get("label") or "en"
        lang_code = normalize_lang_code(raw_lang, target="alpha2")

        count = used_langs.get(lang_code, 0)
        used_langs[lang_code] = count + 1
        lang_suffix = lang_code if count == 0 else f"{lang_code}-{count + 1}"

        try:
            data = fetch_subtitle_bytes(sub_url, headers=headers, referer=referer)
            if not data or len(data.strip()) == 0:
                continue

            ext = detect_subtitle_format(sub_url, data)
            sub_filename = f"{target_base_path}.{lang_suffix}{ext}"

            # Write file directly
            with open(sub_filename, "wb") as f:
                f.write(data)

            saved_paths.append(sub_filename)
            if not quiet:
                print(f"\033[1;32m✓ Saved subtitle:\033[0m {os.path.basename(sub_filename)}")
        except Exception as exc:
            if not quiet:
                print(f"\033[33m⚠ Failed to download subtitle ({sub_url}): {exc}\033[0m")

    return saved_paths


def remux_subtitles_into_mkv(
    video_path: str,
    subtitle_paths: list[str],
    output_mkv_path: str,
    quiet: bool = False,
) -> bool:
    """Losslessly remux a video file and subtitle files into an MKV container using ffmpeg -c copy.

    Returns True if remux succeeded and output file exists, False otherwise.
    """
    if not os.path.exists(video_path) or not subtitle_paths:
        return False
    if not shutil.which("ffmpeg"):
        return False

    cmd = ["ffmpeg", "-nostdin", "-y", "-i", video_path]
    for sub in subtitle_paths:
        cmd.extend(["-i", sub])

    cmd.extend(["-map", "0"])
    for idx, sub in enumerate(subtitle_paths, start=1):
        cmd.extend(["-map", f"{idx}:s:0"])
        stem = os.path.splitext(os.path.basename(sub))[0]
        parts = stem.split(".")
        raw_lang = parts[-1].split("-")[0] if len(parts) >= 2 else "und"
        lang_alpha3 = normalize_lang_code(raw_lang, target="alpha3")
        title = raw_lang.upper() if raw_lang != "und" else "Subtitles"
        cmd.extend([f"-metadata:s:s:{idx-1}", f"language={lang_alpha3}"])
        cmd.extend([f"-metadata:s:s:{idx-1}", f"title={title}"])

    cmd.extend(["-c", "copy", output_mkv_path])

    try:
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
            check=True,
        )
        return os.path.exists(output_mkv_path) and os.path.getsize(output_mkv_path) > 0
    except Exception as exc:
        if not quiet:
            print(f"\033[33m⚠ Subtitle remux failed: {exc}\033[0m")
        return False
