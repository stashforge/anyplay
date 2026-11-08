"""Stream resolution: source ordering, foreground pick, background fill.

Owns the shared stream pool for the current episode and the background
worker that resolves the remaining mirrors after the first one is playing.
Generation counters invalidate workers left over from a previous episode.

Dependencies point downward only:

    core.streams -> media.resolver (resolve_source, generate_source_passes)
    core.streams -> media.sources (source_priority)
    core.streams -> core.storage (get_preferred_mirror)

One upper-layer fact is injected via :func:`configure`:
``episode_data_fn(show_id, ep, ttype, provider_id)`` — the provider episode
catalog lookup (``get_episode_data`` in app_core), which pulls in
provider/UI wiring that must not be imported downward.

``resolve_source`` loads extraction dependencies on demand and remains a
module global so callers can replace the resolution boundary in tests.
"""

import threading
import time

from ..media.sources import (
    calculate_stream_expiry,
    check_stream_health_and_refresh,
    is_stream_valid_fast,
    stream_quality_preference_key,
    source_priority,
)
from ..services.requirements import add_dependency_hints, dependency_failure_hint, dependency_hint_messages
from . import reporting
from .api import ProviderDependencyError
from .storage import get_preferred_mirror
from ..providers import provider_key

YELLOW = "\033[1;33m"
RESET = "\033[0m"

_episode_data_fn = None


def resolve_source(source, silent=False):
    """Load stream extraction only when playback needs a source resolved."""
    from ..media import resolver

    resolver.configure_reporters(reporting.info, reporting.ok, reporting.warn)
    return resolver.resolve_source(source, silent=silent)


def is_fixed_resolution(src: dict) -> bool:
    res = str(src.get("resolution") or "").strip().lower()
    name = str(src.get("sourceName") or "").strip().lower()
    if res and res not in ("auto", "adaptive", "?"):
        return True
    import re
    if re.search(r'\b(1080p?|720p?|480p?|360p?|\d+x\d+)\b', name):
        return True
    return False


def torrent_size_sort_value(item: dict) -> int:
    """Return a stable ascending sort value for torrent mirrors."""
    if not isinstance(item, dict):
        return 0
    torrent = item.get("torrent") if isinstance(item.get("torrent"), dict) else {}
    if str(item.get("type") or "").strip().lower() != "torrent" and not torrent:
        return 0
    try:
        size = int(torrent.get("size_bytes") or item.get("size_bytes") or 0)
    except (TypeError, ValueError):
        size = 0
    return size if size > 0 else 10**30


def torrent_fallback_sort_value(item: dict) -> int:
    """Keep torrent mirrors after regular provider mirrors."""
    if not isinstance(item, dict):
        return 0
    torrent = item.get("torrent") if isinstance(item.get("torrent"), dict) else {}
    return 1 if str(item.get("type") or "").strip().lower() == "torrent" or torrent else 0


def expand_direct_sources(sources: list) -> list:
    """
    If a source provides a direct auto/adaptive stream without parsed variants,
    allow a background yt-dlp worker to extract individual qualities (720p, 1080p).
    Never duplicate fixed-resolution streams (e.g. 1080p, 720p, 480p) or sources
    that already have individual variants provided.
    """
    has_variants = set()
    for src in sources:
        sname = str(src.get("sourceName") or "")
        base_name = sname.split(" (")[0].strip()
        if is_fixed_resolution(src):
            has_variants.add(base_name)

    expanded = []
    for src in sources:
        expanded.append(src)
        url = src.get("link") or src.get("streamUrl")
        sname = str(src.get("sourceName") or "")
        base_name = sname.split(" (")[0].strip()

        if src.get("allow_ytdlp_expansion") is False or is_fixed_resolution(src) or base_name in has_variants:
            continue

        if url and ("Yt-Dlp" not in sname and "yt-dlp" not in sname.lower()):
            ytdl_src = src.copy()
            ytdl_src.pop("link", None)
            ytdl_src.pop("streamUrl", None)
            ytdl_src["sourceUrl"] = url
            ytdl_src["sourceName"] = f"{base_name} Yt-Dlp"
            expanded.append(ytdl_src)
    return expanded


def configure(*, episode_data_fn=None):
    """Inject the provider episode-catalog lookup (see module docstring)."""
    global _episode_data_fn
    if episode_data_fn is not None:
        _episode_data_fn = episode_data_fn


# Keyed stream cache: (show_id, ep_id, ttype, provider_id) -> dict
_MAX_CACHE_ENTRIES = 50
_streams_cache: dict[tuple, dict] = {}
_active_stream_key: tuple | None = None

