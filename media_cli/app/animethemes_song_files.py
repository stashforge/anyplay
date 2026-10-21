"""Shared song download identity and local-file selection, without episode state."""

import hashlib
import json
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from ..media.download import sanitize_filename
from ..media.urls import validate_http_url
from .animethemes_song_title import song_player_title


@dataclass(frozen=True)
class SongFileTarget:
    digest: str
    root: Path
    path: Path

    @property
    def item_id(self) -> str:
        return f"animethemes:song:{self.digest}"


def song_file_target(row, cfg):
    url = validate_http_url(row["stream"]["link"])
    raw = row.get("raw") or {}
    theme, entry, video = (raw.get(key) or {} for key in ("theme", "entry", "video"))
    identity = [theme.get("id"), entry.get("id"), video.get("id"), None if video.get("id") else url]
    digest = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()[:20]
    root = cfg.get("download_dir")
    if not root:
        from ..core.storage import get_default_download_dir
        root = get_default_download_dir()
    root = Path(str(root)).expanduser().resolve()
    directory = root / "AnimeThemes" / (sanitize_filename(row.get("anime"))[:80] or "Songs")
    name = sanitize_filename(song_player_title(row))[:120] or "Song"
    return SongFileTarget(digest, root, directory / f"{name} - {digest}.mkv")


def find_song_file(row, cfg):
    from ..core.storage import is_valid_download_video

    target = song_file_target(row, cfg)
    directory = target.root / "AnimeThemes"
    try:
        if not directory.is_dir():
            return None
        from ..media.download_worker import get_global_worker_if_started

        worker = get_global_worker_if_started()
        if worker is not None and any(item.id == target.item_id and item.status not in ("completed", "skipped")
                                      for item in worker.get_items()):
            return None
        candidates = chain((target.path,), directory.glob(f"*/* - {target.digest}.*"))
        for path in candidates:
            if path.with_name(path.name + ".aria2").exists() or path.with_name(path.name + ".ytdl").exists():
                continue
            if next(path.parent.glob(path.name + ".part*"), None) is not None:
                continue
            if is_valid_download_video(str(path)):
                return str(path)
    except OSError:
        return None
    return None


def local_song_row(row, cfg):
    path = find_song_file(row, cfg)
    if path is None:
        return row
    return {**row, "stream": {**row["stream"], "link": path, "type": "local", "is_local": True,
                              "source_name": "Local", "requires_proxy": False, "headers": {}, "referer": ""}}
