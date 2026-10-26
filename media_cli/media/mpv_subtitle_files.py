"""Session-owned subtitle files, kept outside mpv's HLS demuxer."""

import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from ..services.requirements import MissingPythonDependency, optional_dependency_hint
from .subtitle_playlist import SubtitleAssemblyCancelled
from .subtitles import detect_subtitle_format, fetch_prepared_subtitle_bytes

log = logging.getLogger(__name__)


class SubtitleFilesCancelled(Exception):
    """Subtitle preparation was retired before playback."""


class PreparedSubtitleFiles:
    def __init__(self, subtitles):
        self.subtitles = [dict(track) for track in subtitles]
        self.dependency_hints = []
        self._directory = TemporaryDirectory(prefix="anyplay-subs-")

    def close(self):
        self._directory.cleanup()


def prepare_subtitle_files(subtitles, *, headers=None, referer=None, cancel_check=None):
    resource = PreparedSubtitleFiles(subtitles)

    def check_cancel():
        if cancel_check and cancel_check():
            raise SubtitleFilesCancelled

    try:
        for index, track in enumerate(resource.subtitles):
            check_cancel()
            url = track.get("url") or track.get("file")
            if not url or urlsplit(url).scheme.lower() not in ("http", "https"):
                continue
            try:
                data = fetch_prepared_subtitle_bytes(url, headers=headers, referer=referer, cancel_check=cancel_check)
                check_cancel()
                prefix = data.decode("utf-8-sig", errors="replace").lstrip()
                # Do not save playlists or HTML errors as subtitle files.
                if not prefix.startswith(("WEBVTT", "[Script Info]")) and "-->" not in prefix:
                    raise ValueError("Not a standalone subtitle file")
                path = Path(resource._directory.name) / f"{index}{detect_subtitle_format(url, data)}"
                path.write_bytes(data)
                track["url"] = str(path)
                track.pop("file", None)
            except (SubtitleFilesCancelled, SubtitleAssemblyCancelled) as exc:
                raise SubtitleFilesCancelled from exc
            except MissingPythonDependency:
                hint = optional_dependency_hint(
                    "m3u8", fallback="Remote HLS subtitles may repeat after seeking",
                    feature="local subtitle preparation",
                )
                if hint not in resource.dependency_hints:
                    resource.dependency_hints.append(hint)
            except Exception as exc:
                log.warning("Local subtitle preparation failed (%s); retaining remote track", type(exc).__name__)
        check_cancel()
        return resource
    except BaseException:
        resource.close()
        raise