# all_streams is kept for backwards compatibility and references the active episode's streams.
all_streams: list = []
_streams_lock = threading.RLock()  # reentrant lock guards all_streams and _streams_cache
_streams_generation = 0  # invalidates workers started for an older episode
_bg_thread = None  # type: Optional[threading.Thread]
_bg_lock = threading.RLock()  # reentrant lock guards _bg_thread and _bg_stats
_bg_generation = 0
_bg_stats = {"resolved": 0, "failed": 0, "total": 0, "current": ""}
_foreground_batches = {}
_QUALITY_GRACE_SECONDS = 10.0

# Periodic revalidation
_revalidation_thread = None  # type: Optional[threading.Thread]
_revalidation_lock = threading.RLock()
_revalidation_interval = 300  # 5 minutes
_revalidation_stop_event = threading.Event()


def _start_revalidation_timer():
    """Start the periodic stream revalidation timer."""
    global _revalidation_thread
    with _revalidation_lock:
        if _revalidation_thread is not None and _revalidation_thread.is_alive():
            return
        _revalidation_stop_event.clear()
        _revalidation_thread = threading.Thread(target=_revalidation_worker, daemon=True)
        _revalidation_thread.start()


def _stop_revalidation_timer():
    """Stop the periodic stream revalidation timer."""
    _revalidation_stop_event.set()
    global _revalidation_thread
    with _revalidation_lock:
        if _revalidation_thread is not None:
            _revalidation_thread.join(timeout=2)
            _revalidation_thread = None


def _revalidation_worker():
    """Background worker that periodically revalidates stale streams for active episode."""
    while not _revalidation_stop_event.wait(_revalidation_interval):
        try:
            with _streams_lock:
                if _active_stream_key is None:
                    continue
                entry = _streams_cache.get(_active_stream_key)
                if not entry or not entry.get("streams"):
                    continue
                now = time.time()
                stale_streams = []
                for s in entry["streams"]:
                    # Check if stream is past soft TTL (needs revalidation)
                    validated_at = float(s.get("validated_at") or 0)
                    if validated_at == 0:
                        continue
                    sname = str(s.get("source_name") or s.get("sourceName") or "")
                    surl = str(s.get("link") or s.get("streamUrl") or s.get("sourceUrl") or "")
                    from ..media.sources import get_stream_soft_ttl
                    soft_ttl = get_stream_soft_ttl(sname, surl)
                    if (now - validated_at) >= soft_ttl:
                        stale_streams.append(s)

                if not stale_streams:
                    continue

                # Copy for processing outside lock
                streams_to_check = list(stale_streams)

            # Revalidate outside lock to avoid blocking UI
            for stream in streams_to_check:
                if _revalidation_stop_event.is_set():
                    break
                check_stream_health_and_refresh(stream, time.time())

        except Exception:
            pass  # Silent fail, timer continues


def make_stream_key(show_id=None, ep=None, ttype="sub", provider_id=None, quality="best") -> tuple:
    return (
        str(show_id or ""),
        str(ep or ""),
        str(ttype or "sub").lower(),
        str(provider_id or "").lower(),
        str(quality or "best").lower(),
    )


def _normalize_key(key) -> tuple | None:
    if key is None:
        return None
    if isinstance(key, tuple):
        # Handle old 4-tuple and new 5-tuple format
        if len(key) == 4:
            return make_stream_key(key[0], key[1], key[2], key[3], "best")
        return make_stream_key(*key)
    return make_stream_key(key)


def background_resolution_pending(key) -> bool:
    """Whether this episode still owns an active background extraction."""
    with _streams_lock:
        matches = (_active_stream_key == _normalize_key(key)
                   and _bg_generation == _streams_generation)
    with _bg_lock:
        return matches and bool(_bg_thread and _bg_thread.is_alive())


def _compute_ep_data_version(ep_data: dict) -> str:
    """Compute a hash of the source URLs to detect provider mirror list changes."""
    if not ep_data or not isinstance(ep_data, dict):
        return ""
    source_urls = ep_data.get("episode", {}).get("sourceUrls", [])
    if not source_urls:
        return ""
    # Create a stable hash from source names and URLs
    import hashlib
    content = "".join(
        "|".join(
            (
                str(s.get("sourceName", "")),
                str(s.get("sourceUrl", "")),
                str(s.get("link", "")),
                str(s.get("streamUrl", "")),
                str((s.get("torrent") or {}).get("infohash", "")),
            )
        )
        for s in source_urls
    )
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _clear_streams(key=None, clear_cache=False):
    global _streams_generation, _active_stream_key
    with _streams_lock:
        _streams_generation += 1
        retired = [batch_key for batch_key in _foreground_batches if key is None or batch_key[0] == _normalize_key(key)]
        for batch_key in retired:
            _foreground_batches.pop(batch_key).close()
        all_streams.clear()
        if key is not None:
            k = _normalize_key(key)
            _streams_cache.pop(k, None)
        elif clear_cache:
            _active_stream_key = None
            _streams_cache.clear()
            # Stop revalidation timer when all streams cleared
            _stop_revalidation_timer()
        else:
            _active_stream_key = None
        return _streams_generation


