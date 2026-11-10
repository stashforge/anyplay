"""Small HLS playlist builders."""

from __future__ import annotations

from functools import lru_cache
import subprocess
from urllib.parse import urlsplit


@lru_cache(maxsize=1)
def _ffmpeg_has_extension_picky() -> bool:
    """Older FFmpeg builds lack the HLS format/URL-extension check."""
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-h", "demuxer=hls"],
            capture_output=True, text=True, timeout=3,
        )
        return result.returncode == 0 and "-extension_picky" in result.stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def ffmpeg_hls_input_args(url: str) -> list[str]:
    """Allow proxied/extensionless HLS segments, including WebVTT renditions."""
    if not urlsplit(str(url)).path.lower().endswith(".m3u8"):
        return []
    if not _ffmpeg_has_extension_picky():
        return []
    return ["-allowed_extensions", "ALL", "-extension_picky", "0"]


def _playlist_text(value: object) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def build_titled_hls_manifest(stream_url: str, title: str) -> str:
    safe_title = _playlist_text(title) or "Stream"
    safe_url = _playlist_text(stream_url)
    return (
        "#EXTM3U\n"
        f"#EXTINF:-1,{safe_title}\n"
        f"{safe_url}\n"
    )
