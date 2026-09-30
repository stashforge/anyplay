"""
Background stream resolver for allmanga-cli.

Manages a thread-safe pool of resolved streams for the current episode and
drives a background worker that resolves remaining mirrors while the user
is already watching.  Extracted from ``app.py`` to keep media concerns
separated from UI/orchestration logic.

Public API
----------
* :func:`clear_streams`        — Reset the stream pool for a new episode.
* :func:`extend_streams`       — Add freshly resolved streams to the pool.
* :func:`publish_stream`       — Add a single stream (dedup + generation guard).
* :func:`stream_snapshot`      — Return a point-in-time copy of the pool.
* :func:`stream_count`         — Return the current pool size.
* :func:`start_bg_resolve`     — Launch the background resolver thread.
* :func:`wait_for_bg`          — Block until the background thread finishes.
"""

from __future__ import annotations

import threading

from . import resolver as stream_resolver
from .sources import source_priority

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

all_streams: list = []
"""Live pool of resolved streams for the current episode.

Access must be serialised through :data:`_streams_lock`.
"""

_streams_lock = threading.Lock()
"""Guards all reads and writes to :data:`all_streams` and
:data:`_streams_generation`."""

_streams_generation: int = 0
"""Monotonically increasing counter.  Incremented each time a new episode
begins so that in-flight workers for the previous episode know to stop."""

_bg_thread: threading.Thread | None = None
"""The currently running background resolver thread, or ``None``."""

_bg_lock = threading.Lock()
"""Guards :data:`_bg_thread` and :data:`_bg_stats`."""

_bg_generation: int = 0
"""Generation tag for the currently running background worker."""

_bg_stats: dict = {"resolved": 0, "failed": 0, "total": 0, "current": ""}
"""Live progress counters for the background resolver.

* ``resolved``  — number of sources that yielded at least one stream.
* ``failed``    — number of sources that yielded no streams.
* ``total``     — total number of sources to check.
* ``current``   — source name currently being resolved (empty when idle).
"""


# ---------------------------------------------------------------------------
# Stream-pool helpers
# ---------------------------------------------------------------------------

def clear_streams() -> int:
    """Reset the stream pool for a new episode.

    Returns the new generation number so callers can associate in-flight
    work with this episode.
    """
    global _streams_generation
    with _streams_lock:
        _streams_generation += 1
        all_streams.clear()
        return _streams_generation


def extend_streams(streams: list) -> None:
    """Append *streams* to the pool without deduplication.

    Used when the first batch of streams is added synchronously before
    playback starts.
    """
    with _streams_lock:
        all_streams.extend(streams)


def stream_snapshot() -> list:
    """Return a thread-safe point-in-time copy of the stream pool."""
    with _streams_lock:
        return list(all_streams)


def stream_count() -> int:
    """Return the current number of streams in the pool."""
    with _streams_lock:
        return len(all_streams)


def publish_stream(stream: dict, generation: int) -> bool:
    """Add *stream* to the pool only if its episode generation is still current.

    Prevents stale background workers (from a previous episode) from
    polluting the current episode's pool.  Also deduplicates by link URL.

    Returns ``True`` if the stream was added, ``False`` otherwise.
    """
    link = stream.get("link")
    with _streams_lock:
        if generation != _streams_generation:
            return False
        if link and any(existing.get("link") == link for existing in all_streams):
            return False
        all_streams.append(stream)
        return True


def _generation_is_current(generation: int) -> bool:
    """Return ``True`` if *generation* matches the current stream generation."""
    with _streams_lock:
        return generation == _streams_generation


def _update_bg_stats(
    generation: int,
    *,
    current: str | None = None,
    resolved: int = 0,
    failed: int = 0,
) -> bool:
    """Update background resolver progress counters.

    Returns ``False`` (and makes no changes) if *generation* is stale,
    signalling the worker to stop.
    """
    with _bg_lock:
        if generation != _bg_generation:
            return False
        if current is not None:
            _bg_stats["current"] = current
        _bg_stats["resolved"] += resolved
        _bg_stats["failed"] += failed
        return True


