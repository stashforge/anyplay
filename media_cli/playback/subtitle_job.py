"""One playback's detached subtitle preparation, with owned temporary files."""

import logging
import threading
from copy import deepcopy

log = logging.getLogger(__name__)


class SubtitleJob:
    def __init__(self, tracks, headers, referer, duration, local_files, *, score=None, files=None):
        self._lock = threading.Lock()
        self._retired = threading.Event()
        self._ready = False
        self._delivered = False
        self._tracks = []
        self._resource = None
        self.thread = threading.Thread(
            target=self._run,
            args=(deepcopy(tracks), dict(headers), referer, duration, local_files, score, files),
            name='anyplay-subtitles', daemon=True,
        )
        self.thread.start()

    def _run(self, tracks, headers, referer, duration, local_files, score, files):
        resource = None
        try:
            from ..core.reporting import status_sink_scope
            from ..media.mpv_subtitle_files import prepare_subtitle_files
            from ..media.subtitle_scorer import filter_subtitles
            from .desktop import _prepare_with_timing

            with status_sink_scope(lambda *_: True):
                try:
                    tracks = _prepare_with_timing(
                        'subtitle-filter', score or filter_subtitles, tracks,
                        headers=headers, referer=referer, video_duration_s=duration,
                        cancel_check=self._retired.is_set,
                    )
                except Exception as exc:
                    log.warning('Subtitle scoring unavailable (%s)', type(exc).__name__)
                if self._retired.is_set():
                    return
                if local_files and tracks:
                    try:
                        resource = _prepare_with_timing(
                            'subtitle-files', files or prepare_subtitle_files, tracks,
                            headers=headers, referer=referer, cancel_check=self._retired.is_set,
                        )
                        tracks = resource.subtitles
                        for hint in getattr(resource, 'dependency_hints', ()):
                            log.warning('%s', hint)
                    except Exception as exc:
                        log.warning('Subtitle files unavailable (%s); retaining remote tracks', type(exc).__name__)
                with self._lock:
                    if not self._retired.is_set():
                        self._tracks = tracks
                        self._resource = resource
                        resource = None
                        self._ready = True
        except Exception as exc:
            # Video remains usable even if an optional subtitle job fails.
            log.warning('Background subtitle preparation unavailable (%s)', type(exc).__name__)
        finally:
            if resource is not None:
                self._close_resource(resource)

    @staticmethod
    def _close_resource(resource):
        try:
            resource.close()
        except Exception as exc:
            log.warning('Subtitle cleanup unavailable (%s)', type(exc).__name__)

    def poll(self):
        with self._lock:
            if self._retired.is_set() or not self._ready or self._delivered:
                return None
            self._delivered = True
            return self._tracks

    def close(self):
        with self._lock:
            self._retired.set()
            resource, self._resource = self._resource, None
        if resource is not None:
            self._close_resource(resource)
