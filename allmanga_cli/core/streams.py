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

``resolve_source`` is bound at import from media.resolver but kept as a
module global so tests can patch ``streams.resolve_source`` (or via the
``__globals__`` of any moved function) exactly as before.
"""

import threading
import time

from ..media.resolver import resolve_source
from ..media.sources import (
    calculate_stream_expiry,
    check_stream_health_and_refresh,
    quality_preference_key,
    source_priority,
)
from . import reporting
from .storage import get_preferred_mirror

YELLOW = "\033[1;33m"
RESET = "\033[0m"

_episode_data_fn = None


def is_fixed_resolution(src: dict) -> bool:
    res = str(src.get("resolution") or "").strip().lower()
    name = str(src.get("sourceName") or "").strip().lower()
    if res and res not in ("auto", "adaptive", "?"):
        return True
    import re
    if re.search(r'\b(1080p?|720p?|480p?|360p?|\d+x\d+)\b', name):
        return True
    return False


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

        if is_fixed_resolution(src) or base_name in has_variants:
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
        f"{s.get('sourceName', '')}|{s.get('link', '')}|{s.get('streamUrl', '')}"
        for s in source_urls
    )
    return hashlib.md5(content.encode()).hexdigest()[:12]


def _clear_streams(key=None, clear_cache=False):
    global _streams_generation, _active_stream_key
    with _streams_lock:
        _streams_generation += 1
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


def _stream_snapshot(show_id=None, ep=None, ttype="sub", provider_id=None, quality="best", ep_data=None) -> list:
    now = time.time()
    with _streams_lock:
        if show_id is not None:
            k = make_stream_key(show_id, ep, ttype, provider_id, quality)
            entry = _streams_cache.get(k)
            if not entry:
                return []
            # If caller provides ep_data, check version matches cached version
            if ep_data is not None:
                current_version = _compute_ep_data_version(ep_data)
                cached_version = entry.get("ep_data_version", "")
                if current_version and cached_version and current_version != cached_version:
                    return []  # Version mismatch - treat as stale
            valid = [s for s in entry.get("streams", []) if check_stream_health_and_refresh(s, now)]
            entry["streams"] = valid
            return list(valid)
        elif _active_stream_key is not None:
            entry = _streams_cache.get(_active_stream_key)
            if entry:
                if ep_data is not None:
                    current_version = _compute_ep_data_version(ep_data)
                    cached_version = entry.get("ep_data_version", "")
                    if current_version and cached_version and current_version != cached_version:
                        return []
                valid = [s for s in entry.get("streams", []) if check_stream_health_and_refresh(s, now)]
                entry["streams"] = valid
                return list(valid)
        valid_all = [s for s in all_streams if check_stream_health_and_refresh(s, now)]
        all_streams[:] = valid_all
        return list(valid_all)


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
        if k not in _streams_cache:
            _streams_cache[k] = {"state": "READY", "streams": [], "resolved_at": now, "ep_data": ep_data, "ep_data_version": ep_data_version}
        else:
            _streams_cache[k]["ep_data"] = ep_data
            _streams_cache[k]["ep_data_version"] = ep_data_version
def _publish_stream(stream, generation, key=None):
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
            pref_mirror = get_preferred_mirror(show_id)
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
        q_key = quality_preference_key(res, quality)
        return (pref_boost, sub_rank, audio_penalty, q_key, prio)

    sources = sorted(sources, key=_bg_sort_key)
    now = time.time()

    ep_data_version = _compute_ep_data_version(ep_data)
    stream_key = make_stream_key(show_id, ep, ttype, provider_id, quality) if show_id is not None else None

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
            # Populate all_streams with still-valid existing cached streams (full health check)
            existing_valid = [s for s in _streams_cache[stream_key].get("streams", []) if check_stream_health_and_refresh(s, now)]
            _streams_cache[stream_key]["streams"] = existing_valid
            all_streams.extend(existing_valid)
        seen_keys = {(s.get("source_name"), s.get("resolution")) for s in all_streams}

    with _bg_lock:
        _bg_stats = {
            "resolved": len(exclude_names),
            "failed": 0,
            "total": len(sources),
            "current": "",
            "status_msg": "checking sources..." if sources else "Loading sources…",
        }

    def worker():
        nonlocal sources
        if not sources and _episode_data_fn and show_id:
            try:
                fetched_ep_data = _episode_data_fn(show_id, ep, ttype, provider_id=provider_id)
                if not _generation_is_current(generation):
                    return
                if fetched_ep_data:
                    _set_cached_ep_data(fetched_ep_data, key=stream_key)
                    raw_sources = fetched_ep_data.get("episode", {}).get("sourceUrls", [])
                    sources = expand_direct_sources(raw_sources)
                    sources = sorted(sources, key=_bg_sort_key)
                    with _bg_lock:
                        if _bg_generation == generation:
                            _bg_stats["total"] = len(sources)
            except Exception:
                pass

        from ..media.resolver import generate_source_passes
        for src, failed_queue, is_retry, is_final_pass in generate_source_passes(sources, max_passes=3):
            if not _generation_is_current(generation):
                return
            sname = src.get("sourceName", "")
            if not sname:
                continue
            clean_sname = sname.title()
            if sname in exclude_names or clean_sname in exclude_names or any(sname.lower() in ex.lower() for ex in exclude_names):
                continue

            display_name = sname
            if not _update_bg_stats(generation, current=display_name, status_msg=f"checking {display_name.lower()}..."):
                return
            try:
                found = False
                for stream in resolve_source(src, silent=True):
                    if not _generation_is_current(generation):
                        return
                    s_key = (stream.get("source_name"), stream.get("resolution"))
                    if s_key not in seen_keys and _publish_stream(stream, generation, key=stream_key):
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
            except Exception:
                if not is_final_pass:
                    failed_queue.append(src)
                else:
                    if not _update_bg_stats(generation, failed=1, status_msg=f"{display_name} unavailable"):
                        return

        with _streams_lock:
            if generation == _streams_generation and stream_key in _streams_cache:
                entry = _streams_cache[stream_key]
                if entry.get("streams"):
                    entry["state"] = "READY"
                else:
                    entry["state"] = "FAILED"
        _update_bg_stats(generation, current="", status_msg="")

    with _bg_lock:
        _bg_thread = threading.Thread(target=worker, daemon=True)
        _bg_thread.start()


def fetch_episode_stream(show_id, ep_number, ttype="sub", quality="best", provider_id=None, exclude_sources=None, ep_data=None):
    if ep_data is None and _episode_data_fn:
        ep_data = _episode_data_fn(show_id, ep_number, ttype, provider_id=provider_id)
    if not ep_data:
        reporting.warn("No episode metadata returned by provider.")
        return None
    sources = ep_data.get("episode", {}).get("sourceUrls", [])
    sources = expand_direct_sources(sources)
    if not sources:
        reporting.warn("0 streaming mirrors found for this episode.")
        return None

    pref = get_preferred_mirror(show_id)
    pref_name = pref.get("source_name", "")
    pref_res = pref.get("resolution", "")

    def dynamic_prio(src):
        api_name = src.get("sourceName", "")
        if pref_name.startswith(api_name) and api_name:
            return (-1, 0, 0, (0, 0))
        sname = str(src.get("sourceName") or "").lower()
        res = str(src.get("resolution") or sname)
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
        q_key = quality_preference_key(res, quality)
        return (sub_rank, audio_penalty, q_key, prio)

    from ..media.resolver import generate_source_passes
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

    retry_announced = False
    for attempt_idx, (src, failed, is_retry, is_final_pass) in enumerate(generate_source_passes(sorted_sources, max_passes=2), 1):
        if is_retry and not retry_announced:
            reporting.warn("Initial pass failed. Retrying failed mirrors (Pass 2/2)...")
            retry_announced = True
        src_name = src.get("sourceName", "?")
        prefix = "Retry " if is_retry else ""
        mirror_num = min(attempt_idx if not is_retry else len(failed) + 1, total_mirrors)
        reporting.info(f"[{prefix}{mirror_num}/{total_mirrors}] Testing mirror: {src_name} ...")
        streams = resolve_source(src)
        if streams:
            selected_stream = streams[0]
            found_pref = False
            if pref_name:
                for s in streams:
                    if s.get("source_name") == pref_name and s.get("resolution", "?") == pref_res:
                        selected_stream = s
                        found_pref = True
                        break
            if not found_pref:
                sorted_by_pref = sorted(
                    streams,
                    key=lambda s: quality_preference_key(s.get("resolution") or s.get("source_name", ""), quality)
                )
                if sorted_by_pref:
                    selected_stream = sorted_by_pref[0]
            reporting.ok(f"Connected: {selected_stream.get('source_name', src_name)} ({selected_stream.get('resolution', '?')})")
            return selected_stream, src.get("sourceName", ""), ep_data, streams
        elif streams is not None:
            failed.append(src)
            reporting.warn(f"[{prefix}{mirror_num}/{total_mirrors}] {src_name} unavailable. Trying next...")

    reporting.warn("All mirrors were tested and failed.")
    return None


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
