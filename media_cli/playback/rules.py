"""Pure playback completion, navigation, and OSD rules."""


def should_clear_query_on_child_left(previous, direct_single):
    return bool(direct_single) and previous in ("SEARCH", "ANILIST_SEARCH")


def minimum_played_for_completion(duration):
    try:
        duration = max(0.0, float(duration))
    except (TypeError, ValueError):
        return 0.0
    return min(300.0, duration * 0.25)


def playback_is_actively_advancing(props, started):
    if not started:
        return False
    try:
        position = float((props or {}).get("playback-time") or 0)
    except (TypeError, ValueError):
        return False
    return bool(
        position > 0
        and not (props or {}).get("pause")
        and not (props or {}).get("paused-for-cache")
    )


def playback_looks_complete(
    result,
    percent,
    time_pos,
    duration,
    played_seconds=0,
    start_time=0,
):
    try:
        duration = max(0.0, float(duration))
        time_pos = max(0.0, float(time_pos))
        percent = max(0.0, float(percent))
        played_seconds = max(0.0, float(played_seconds))
        start_time = max(0.0, float(start_time))
    except (TypeError, ValueError):
        return False
    if duration <= 0:
        if result == "EOF" and time_pos > 300:
            return True
        return False

    remaining = max(0.0, duration - time_pos)
    position_ratio = min(1.0, time_pos / duration)
    near_end = percent >= 90 or position_ratio >= 0.90
    ending_reached = (
        (percent >= 80 or position_ratio >= 0.80)
        and remaining <= 250
    )

    effective_played = played_seconds + start_time
    minimum_played = minimum_played_for_completion(duration)
    if effective_played < minimum_played:
        return False

    if result == "QUIT":
        return (
            near_end
            or ending_reached
            or remaining <= 30.0
            or time_pos >= (duration - 30.0)
        )

    return (
        result in ("EOF", "NEXT")
        or near_end
        or ending_reached
        or remaining <= 30.0
        or time_pos >= (duration - 30.0)
    )


def playback_updates_history(
    result,
    percent,
    time_pos,
    duration,
    played_seconds=0,
    start_time=0,
):
    return playback_looks_complete(
        result, percent, time_pos, duration, played_seconds, start_time=start_time
    )


def marked_watched_osd(ep, synced=False):
    from ..domain.episodes import clean_episode_identifier
    clean_ep = clean_episode_identifier(str(ep)) or str(ep)
    target = " • synced to AniList" if synced else " • local only"
    return f"EP {clean_ep} marked as watched{target}"


def sync_queued_osd(ep):
    from ..domain.episodes import clean_episode_identifier
    clean_ep = clean_episode_identifier(str(ep)) or str(ep)
    return f"EP {clean_ep} marked as watched • AniList sync queued"


def pending_completion_osd(ep, next_ep):
    from ..domain.episodes import clean_episode_identifier
    clean_ep = clean_episode_identifier(str(ep)) or str(ep)
    clean_next = clean_episode_identifier(str(next_ep)) or str(next_ep)
    return f"EP {clean_ep} will be marked after EP {clean_next} plays 2 min"


def episode_transition_osd(action, state):
    direction = "previous" if str(action).upper() == "PREV" else "next"
    if state == "loading":
        return f"Loading {direction} episode..."
    if state == "starting":
        return (
            f"{direction.capitalize()} episode is ready\n"
            f"Starting {direction} episode..."
        )
    if state == "ready":
        return f"{direction.capitalize()} episode is ready"
    if state == "failed":
        return f"{direction.capitalize()} episode is not available"
    return ""


def prefetch_matches_request(prefetched_ep, requested_ep):
    return (
        prefetched_ep is not None
        and requested_ep is not None
        and str(prefetched_ep) == str(requested_ep)
    )