def _extend_streams(streams, key=None):
    now = time.time()
    for s in streams:
        if isinstance(s, dict):
            s.setdefault("resolved_at", now)
            if "expires_at" not in s:
                s["expires_at"] = calculate_stream_expiry(s, s["resolved_at"])
    with _streams_lock:
        all_streams.extend(streams)
        if key is not None:
            k = _normalize_key(key)
            if k not in _streams_cache:
                # LRU eviction: remove oldest entries if cache is full
                if len(_streams_cache) >= _MAX_CACHE_ENTRIES:
                    oldest_keys = list(_streams_cache.keys())[:len(_streams_cache) - _MAX_CACHE_ENTRIES + 1]
                    for old_k in oldest_keys:
                        _streams_cache.pop(old_k, None)
                _streams_cache[k] = {"streams": [], "resolved_at": now, "ep_data": None}
            _streams_cache[k]["streams"].extend(streams)


def _stream_snapshot(show_id=None, ep=None, ttype="sub", provider_id=None, quality="best", ep_data=None, *, cancel_check=None, revalidate=True) -> list:
    from copy import deepcopy

    if cancel_check and cancel_check():
        return []
    now = time.time()
    with _streams_lock:
        k = make_stream_key(show_id, ep, ttype, provider_id, quality) if show_id is not None else _active_stream_key
        entry = _streams_cache.get(k) if k is not None else None
        if show_id is not None and entry is None:
            return []
        version = entry.get("ep_data_version", "") if entry is not None else ""
        if entry is not None and ep_data is not None:
            requested_version = _compute_ep_data_version(ep_data)
            if requested_version and version and requested_version != version:
                return []
        originals = tuple(entry.get("streams", []) if entry is not None else all_streams)
        before = deepcopy(originals)

    # Health checks may perform network I/O; never keep the cache locked here.
    valid = []
    for index, candidate in enumerate(deepcopy(before)):
        if cancel_check and cancel_check():
            return []
        validate = check_stream_health_and_refresh if revalidate else is_stream_valid_fast
        if validate(candidate, now):
            valid.append((index, candidate))

    with _streams_lock:
        if cancel_check and cancel_check():
            return []
        if entry is not None:
            if _streams_cache.get(k) is not entry or entry.get("ep_data_version", "") != version:
                return []
            current = entry.get("streams", [])
        else:
            current = all_streams
        if len(current) != len(originals) or any(a is not b for a, b in zip(current, originals)) or originals != before:
            return []
        accepted = []
        for index, candidate in valid:
            original = originals[index]
            original.update(candidate)
            accepted.append(original)
        if entry is not None:
            entry["streams"] = accepted
        else:
            all_streams[:] = accepted
        return list(accepted)


class StreamRevalidation:
    """One view's cancellable stale-link check; publication uses snapshot guards."""

    def __init__(self, key, episode_data):
        from copy import deepcopy
        self._cancelled = threading.Event()
        self.thread = threading.Thread(
            target=self._run, args=(tuple(key), deepcopy(episode_data)),
            name='anyplay-mirror-availability', daemon=True,
        )
        self.thread.start()

    def _run(self, key, episode_data):
        from .reporting import status_sink_scope
        try:
            with status_sink_scope(lambda *_: True):
                _stream_snapshot(*key, episode_data, cancel_check=self._cancelled.is_set)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning('Mirror availability check failed (%s)', type(exc).__name__)

    @property
    def pending(self):
        return self.thread.is_alive()

    def close(self):
        self._cancelled.set()


def _stream_count(show_id=None, ep=None, ttype="sub", provider_id=None, quality="best") -> int:
    return len(_stream_snapshot(show_id, ep, ttype, provider_id, quality))


def is_stream_cache_ready(show_id=None, ep=None, ttype="sub", provider_id=None, quality="best") -> bool:
    """Return True if streams for this key have already been resolved (READY or FAILED)."""
    with _streams_lock:
        k = make_stream_key(show_id, ep, ttype, provider_id, quality)
        entry = _streams_cache.get(k)
        if not entry:
            return False
        return entry.get("state") in ("READY", "FAILED")


def _prune_dead_stream(key, stream_link: str):
    """Remove a dead/failing stream from cache."""
    if not stream_link:
        return
    with _streams_lock:
        k = _normalize_key(key)
        entry = _streams_cache.get(k)
        if entry and "streams" in entry:
            entry["streams"] = [s for s in entry["streams"] if (s.get("link") != stream_link and s.get("streamUrl") != stream_link)]


