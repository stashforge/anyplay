"""Display helpers for torrent mirror rows."""

from __future__ import annotations

import re

from .models import TorrentResult


def human_size(size: int) -> str:
    value = float(size or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}TiB"


def quality_from_name(name: str) -> str:
    text = str(name or "")
    match = re.search(r"\b(2160|1440|1080|720|480|360)p\b", text, re.I)
    if match:
        return f"{match.group(1)}p"
    if re.search(r"\b4k\b", text, re.I):
        return "2160p"
    return ""


def _fmt_langs(langs: tuple[str, ...]) -> str:
    if not langs:
        return "Unknown"
    normalized = []
    for lang in langs[:3]:
        base = str(lang).split("-")[0].strip().upper()
        normalized.append(base or str(lang).upper())
    suffix = "+" if len(langs) > 3 else ""
    return ",".join(normalized) + suffix


def build_display_label(result: TorrentResult) -> str:
    group = result.group or "Unknown group"
    quality = quality_from_name(result.name)
    title = f"[Torrent] {group}"
    if quality:
        title = f"{title} {quality}"

    seeders = "seeders: Unknown" if result.seeders is None else f"{result.seeders} seeders"
    parts = [
        title,
        f"{_fmt_langs(result.audio_langs)} audio" if result.audio_langs else "audio: Unknown",
        f"{_fmt_langs(result.sub_langs)} subs" if result.sub_langs else "subs: Unknown",
        seeders,
        human_size(result.size_bytes),
    ]
    return " | ".join(parts)
