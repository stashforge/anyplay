"""Versioned queue history; store stable resolution requests, never session credentials or callbacks."""

import json
import logging
import os
import threading

from ..state.io import atomic_write_json

_log = logging.getLogger(__name__)
FIELDS = (
    "id",
    "title",
    "episode",
    "destination_path",
    "engine",
    "progress",
    "downloaded_bytes",
    "total_bytes",
    "created_at",
    "started_at",
    "completed_at",
    "ep_id",
    "output_files",
    "automatic",
    "download_sub_langs",
    "download_audio_langs",
    "subtitle_mode",
    "resume_request",
)
IDENTITY = (
    "_provider",
    "_provider_id",
    "_id",
    "id",
    "provider",
    "provider_id",
    "title",
    "name",
    "url",
    "anilist_id",
    "mal_id",
    "malId",
    "anilistId",
)


class QueueStore:
    def __init__(self, path):
        self.path = str(path)
        self.lock = threading.Lock()
        self.previous = None

    def save(self, items):
        records = []
        for item in items:
            record = {key: getattr(item, key) for key in FIELDS}
            from .download_resume import request_for

            record["resume_request"] = request_for(item)
            record["status"] = item.status.value
            record["completion_state_version"] = 1
            # Provider identity is sufficient for a later fresh resolution step.
            record["show_metadata"] = {
                key: value
                for key, value in item.show_metadata.items()
                if key in IDENTITY and isinstance(value, (str, int, float, bool, type(None)))
            }
            record["show_metadata"].pop("url", None)
            record["torrent"] = {
                key: value
                for key, value in item.torrent.items()
                if key in {"infohash", "file_index", "file_name", "file_path", "file_size", "tracker_urls"}
            }
            records.append(record)
        payload = {"version": 1, "items": records}
        encoded = json.dumps(payload, sort_keys=True)
        with self.lock:
            if self.previous == encoded:
                return
            atomic_write_json(self.path, payload, indent=2)
            self.previous = encoded

    def load(self):
        from .download_worker import DownloadItem, DownloadStatus

        try:
            with open(self.path, encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("version") != 1 or not isinstance(payload.get("items"), list):
                raise ValueError("Unsupported download queue format")
        except FileNotFoundError:
            return []
        except (OSError, ValueError, AttributeError):
            _log.warning("Could not read saved download queue", exc_info=True)
            return []
        items = []
        seen = set()
        for record in payload["items"]:
            try:
                if not isinstance(record, dict):
                    continue
                values = {key: record[key] for key in FIELDS if key in record}
                if any(not isinstance(values.get(key), str) for key in ("id", "title", "episode", "destination_path")):
                    continue
                if values["id"] in seen:
                    continue
                item = DownloadItem(**values)
                for key in ("progress", "downloaded_bytes", "total_bytes"):
                    if not isinstance(getattr(item, key), (int, float)) or getattr(item, key) < 0:
                        raise ValueError("Invalid download progress")
                item.show_metadata = (
                    record.get("show_metadata", {}) if isinstance(record.get("show_metadata"), dict) else {}
                )
                item.torrent = record.get("torrent", {}) if isinstance(record.get("torrent"), dict) else {}
                status = DownloadStatus(record["status"])
                if status in {DownloadStatus.QUEUED, DownloadStatus.RESOLVING, DownloadStatus.DOWNLOADING}:
                    status = DownloadStatus.PAUSED
                    item.logs.append("Interrupted download restored paused; partial files retained.")
                # Older restores changed successful downloads to FAILED solely
                # because their published file was later moved/deleted. output_files
                # is populated only after a successful publication, not acquisition.
                if (status == DownloadStatus.FAILED and item.output_files
                        and item.progress == 100 and item.total_bytes > 0
                        and item.downloaded_bytes == item.total_bytes
                        and item.completed_at > item.started_at > 0
                        and "completion_state_version" not in record):
                    status = DownloadStatus.COMPLETED
                    item.logs.append("Recovered completion recorded before an older missing-file restore.")
                item.status = status
                item.restored = True
                if status in {DownloadStatus.COMPLETED, DownloadStatus.SKIPPED}:
                    files = item.output_files or [item.destination_path]
                    if not all(isinstance(path, str) and os.path.isfile(path) for path in files):
                        item.error = "Downloaded file is missing."
                        item.logs.append("Completed download file is no longer at its saved path; completion history retained.")
                item.logs.append("Restored saved queue entry. Press p to resume, or r to retry a stopped download.")
                items.append(item)
                seen.add(item.id)
            except (ValueError, TypeError, KeyError):
                _log.warning("Ignoring invalid saved download queue entry")
        return items