def _get_cached_ep_data(key=None, show_id=None, ep=None, ttype="sub", provider_id=None, quality="best") -> dict | None:
    with _streams_lock:
        if key is not None:
            k = _normalize_key(key)
        elif show_id is not None:
            k = make_stream_key(show_id, ep, ttype, provider_id, quality)
        elif _active_stream_key is not None:
            k = _active_stream_key
        else:
            return None
        entry = _streams_cache.get(k)
        return entry.get("ep_data") if entry else None


def _get_cached_ep_data_with_version(key=None, show_id=None, ep=None, ttype="sub", provider_id=None, quality="best") -> tuple[dict | None, str]:
    """Get cached ep_data and its version. Returns (ep_data, version)."""
    with _streams_lock:
        if key is not None:
            k = _normalize_key(key)
        elif show_id is not None:
            k = make_stream_key(show_id, ep, ttype, provider_id, quality)
        elif _active_stream_key is not None:
            k = _active_stream_key
        else:
            return None, ""
        entry = _streams_cache.get(k)
        if not entry:
            return None, ""
        return entry.get("ep_data"), entry.get("ep_data_version", "")


def _set_cached_ep_data(ep_data: dict, key=None, show_id=None, ep=None, ttype="sub", provider_id=None, quality="best"):
    if not ep_data:
        return
    now = time.time()
    ep_data_version = _compute_ep_data_version(ep_data)
    with _streams_lock:
        if key is not None:
            k = _normalize_key(key)
        elif show_id is not None:
            k = make_stream_key(show_id, ep, ttype, provider_id, quality)
        else:
            return
        source_urls = ep_data.get("episode", {}).get("sourceUrls", [])
        unresolved_state = "STALE" if source_urls else "FAILED"
        if k not in _streams_cache:
            _streams_cache[k] = {
                "state": unresolved_state,
                "streams": [],
                "resolved_at": now,
                "ep_data": ep_data,
                "ep_data_version": ep_data_version,
            }
        else:
            old_version = _streams_cache[k].get("ep_data_version", "")
            _streams_cache[k]["ep_data"] = ep_data
            _streams_cache[k]["ep_data_version"] = ep_data_version
            if ep_data_version != old_version and (ep_data_version or old_version):
                _streams_cache[k].pop("dependency_error", None)
                _streams_cache[k].pop("dependency_hints", None)
                _streams_cache[k]["streams"] = []
                _streams_cache[k]["state"] = unresolved_state
            elif source_urls and not _streams_cache[k].get("streams"):
                _streams_cache[k]["state"] = "STALE"


def _publish_stream(stream, generation, key=None, *, expected_version=None):
    """Publish a resolved stream only while its episode generation is current."""
    now = time.time()
    stream.setdefault("created_at", now)
    stream.setdefault("validated_at", now)
    if "expires_at" not in stream:
        stream["expires_at"] = calculate_stream_expiry(stream, stream["created_at"])
    sname = stream.get("source_name")
    res = stream.get("resolution")

    with _streams_lock:
        if generation != _streams_generation:
            return False
        if expected_version is not None:
            entry = _streams_cache.get(key)
            if entry is None or entry.get("ep_data_version") != expected_version:
                return False

        # Fresh Stream Replacement / Upsert in all_streams
        found_idx = next(
            (i for i, existing in enumerate(all_streams)
             if existing.get("source_name") == sname and existing.get("resolution") == res),
            None
        )
        if found_idx is not None:
            all_streams[found_idx] = stream
        else:
            all_streams.append(stream)

        if key is not None:
            k = key if isinstance(key, tuple) else make_stream_key(key)
            if k not in _streams_cache:
                _streams_cache[k] = {"state": "READY", "streams": [], "resolved_at": now, "ep_data": None}
            entry = _streams_cache[k]
            cache_streams = entry.get("streams", [])
            c_found_idx = next(
                (i for i, existing in enumerate(cache_streams)
                 if existing.get("source_name") == sname and existing.get("resolution") == res),
                None
            )
            if c_found_idx is not None:
                cache_streams[c_found_idx] = stream
            else:
                cache_streams.append(stream)
            entry["streams"] = cache_streams
            entry["state"] = "READY"
        return True


def _generation_is_current(generation):
    with _streams_lock:
        return generation == _streams_generation


def _update_bg_stats(generation, *, current=None, resolved=0, failed=0, status_msg=None):
    with _bg_lock:
        if generation != _bg_generation:
            return False
        if current is not None:
            _bg_stats["current"] = current
        if status_msg is not None:
            _bg_stats["status_msg"] = status_msg
        _bg_stats["resolved"] += resolved
        _bg_stats["failed"] += failed
        return True


def resolution_feedback(key):
    """Read source feedback on the owner; never raise from a worker snapshot."""
    with _streams_lock:
        entry = _streams_cache.get(key, {})
        return dependency_hint_messages(entry), entry.get("dependency_error")


