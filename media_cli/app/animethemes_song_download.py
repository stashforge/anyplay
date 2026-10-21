"""Explicit song downloads reuse the common worker without episode registration."""

from ..media.download_worker import DownloadItem, get_global_worker
from ..media.urls import validate_http_url, validate_optional_referer
from ..ui.download_manager import run_download_manager
from .animethemes_song_files import find_song_file, song_file_target


def _prepare_download(row, cfg, args):
    stream = row["stream"]
    url = validate_http_url(stream["link"])
    target = song_file_target(row, cfg)
    if find_song_file(row, cfg):
        return None
    headers = dict(stream.get("headers") or {})
    referer = validate_optional_referer(stream.get("referer"))
    if referer:
        headers["Referer"] = referer
    raw = row.get("raw") or {}
    theme = raw.get("theme") or {}
    engine = getattr(args, "downloader", None) or cfg.get("downloader", "auto")
    item = DownloadItem(
        id=target.item_id, title=row.get("anime") or row.get("song") or "AnimeThemes",
        episode=f"{row.get('song') or 'Song'} {row.get('theme') or ''}".strip(),
        destination_path=str(target.path), url=url, headers=headers,
        engine=engine if engine in ("yt-dlp", "aria2c", "ffmpeg") else "auto",
        ep_id=f"theme:{theme.get('id') or target.digest}",
        show_metadata={"_provider": "animethemes", "_id": f"animethemes:theme:{theme.get('id') or target.digest}"},
        automatic=False,
    )
    return target, item


def download_songs(rows, cfg, args):
    rows = tuple(rows)
    if not rows:
        raise ValueError("Select at least one song")
    jobs = {}
    for row in rows:
        prepared = _prepare_download(row, cfg, args)
        if prepared is not None:
            jobs[prepared[1].id] = prepared
    if not jobs:
        return "LOCAL"
    root = next(iter(jobs.values()))[0].root
    worker = get_global_worker(str(root), max_concurrent=cfg.get("download_slots", 2))
    for _, item in jobs.values():
        worker.add_item(item)
    return run_download_manager(worker, standalone=False)


def download_song(row, cfg, args):
    return download_songs((row,), cfg, args)
