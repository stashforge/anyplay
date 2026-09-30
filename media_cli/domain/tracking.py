"""Pure AniList status and progress transition rules."""

from .metadata import positive_int


def tracking_status_for_play(show):
    status = str(show.get("_anilist_list") or "").upper()
    if status == "COMPLETED":
        return "REPEATING"
    if status not in ("CURRENT", "WATCHING", "REPEATING", "REWATCHING"):
        return "CURRENT"
    return None


def tracking_status_for_progress(show, progress):
    total = positive_int(show.get("episodeCount"))
    try:
        progress = max(0, int(progress))
    except (TypeError, ValueError):
        progress = 0
    media_finished = str(show.get("status") or "").upper() == "FINISHED"
    if media_finished and total and progress >= total:
        return "COMPLETED"
    return tracking_status_for_play(show)


def completed_media_total(show, ttype="sub"):
    if str(show.get("status") or "").upper() != "FINISHED":
        return None
    total = positive_int(show.get("episodeCount"))
    if total:
        return total
    episode_ids = show.get("_episode_ids") or []
    if episode_ids:
        return len(episode_ids)
    available = show.get("availableEpisodes") or {}
    total = positive_int(available.get(ttype))
    if total:
        return total
    totals = [positive_int(value) for value in available.values()]
    return max((value for value in totals if value), default=None)


def apply_tracking_progress_local(show, progress, status=None):
    if status:
        show["_anilist_list"] = status
    show["_anilist_progress"] = progress


def mark_tracking_progress_local(show, progress):
    status = tracking_status_for_play(show)
    apply_tracking_progress_local(show, progress, status)
    return status


def tracking_progress_for_sync(show, progress, status=None):
    try:
        progress = max(0, int(progress))
    except (TypeError, ValueError):
        return 0
    if status == "REPEATING":
        return progress
    try:
        current = max(0, int(show.get("_anilist_progress") or 0))
    except (TypeError, ValueError):
        current = 0
    return max(current, progress)