def _record_dependency_feedback(key, generation, error=None, hints=()):
    with _streams_lock:
        entry = _streams_cache.get(key)
        if (generation != _streams_generation or entry is None
                or entry.get("resolution_generation") != generation
                or entry.get("resolution_version") != entry.get("ep_data_version")):
            return False
        if error is not None:
            entry.setdefault("dependency_error", error)
            hints = (*hints, dependency_failure_hint(error))
        entry["dependency_hints"] = list(dependency_hint_messages(add_dependency_hints(entry, hints)))
        return True


def start_bg_resolve(
    ep_data,
    exclude_names: set,
    show_id=None,
    ep=None,
    ttype="sub",
    provider_id=None,
    quality="best"
):
    """
    Start resolving all remaining sources in background for a specific show/ep.
    exclude_names: source names already resolved (skip them to avoid duplicates).
    """
    global _streams_generation, _bg_thread, _bg_generation, _bg_stats, _active_stream_key
    if ep_data is not None and not isinstance(ep_data, dict):
        return
    if isinstance(ep_data, dict) and ep_data.get("is_local"):
        return
    if ep_data is None and not (show_id and _episode_data_fn):
        return
    sources = ep_data.get("episode", {}).get("sourceUrls", []) if isinstance(ep_data, dict) else []
    sources = expand_direct_sources(sources)

    # Get preferred mirror for this show to prioritize in background
    pref_mirror = None
    if show_id:
        try:
            from .storage import get_preferred_mirror
            pref_mirror = get_preferred_mirror(show_id, provider_key(provider_id))
        except Exception:
            pass

    def _bg_sort_key(s):
        sname = str(s.get("sourceName") or "").lower()
        res = str(s.get("resolution") or sname)
        is_sub = "sub" in sname
        is_dub = ("dub" in sname) or ((" eng" in sname or "english" in sname) and not is_sub)
        audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
        is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
        is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
        is_donghua = str(provider_id or "").lower() in {"animexin", "lucifer", "animekhor"}
        if is_donghua:
            sub_rank = 0 if is_hard else (2 if is_soft else 1)
        else:
            sub_rank = 0 if is_soft else (2 if is_hard else 1)

        # Preferred mirror gets highest priority
        pref_boost = 0
        if pref_mirror:
            src_name = s.get("sourceName") or s.get("source_name") or ""
            pref_name = pref_mirror.get("source_name", "")
            pref_res = pref_mirror.get("resolution", "")
            if src_name and pref_name and src_name.startswith(pref_name) and (not pref_res or res == pref_res):
                pref_boost = -100  # Very high priority

        prio = source_priority(s)
        q_key = stream_quality_preference_key(s, quality)
        return (
            pref_boost,
            torrent_fallback_sort_value(s),
            sub_rank,
            audio_penalty,
            q_key,
            prio,
            torrent_size_sort_value(s),
        )

    sources = sorted(sources, key=_bg_sort_key)
    now = time.time()

    ep_data_version = _compute_ep_data_version(ep_data)
    stream_key = make_stream_key(show_id, ep, ttype, provider_id, quality) if show_id is not None else None

    def _is_excluded_source(src):
        sname = str(src.get("sourceName") or "")
        if not sname:
            return True
        clean_sname = sname.title()
        return (
            sname in exclude_names
            or clean_sname in exclude_names
            or any(sname.lower() in str(ex).lower() for ex in exclude_names)
        )

    with _streams_lock:
        _streams_generation += 1
        generation = _streams_generation
        _bg_generation = generation
        _active_stream_key = stream_key
        _start_revalidation_timer()
        all_streams.clear()
        if stream_key is not None:
            if stream_key not in _streams_cache:
                _streams_cache[stream_key] = {"state": "RESOLVING", "streams": [], "resolved_at": now, "ep_data": ep_data, "ep_data_version": ep_data_version}
            else:
                curr_state = _streams_cache[stream_key].get("state", "READY")
                _streams_cache[stream_key]["state"] = "REFRESHING" if curr_state in ("READY", "STALE") else "RESOLVING"
                if ep_data:
                    _streams_cache[stream_key]["ep_data"] = ep_data
                    _streams_cache[stream_key]["ep_data_version"] = ep_data_version
            entry = _streams_cache[stream_key]
            entry.pop("dependency_error", None)
            entry.pop("dependency_hints", None)
            entry["resolution_generation"] = generation
            entry["resolution_version"] = entry.get("ep_data_version")

    with _bg_lock:
        _bg_stats = {
            "resolved": len(exclude_names),
            "failed": 0,
            "total": len(sources),
            "current": "",
            "status_msg": "checking sources..." if sources else "Loading sources…",
        }

    from .source_batch import SourceBatch
    with _streams_lock:
        batch = _foreground_batches.pop((stream_key, ep_data_version), None)
    if batch is None:
        resolver = resolve_source
        batch = SourceBatch(lambda source: resolver(source, silent=True))

    def worker_body():
        nonlocal sources
        resolution_version = ep_data_version
        if stream_key is not None:
            existing_valid = _stream_snapshot(
                show_id, ep, ttype, provider_id, quality,
                cancel_check=lambda: not _generation_is_current(generation),
            )
        else:
            existing_valid = []
        with _streams_lock:
            if generation != _streams_generation:
                return
            if stream_key is not None and _streams_cache.get(stream_key, {}).get("ep_data_version") != resolution_version:
                return
            # Publish only accepted health checks; never expose unchecked cache.
            all_streams.extend(existing_valid)
            seen_keys = {(s.get("source_name"), s.get("resolution")) for s in all_streams}
        if not sources and _episode_data_fn and show_id:
            try:
                fetched_ep_data = _episode_data_fn(show_id, ep, ttype, provider_id=provider_id)
                if not _generation_is_current(generation):
                    return
                if fetched_ep_data:
                    _set_cached_ep_data(fetched_ep_data, key=stream_key)
                    resolution_version = _compute_ep_data_version(fetched_ep_data)
                    with _streams_lock:
                        if generation != _streams_generation:
                            return
                        if stream_key in _streams_cache:
                            entry = _streams_cache[stream_key]
                            entry["resolution_version"] = entry.get("ep_data_version")
                    raw_sources = fetched_ep_data.get("episode", {}).get("sourceUrls", [])
                    sources = expand_direct_sources(raw_sources)
                    sources = sorted(sources, key=_bg_sort_key)
                    with _bg_lock:
                        if _bg_generation == generation:
                            _bg_stats["total"] = len(sources)
            except ProviderDependencyError as exc:
                _record_dependency_feedback(stream_key, generation, exc)
            except Exception:
                pass

        from .source_batch import source_identity

        def completed_sources():
            pending = list({source_identity(src): src for src in sources
                            if not _is_excluded_source(src)}.values())
            for pass_index in range(3):
                failed = []
                if pass_index:
                    for src in pending:
                        batch.futures.pop(source_identity(src), None)
                seen = set()
                while len(seen) < len(pending):
                    if not _generation_is_current(generation):
                        return
                    with _streams_lock:
                        if stream_key is not None and _streams_cache.get(stream_key, {}).get("ep_data_version") != resolution_version:
                            return
                    for src, future in batch.poll(pending, seen):
                        seen.add(source_identity(src))
                        yield src, failed, pass_index == 2, future
                pending = failed
                if not pending:
                    return

        for src, failed_queue, is_final_pass, future in completed_sources():
            if not _generation_is_current(generation):
                return
            with _streams_lock:
                if stream_key is not None and _streams_cache.get(stream_key, {}).get("ep_data_version") != resolution_version:
                    return
            sname = src.get("sourceName", "")
            if _is_excluded_source(src):
                continue

            display_name = sname
            if not _update_bg_stats(generation, current=display_name, status_msg=f"checking {display_name.lower()}..."):
                return
            try:
                found = False
                resolved = future.result()
                for stream in resolved or []:
                    found = True
                    if not _generation_is_current(generation):
                        return
                    _record_dependency_feedback(stream_key, generation, hints=dependency_hint_messages(stream))
                    s_key = (stream.get("source_name"), stream.get("resolution"))
                    if s_key not in seen_keys:
                        if not _publish_stream(stream, generation, key=stream_key, expected_version=resolution_version if stream_key is not None else None):
                            return
                        seen_keys.add(s_key)
                        found = True

                if not found:
                    if not is_final_pass:
                        failed_queue.append(src)

                inc_failed = 1 if not found and is_final_pass else 0
                inc_resolved = 1 if found else 0

                status_msg = f"checking {display_name.lower()}..." if found else f"{display_name} unavailable"
                if inc_resolved or inc_failed:
                    if not _update_bg_stats(
                        generation,
                        resolved=inc_resolved,
                        failed=inc_failed,
                        status_msg=status_msg,
                    ):
                        return
            except ProviderDependencyError as exc:
                _record_dependency_feedback(stream_key, generation, exc)
                if not _update_bg_stats(generation, failed=1, status_msg=f"{display_name} unavailable"):
                    return
            except Exception:
                if not is_final_pass:
                    failed_queue.append(src)
                else:
                    if not _update_bg_stats(generation, failed=1, status_msg=f"{display_name} unavailable"):
                        return

        with _streams_lock:
            if generation == _streams_generation and stream_key in _streams_cache:
                entry = _streams_cache[stream_key]
                if entry.get("ep_data_version") != resolution_version:
                    return
                if entry.get("streams"):
                    entry["state"] = "READY"
                else:
                    entry["state"] = "FAILED"
        _update_bg_stats(generation, current="", status_msg="")

    def worker():
        try:
            worker_body()
        finally:
            if batch:
                batch.close()

    with _bg_lock:
        _bg_thread = threading.Thread(target=worker, daemon=True)
        _bg_thread.start()


