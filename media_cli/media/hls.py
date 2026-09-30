"""Small HLS playlist builders."""

from __future__ import annotations


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
