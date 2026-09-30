"""Pure record operations for deferred AniList writes."""

import os
import time
import uuid

from .lists import load_json_list


def mutation_key(record):
    media_id = record.get("media_id")
    if media_id is not None:
        return "id", str(media_id)
    return "title", record.get("title") or ""


def make_progress_record(
        media_id,
        title,
        progress,
        status,
        show,
        translation_type,
        pending_completion=None,
        *,
        date_updates=None,
        record_id=None,
        created_at=None):
    record = {
        "id": record_id or uuid.uuid4().hex,
        "media_id": int(media_id) if media_id is not None else None,
        "title": title,
        "progress": int(progress) if progress is not None else None,
        "status": status,
        "show_id": str((show or {}).get("_id") or ""),
        "ttype": translation_type,
        "created_at": int(
            time.time() if created_at is None else created_at
        ),
        "attempts": 0,
    }
    if pending_completion:
        record["pending_completion"] = pending_completion
    if date_updates:
        record.update({
            key: value
            for key, value in date_updates.items()
            if key in ("started_at", "completed_at") and value
        })
    return record


def replace_progress_record(records, record):
    key = mutation_key(record)
    matching = [
        queued for queued in records if mutation_key(queued) == key
    ]
    if "pending_completion" not in record:
        for queued in reversed(matching):
            if queued.get("pending_completion"):
                record["pending_completion"] = queued["pending_completion"]
                break
    updated = [
        queued for queued in records if mutation_key(queued) != key
    ]
    updated.append(record)
    return updated


def update_record(records, record_id, changes):
    updated = list(records)
    for record in updated:
        if record.get("id") == record_id:
            record.update(changes)
            return updated, True
    return updated, False


def remove_record(records, record_id):
    return [
        record for record in records if record.get("id") != record_id
    ]


def load_records(path, cached=None):
    return load_json_list(path, cached)


def secure_queue_file(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