def fetch_episode_stream(show_id, ep_number, ttype="sub", quality="best", provider_id=None, exclude_sources=None, ep_data=None, *, cancel_check=None):
    with _streams_lock:
        foreground_generation = _streams_generation

    def cancelled():
        return bool((cancel_check and cancel_check()) or not _generation_is_current(foreground_generation))

    if cancel_check and cancel_check():
        return None
    if ep_data is None and _episode_data_fn:
        ep_data = _episode_data_fn(show_id, ep_number, ttype, provider_id=provider_id)
    if cancel_check and cancel_check():
        return None
    if not ep_data:
        reporting.warn("No episode metadata returned by provider.")
        return None
    sources = ep_data.get("episode", {}).get("sourceUrls", [])
    sources = expand_direct_sources(sources)
    if not sources:
        reporting.warn("0 streaming mirrors found for this episode.")
        return None

    pref = get_preferred_mirror(show_id, provider_key(provider_id))
    pref_name = pref.get("source_name", "")
    pref_res = pref.get("resolution", "")

    def dynamic_prio(src):
        api_name = src.get("sourceName", "")
        if pref_name.startswith(api_name) and api_name:
            return (-1, 0, 0, (0, 0))
        sname = str(src.get("sourceName") or "").lower()
        is_sub = "sub" in sname
        is_dub = ("dub" in sname) or ((" eng" in sname or "english" in sname) and not is_sub)
        audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
        is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
        is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
        is_donghua = str(provider_id or "").lower() in {"animexin", "lucifer", "animekhor"}
        if is_donghua:
            sub_rank = 0 if is_hard else (2 if is_soft else 1)
        else:
            sub_rank = 0 if is_soft else (2 if is_hard else 1)
        prio = source_priority(src)
        q_key = stream_quality_preference_key(src, quality)
        return (
            torrent_fallback_sort_value(src),
            sub_rank,
            audio_penalty,
            q_key,
            prio,
            torrent_size_sort_value(src),
        )

    exclude_sources = set(exclude_sources or [])

    def _is_excluded(s):
        sname = str(s.get("sourceName") or "")
        base_name = sname.split(" (")[0].strip().lower()
        link = str(s.get("link") or s.get("streamUrl") or "").strip().lower()
        for ex in exclude_sources:
            ex_str = str(ex).strip().lower()
            if not ex_str:
                continue
            if "(" in ex_str and ")" in ex_str:
                if ex_str == sname.lower() or (link and ex_str == link):
                    return True
            else:
                ex_base = ex_str.split(" (")[0].strip()
                if ex_base and ex_base == base_name:
                    return True
                if ex_str in sname.lower() or (link and ex_str == link):
                    return True
        return False

    valid_sources = [s for s in sources if not _is_excluded(s)]
    if not valid_sources:
        reporting.warn("All available mirrors are excluded.")
        return None

    sorted_sources = sorted(valid_sources, key=dynamic_prio)
    total_mirrors = len(sorted_sources)
    mirror_names = [s.get("sourceName", "?") for s in sorted_sources]
    reporting.info(f"Found {total_mirrors} mirror(s): {', '.join(mirror_names[:4])}{'...' if total_mirrors > 4 else ''}")

    from .source_batch import SourceBatch, source_identity

    batch = SourceBatch(resolve_source, cancel_check=cancelled)
    errors = []
    candidates = []
    deadline = None
    handed_off = False
    stream_key = make_stream_key(show_id, ep_number, ttype, provider_id, quality)
    batch_key = (stream_key, _compute_ep_data_version(ep_data))

    def candidate_key(pair):
        source, row = pair
        preferred = bool(pref_name and row.get("source_name") == pref_name and row.get("resolution", "?") == pref_res)
        return (not preferred, torrent_fallback_sort_value(source), stream_quality_preference_key(row, quality), dynamic_prio(source))

    def accept():
        nonlocal handed_off
        if cancelled():
            return None
        source, selected = min(candidates, key=candidate_key)
        hints = [dependency_failure_hint(error) for error in errors]
        found = [add_dependency_hints(row, hints) for _, row in candidates]
        selected = add_dependency_hints(selected, hints)
        with _streams_lock:
            if cancelled():
                return None
            previous = _foreground_batches.pop(batch_key, None)
            if previous:
                previous.close()
            batch.detach_cancel_check()
            _foreground_batches[batch_key] = batch
            while len(_foreground_batches) > 8:
                _foreground_batches.pop(next(iter(_foreground_batches))).close()
            handed_off = True
            reporting.ok(f"Connected: {selected.get('source_name', '?')} ({selected.get('resolution', '?')})")
        return selected, source.get("sourceName", ""), ep_data, found

    try:
        pending = sorted_sources
        for pass_index in range(2):
            failures = []
            if pass_index:
                reporting.warn("Initial pass failed. Retrying failed mirrors (Pass 2/2)...")
                for source in pending:
                    batch.futures.pop(source_identity(source), None)
            # Race comparable sources only: preserve language/subtype and torrent preference.
            tiers = list(dict.fromkeys(dynamic_prio(source)[:3] for source in pending))
            for tier in tiers:
                group = list({source_identity(source): source for source in pending if dynamic_prio(source)[:3] == tier}.values())
                seen = set()
                reporting.info(f"Resolving up to 3 mirrors in parallel ({len(group)} sources)...")
                while len(seen) < len(group):
                    if cancelled():
                        return None
                    ready = batch.poll(group, seen)
                    if cancelled():
                        return None
                    for source, future in ready:
                        seen.add(source_identity(source))
                        name = source.get("sourceName", "?")
                        try:
                            rows = future.result()
                        except ProviderDependencyError as exc:
                            errors.append(exc)
                            continue
                        except Exception:
                            rows = []
                        if rows:
                            candidates.extend((source, row) for row in rows)
                            if deadline is None:
                                deadline = time.monotonic() + _QUALITY_GRACE_SECONDS
                            reporting.ok(f"{name}: found {len(rows)} stream(s)")
                        elif rows is not None:
                            failures.append(source)
                            reporting.warn(f"{name} unavailable; checking other mirrors...")
                    if candidates:
                        _, best = min(candidates, key=candidate_key)
                        key = stream_quality_preference_key(best, quality)
                        preferred = bool(pref_name and best.get("source_name") == pref_name and best.get("resolution", "?") == pref_res)
                        matches = key[0] == 0 and not pref_name
                        if preferred or matches or len(seen) == len(group) or time.monotonic() >= deadline:
                            return accept()
            pending = failures
            if not pending:
                break
        if errors:
            raise errors[0]
        reporting.warn("All mirrors were tested and failed.")
        return None
    finally:
        if not handed_off:
            batch.close()


