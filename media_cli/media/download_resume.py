"""Rebuild download work from stable identities rather than expired session URLs."""

import re
from urllib.parse import urlsplit


def request_for(item):
    """Only uncredentialed static links qualify for direct-link restoration."""
    if item.resume_request:
        return dict(item.resume_request)
    show = item.show_metadata
    provider = show.get("_provider") or show.get("provider")
    show_id = show.get("_provider_id") or show.get("_id") or show.get("id")
    if provider and show_id and item.ep_id is not None and provider != "animethemes":
        return {
            "kind": "provider",
            "provider": str(provider),
            "show_id": str(show_id),
            "episode_id": str(item.ep_id),
            "translation": "sub",
            "quality": "best",
        }
    if not item.resolve_fn and not item.headers and item.url:
        parts = urlsplit(item.url)
        if (
            parts.scheme in {"http", "https"}
            and not parts.query
            and not parts.username
            and parts.hostname not in {"localhost", "127.0.0.1", "::1"}
        ):
            return {"kind": "direct", "url": item.url}
    return {}


def complete_restored(item):
    if item.show_metadata.get("_provider") == "animethemes":
        return
    from ..core.storage import register_downloaded_episode_files
    from .download import sanitize_filename

    register_downloaded_episode_files(
        sanitize_filename(item.title) or item.title,
        str(item.ep_id if item.ep_id is not None else item.episode),
        item.output_files or [item.destination_path],
        metadata=item.show_metadata,
    )


def resolve_restored(item):
    if item.engine == "libtorrent" or item.torrent:
        from ..torrents.models import TorrentResult

        metadata = item.torrent
        infohash = str(metadata.get("infohash") or "")
        if not re.fullmatch("[a-fA-F0-9]{40}", infohash) or type(metadata.get("file_index")) is not int:
            raise ValueError("Saved torrent identity is incomplete; reselect this file.")
        item.logs.append("Resuming torrent: checking saved pieces and fetching missing data.")
        return (
            TorrentResult(
                source="saved",
                name=item.title,
                infohash=infohash,
                tracker_urls=tuple(metadata.get("tracker_urls") or ()),
            ).magnet,
            {},
            None,
        )
    request = item.resume_request
    if request.get("kind") == "direct":
        from .urls import validate_http_url

        return validate_http_url(request["url"]), {}, None
    from ..app_core import fetch_episode_stream
    from .download_worker import prepare_download_proxy

    item.logs.append("Refreshing stream links for saved download.")
    result = fetch_episode_stream(
        request["show_id"],
        request["episode_id"],
        request.get("translation", "sub"),
        request.get("quality", "best"),
        provider_id=request["provider"],
        exclude_sources=item.failed_mirrors,
    )
    if not result or not result[0]:
        raise RuntimeError("No stream found for saved download.")
    stream = result[0]
    url = stream.get("link") or stream.get("streamUrl") or stream.get("sourceUrl")
    if not url:
        raise RuntimeError("No stream URL returned for saved download.")
    headers = dict(stream.get("headers") or {})
    referer = stream.get("referer", "")
    if referer:
        headers.setdefault("Referer", referer)
    item.current_mirror_name = result[1] or stream.get("source_name") or "selected mirror"
    item.current_mirror_url = url
    item.audio_tracks = list(stream.get("audio_tracks") or [])
    item.audio_url = stream.get("audio_url", "")
    item.subtitles = list(stream.get("subtitles") or stream.get("vtt") or [])
    item.subtitle_url = stream.get("subtitle_url", "")
    item.provider_skips = stream.get("skip_intervals") or []
    url, proxy = prepare_download_proxy(stream, url, headers, referer)
    return url, headers, proxy


def bind_restored(item):
    if not item.restored:
        return True
    if not (item.torrent or item.resume_request.get("kind") in {"direct", "provider"}):
        item.error = "Saved entry lacks provider identity; reselect this file once to enable resume."
        item.logs.append(item.error)
        return False
    if item.engine == "ffmpeg":
        item.logs.append("FFmpeg remux cannot append safely; this item will restart using fresh links.")
    item.resolve_fn = resolve_restored
    item.on_complete = complete_restored
    item.url = ""
    item.restored = False
    item._resume_existing = True
    item.logs.append("Resume queued. Existing partial data will be reused when supported by the download engine.")
    return True
