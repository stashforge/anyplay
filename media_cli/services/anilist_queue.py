"""AniList background mutation queue and retry worker."""

from __future__ import annotations

import threading
import time as _time

from ..context import FLAGS
from ..core import reporting, storage
from ..state import anilist_queue as anilist_queue_state
from ..state import paths as _paths

debug_warn = reporting.debug_warn
ANILIST_TIMEOUT = 12

_anilist_queue_lock = threading.RLock()
_anilist_queue_cache = None
_anilist_write_lock = threading.Lock()
_anilist_write_condition = threading.Condition(_anilist_write_lock)
_anilist_active_mutations = set()
_anilist_work_items = []
_anilist_worker = None

# Injected functions from core/anilist.py to avoid circular dependencies
_scrobble_anilist_fn = None
_should_update_anilist_progress_fn = None
_anilist_date_updates_fn = None


def configure_queue(*, scrobble_fn=None, should_update_fn=None, date_updates_fn=None):
    global _scrobble_anilist_fn, _should_update_anilist_progress_fn, _anilist_date_updates_fn
    if scrobble_fn is not None:
        _scrobble_anilist_fn = scrobble_fn
    if should_update_fn is not None:
        _should_update_anilist_progress_fn = should_update_fn
    if date_updates_fn is not None:
        _anilist_date_updates_fn = date_updates_fn


def _is_incognito():
    return FLAGS.incognito_mode


def _load_anilist_queue():
    global _anilist_queue_cache
    with _anilist_queue_lock:
        try:
            _anilist_queue_cache = anilist_queue_state.load_records(
                _paths.ANILIST_QUEUE_PATH,
                _anilist_queue_cache,
            )
        except Exception as e:
            debug_warn("Failed to load AniList mutation queue", e)
            storage._preserve_invalid_state_file(_paths.ANILIST_QUEUE_PATH, "AniList mutation queue")
            _anilist_queue_cache = []
        return _anilist_queue_cache


def _save_anilist_queue(records):
    global _anilist_queue_cache
    if _is_incognito():
        return False
    with _anilist_queue_lock:
        storage._atomic_write_json(_paths.ANILIST_QUEUE_PATH, records, indent=2)
        anilist_queue_state.secure_queue_file(_paths.ANILIST_QUEUE_PATH)
        _anilist_queue_cache = records
        return True


def _anilist_mutation_key(record):
    return anilist_queue_state.mutation_key(record)


def _enqueue_anilist_progress(media_id, title, progress, status, show, ttype,
                              pending_completion=None):
    date_updates = _anilist_date_updates_fn(show or {}, progress, status) if _anilist_date_updates_fn else {}
    record = anilist_queue_state.make_progress_record(
        media_id,
        title,
        progress,
        status,
        show,
        ttype,
        pending_completion,
        date_updates=date_updates,
    )
    with _anilist_queue_lock:
        records = anilist_queue_state.replace_progress_record(
            _load_anilist_queue(),
            record,
        )
        _save_anilist_queue(records)
    return record


def _update_queued_mutation(mutation_id, **changes):
    with _anilist_queue_lock:
        records, changed = anilist_queue_state.update_record(
            _load_anilist_queue(),
            mutation_id,
            changes,
        )
        if changed:
            _save_anilist_queue(records)


def _remove_queued_mutation(mutation_id):
    with _anilist_queue_lock:
        _save_anilist_queue(anilist_queue_state.remove_record(
            _load_anilist_queue(),
            mutation_id,
        ))


def _checkpoint_queued_progress(record):
    return None


def _finish_queued_pending_completion(record):
    completion = record.get("pending_completion") or {}
    show_id = completion.get("show_id")
    episode = completion.get("episode")
    if not show_id or episode is None:
        return
    pending = storage.get_pending_completion(show_id)
    if str(pending.get("episode")) != str(episode):
        return
    storage.save_resume_time(show_id, episode, 0)
    storage.clear_pending_completion(show_id)


def _run_anilist_callback(callback, label):
    if not callback:
        return
    try:
        callback()
    except Exception as e:
        debug_warn(f"AniList {label} callback failed", e)