# ---------------------------------------------------------------------------
# Background resolver
# ---------------------------------------------------------------------------

def start_bg_resolve(ep_data: dict, exclude_names: set) -> None:
    """Start resolving all remaining mirrors in a background thread.

    Call this *after* the first stream is already playing so that the user
    never waits for mirror discovery.

    Parameters
    ----------
    ep_data:
        Episode payload from ``allanime_service.get_episode_data()``.
    exclude_names:
        Source names already resolved synchronously; skipped to avoid
        creating duplicate entries in the stream pool.
    """
    global _bg_thread, _bg_generation, _bg_stats

    p_id = str(ep_data.get("_provider") or ep_data.get("provider") or ep_data.get("episode", {}).get("provider") or "").lower()
    is_donghua = p_id in {"animexin", "lucifer", "animekhor"}

    def _bg_sort_key(s):
        sname = str(s.get("sourceName") or "").lower()
        is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
        is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
        if is_donghua:
            sub_rank = 0 if is_hard else (2 if is_soft else 1)
        else:
            sub_rank = 0 if is_soft else (2 if is_hard else 1)
        return (sub_rank, source_priority(s))

    sources = sorted(
        ep_data.get("episode", {}).get("sourceUrls", []),
        key=_bg_sort_key,
    )
    with _streams_lock:
        _streams_generation_local = _streams_generation + 1
        # Atomically claim the next generation so the worker is tagged
        # to the episode being started, not the one being finished.
    # We bump _streams_generation via clear_streams(); here we just
    # capture what the pool snapshot looks like *before* the bump so
    # the worker can skip links that are already present.
    with _streams_lock:
        generation = _streams_generation
        seen_links = {s.get("link") for s in all_streams}

    with _bg_lock:
        _bg_generation = generation
        _bg_stats = {
            "resolved": len(exclude_names),
            "failed": 0,
            "total": len(sources),
            "current": "",
        }

    def worker() -> None:
        from .resolver import generate_source_passes
        for src, failed_queue, is_retry, is_final_pass in generate_source_passes(sources, max_passes=3):
            if not _generation_is_current(generation):
                return
            sname = src.get("sourceName", "")
            if sname in exclude_names:
                continue
            if not _update_bg_stats(generation, current=sname):
                return
            try:
                found = False
                res = stream_resolver.resolve_source(src, silent=True)
                if res is not None:
                    for stream in res:
                        if not _generation_is_current(generation):
                            return
                        link = stream.get("link")
                        if link not in seen_links and publish_stream(stream, generation):
                            seen_links.add(link)
                            found = True

                if not found:
                    if not is_final_pass:
                        failed_queue.append(src)

                inc_failed = 1 if not found and is_final_pass else 0
                inc_resolved = 1 if found else 0

                if inc_resolved or inc_failed:
                    if not _update_bg_stats(
                        generation,
                        resolved=inc_resolved,
                        failed=inc_failed,
                    ):
                        return
            except Exception:
                if not is_final_pass:
                    failed_queue.append(src)
                else:
                    if not _update_bg_stats(generation, failed=1):
                        return
        _update_bg_stats(generation, current="")

    with _bg_lock:
        _bg_thread = threading.Thread(
            target=worker,
            name="bg-resolver",
            daemon=True,
        )
        _bg_thread.start()


def wait_for_bg() -> None:
    """Block until the background resolver thread finishes.

    Call before showing the Mirrors picker so that the list is complete.
    """
    global _bg_thread
    with _bg_lock:
        t = _bg_thread
    if t and t.is_alive():
        t.join()


def bg_thread_alive() -> bool:
    """Return ``True`` if the background resolver thread is still running."""
    with _bg_lock:
        return bool(_bg_thread and _bg_thread.is_alive())


def bg_stats_snapshot() -> dict:
    """Return a thread-safe copy of the current background resolver stats."""
    with _bg_lock:
        return dict(_bg_stats)