# ── Cache Stats / Debugging ─────────────────────────────────────────────

def get_cache_stats() -> dict:
    """Return statistics about the stream cache for debugging."""
    with _streams_lock:
        total_entries = len(_streams_cache)
        total_streams = sum(len(entry.get("streams", [])) for entry in _streams_cache.values())
        states = {}
        for entry in _streams_cache.values():
            state = entry.get("state", "unknown")
            states[state] = states.get(state, 0) + 1
        return {
            "total_entries": total_entries,
            "total_streams": total_streams,
            "states": states,
            "active_stream_key": _active_stream_key,
            "max_entries": _MAX_CACHE_ENTRIES,
            "bg_thread_alive": _bg_thread.is_alive() if _bg_thread else False,
            "revalidation_thread_alive": _revalidation_thread.is_alive() if _revalidation_thread else False,
        }


def debug_dump_cache() -> str:
    """Return a formatted string dump of the entire cache for debugging."""
    lines = ["=== Stream Cache Debug Dump ==="]
    with _streams_lock:
        lines.append(f"Active stream key: {_active_stream_key}")
        lines.append(f"Generation: {_streams_generation}")
        lines.append(f"Max entries: {_MAX_CACHE_ENTRIES}")
        lines.append(f"Current entries: {len(_streams_cache)}")
        lines.append(f"BG thread alive: {_bg_thread.is_alive() if _bg_thread else False}")
        lines.append(f"Revalidation thread alive: {_revalidation_thread.is_alive() if _revalidation_thread else False}")
        lines.append("")
        for i, (key, entry) in enumerate(_streams_cache.items()):
            lines.append(f"Entry {i}: {key}")
            lines.append(f"  State: {entry.get('state', 'unknown')}")
            lines.append(f"  Resolved at: {entry.get('resolved_at')}")
            lines.append(f"  EP data version: {entry.get('ep_data_version', 'N/A')}")
            lines.append(f"  Streams: {len(entry.get('streams', []))}")
            for j, stream in enumerate(entry.get("streams", [])):
                sname = stream.get("source_name", "?")
                sres = stream.get("resolution", "?")
                created = stream.get("created_at")
                validated = stream.get("validated_at")
                expires = stream.get("expires_at")
                lines.append(f"    [{j}] {sname} ({sres}) created={created} validated={validated} expires={expires}")
            lines.append("")
    return "\n".join(lines)


def clear_cache() -> None:
    """Clear the entire stream cache (for testing)."""
    global _streams_cache, _active_stream_key, _streams_generation, all_streams
    with _streams_lock:
        _streams_cache.clear()
        _active_stream_key = None
        _streams_generation = 0
        all_streams.clear()