def _run_queued_anilist_progress(record, token, show=None,
                                  on_success=None, on_failure=None):
    from ..domain.tracking import apply_tracking_progress_local
    mutation_id = record["id"]
    success = False
    try:
        _update_queued_mutation(
            mutation_id,
            attempts=int(record.get("attempts") or 0) + 1,
            last_attempt_at=int(_time.time()),
        )
        prog_val = record.get("progress")
        prog_int = int(prog_val) if prog_val is not None else None
        scrobble_fn = _scrobble_anilist_fn
        if scrobble_fn is None:
            from ..core.anilist import scrobble_anilist as scrobble_fn
        success = scrobble_fn(
            token,
            record.get("title") or "",
            prog_int,
            media_id=record.get("media_id"),
            status=record.get("status"),
            show=show,
            started_at=record.get("started_at"),
            completed_at=record.get("completed_at"),
        )
        if success:
            if show is not None:
                if prog_int is not None:
                    apply_tracking_progress_local(
                        show, prog_int, record.get("status")
                    )
                else:
                    if record.get("status"):
                        show["_anilist_list"] = record.get("status")
                show["_progress_authority"] = "AL"
            else:
                _checkpoint_queued_progress(record)
            _finish_queued_pending_completion(record)
            _remove_queued_mutation(mutation_id)
            _run_anilist_callback(on_success, "success")
        else:
            _update_queued_mutation(mutation_id, last_error="AniList update failed")
            _run_anilist_callback(on_failure, "failure")
    except Exception as e:
        debug_warn("Queued AniList update failed", e)
        _update_queued_mutation(mutation_id, last_error=str(e))
        _run_anilist_callback(on_failure, "failure")
    finally:
        with _anilist_write_condition:
            _anilist_active_mutations.discard(mutation_id)
            _anilist_write_condition.notify_all()
    return success


def _anilist_write_worker():
    global _anilist_worker
    while True:
        with _anilist_write_condition:
            if not _anilist_work_items:
                _anilist_worker = None
                _anilist_write_condition.notify_all()
                return
            record, token, show, on_success, on_failure = _anilist_work_items.pop(0)
        _run_queued_anilist_progress(
            record, token, show, on_success, on_failure
        )


def _start_queued_anilist_progress(record, token, show=None,
                                   on_success=None, on_failure=None):
    global _anilist_worker
    mutation_id = record.get("id")
    with _anilist_write_condition:
        if not mutation_id or mutation_id in _anilist_active_mutations:
            return None
        _anilist_active_mutations.add(mutation_id)
        _anilist_work_items.append(
            (record, token, show, on_success, on_failure)
        )
        if _anilist_worker is None:
            _anilist_worker = threading.Thread(
                target=_anilist_write_worker,
                name="anilist-write-worker",
                daemon=True,
            )
            _anilist_worker.start()
        _anilist_write_condition.notify_all()
        return record


def queue_anilist_progress(token, title, progress, media_id, show, ttype,
                           status=None, on_success=None, on_failure=None,
                           pending_completion=None):
    from ..domain.tracking import (
        tracking_progress_for_sync,
        tracking_status_for_progress,
    )
    if _is_incognito():
        return None
    status = tracking_status_for_progress(show, progress) if status is None else status
    progress = tracking_progress_for_sync(show, progress, status)

    existing = show.get("_anilist_progress") if show else None
    should_update_fn = _should_update_anilist_progress_fn
    if should_update_fn is None:
        from ..core.anilist import should_update_anilist_progress as should_update_fn
    prog_to_send = progress if should_update_fn(existing, progress) else None

    try:
        record = _enqueue_anilist_progress(
            media_id, title, prog_to_send, status, show, ttype,
            pending_completion=pending_completion,
        )
    except Exception as e:
        debug_warn("Could not persist AniList update", e)
        if on_failure:
            on_failure()
        return None
    return _start_queued_anilist_progress(
        record, token, show, on_success, on_failure
    )


def retry_queued_anilist_writes(token):
    if _is_incognito():
        return 0
    if not token:
        return 0
    records = list(_load_anilist_queue())
    latest = {}
    for record in records:
        key = _anilist_mutation_key(record)
        previous = latest.get(key)
        if (previous and previous.get("pending_completion")
                and not record.get("pending_completion")):
            record["pending_completion"] = previous["pending_completion"]
        latest[key] = record
    records = sorted(
        latest.values(),
        key=lambda record: (int(record.get("created_at") or 0), record.get("id") or ""),
    )
    if len(records) != len(_load_anilist_queue()):
        _save_anilist_queue(records)
    started = 0
    for record in records:
        if _start_queued_anilist_progress(record, token):
            started += 1
    return started


def flush_anilist_writes(timeout=None):
    if _is_incognito():
        return True
    timeout = ANILIST_TIMEOUT + 2 if timeout is None else max(0, float(timeout))
    deadline = _time.monotonic() + timeout
    with _anilist_write_condition:
        while _anilist_active_mutations or _anilist_work_items:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return False
            _anilist_write_condition.wait(remaining)
        return True
