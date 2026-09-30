"""Pure mutations for playback preferences."""

import time


def preferred_mirror(preferences, show_id):
    return preferences.get(str(show_id), {})


def toggle_preferred_mirror(preferences, show_id, source_name, resolution):
    key = str(show_id)
    current = preferences.get(key, {})
    if (
        current.get("source_name") == source_name
        and current.get("resolution") == resolution
    ):
        current.pop("source_name", None)
        current.pop("resolution", None)
        if current:
            preferences[key] = current
        else:
            preferences.pop(key, None)
    else:
        current["source_name"] = source_name
        current["resolution"] = resolution
        preferences[key] = current
    return preferences


def episode_order(preferences, show_id, default):
    return preferences.get(str(show_id), {}).get("episode_order", default)


def toggle_episode_order(preferences, show_id, default):
    key = str(show_id)
    current = preferences.get(key, {})
    order = (
        "desc"
        if current.get("episode_order", default) == "asc"
        else "asc"
    )
    current["episode_order"] = order
    preferences[key] = current
    return order


def title_sync_preference(preferences, show):
    if not show:
        return None
    show_id = show.get("_id")
    anilist_id = show.get("aniListId")
    if show_id and "sync_enabled" in preferences.get(str(show_id), {}):
        return bool(preferences[str(show_id)]["sync_enabled"])
    key = f"al:{anilist_id}"
    if anilist_id and "sync_enabled" in preferences.get(key, {}):
        return bool(preferences[key]["sync_enabled"])
    return None


def set_title_sync(preferences, show, enabled):
    keys = []
    if show.get("_id"):
        keys.append(str(show["_id"]))
    if show.get("aniListId"):
        keys.append(f"al:{show['aniListId']}")
    for key in keys:
        current = preferences.get(key, {})
        current["sync_enabled"] = bool(enabled)
        preferences[key] = current
    return preferences


def resume_time(preferences, show_id, episode):
    show_resumes = preferences.get(str(show_id), {}).get("resumes", {})
    if not show_resumes:
        return 0
    ep_str = str(episode)
    if ep_str in show_resumes:
        return int(show_resumes[ep_str] or 0)
    from ..domain.episodes import clean_episode_identifier, episode_progress_number
    cleaned = str(clean_episode_identifier(ep_str) or "")
    if cleaned and cleaned in show_resumes:
        return int(show_resumes[cleaned] or 0)
    try:
        num = str(int(float(episode_progress_number(ep_str))))
        if num in show_resumes:
            return int(show_resumes[num] or 0)
    except Exception:
        pass
    for k, v in show_resumes.items():
        if str(clean_episode_identifier(k) or k) == cleaned or k == ep_str:
            return int(v or 0)
    return 0


def save_resume_time(preferences, show_id, episode, position):
    key = str(show_id)
    current = preferences.get(key, {})
    resumes = current.get("resumes", {})
    from ..domain.episodes import clean_episode_identifier
    ep_str = str(episode)
    cleaned = str(clean_episode_identifier(ep_str) or ep_str)
    if position <= 0:
        resumes.pop(ep_str, None)
        resumes.pop(cleaned, None)
    else:
        pos_val = int(position)
        resumes[ep_str] = pos_val
        if cleaned != ep_str:
            resumes[cleaned] = pos_val
    current["resumes"] = resumes
    preferences[key] = current
    return preferences



def pending_completion(preferences, show_id):
    pending = (
        preferences.get(str(show_id), {})
        .get("pending_completion", {})
    )
    return pending if isinstance(pending, dict) else {}


def save_pending_completion(
        preferences,
        show_id,
        episode,
        progress,
        next_episode,
        position,
        duration,
        *,
        timestamp=None):
    key = str(show_id)
    current = preferences.get(key, {})
    current["pending_completion"] = {
        "episode": str(episode),
        "progress": int(progress),
        "next_episode": str(next_episode),
        "position": int(position or 0),
        "duration": int(duration or 0),
        "timestamp": int(time.time() if timestamp is None else timestamp),
    }
    preferences[key] = current
    return preferences


def clear_pending_completion(preferences, show_id):
    key = str(show_id)
    current = preferences.get(key, {})
    if "pending_completion" in current:
        current.pop("pending_completion", None)
        preferences[key] = current
    return preferences


def anilist_match(preferences, anilist_id):
    entry = preferences.get(f"al:{anilist_id}", {})
    return entry.get("al_match", {})


def save_anilist_match(preferences, anilist_id, source_show):
    al_id = str(anilist_id or "")
    if not al_id:
        return preferences

    key = f"al:{al_id}"
    current = preferences.get(key, {})
    current.pop("provider_matches", None)  # Purge any legacy bloated maps

    provider = str(source_show.get("_provider") or source_show.get("provider") or "").lower()
    source_id = str(source_show.get("_id") or source_show.get("id") or "")
    title_name = source_show.get("name") or source_show.get("englishName") or ""
    match_source = source_show.get("_match_source") or "unknown"

    # Clean old reverse provider link if switching to a new provider show
    old_match = current.get("al_match", {})
    old_sid = str(old_match.get("_id") or "")
    if old_sid and old_sid != source_id and old_sid in preferences:
        preferences[old_sid].pop("anilist_match", None)

    # 1:1 Minimal Essential Record for AniList -> Provider
    current["al_match"] = {
        "_id": source_id,
        "_provider": provider,
        "name": title_name,
        "match_source": match_source,
    }
    preferences[key] = current

    # 1:1 Minimal Reverse Record for Provider -> AniList
    if source_id:
        p_current = preferences.get(source_id, {})
        p_current["anilist_match"] = {
            "_id": al_id,
            "name": title_name,
            "match_source": match_source,
        }
        preferences[source_id] = p_current

    return preferences


def source_anilist_match(preferences, show_id):
    return preferences.get(str(show_id), {}).get("anilist_match", {})


def save_source_anilist_match(preferences, source_show, anilist_show):
    show_id = str((source_show or {}).get("_id") or (source_show or {}).get("id") or "")
    anilist_id = str((anilist_show or {}).get("_id") or (anilist_show or {}).get("id") or "")
    if not show_id or not anilist_id:
        return preferences
    return save_anilist_match(preferences, anilist_id, source_show)


def clear_anilist_match(preferences, anilist_id):
    key = f"al:{anilist_id}"
    if key in preferences:
        old_match = preferences[key].get("al_match", {})
        old_sid = str(old_match.get("_id") or "")
        if old_sid and old_sid in preferences:
            preferences[old_sid].pop("anilist_match", None)
        preferences[key].pop("al_match", None)
        if not preferences[key]:
            del preferences[key]
    return preferences


ACTION_FEEDBACK_DURATION = 2.5


def set_action_feedback(show, msg):
    """Set a temporary action feedback message on a show dict."""
    if isinstance(show, dict):
        show["_action_feedback"] = msg
        show["_action_feedback_time"] = time.time()


def get_active_feedback(show, duration=ACTION_FEEDBACK_DURATION):
    """Return the active temporary feedback message if still within duration."""
    if not isinstance(show, dict):
        return ""
    msg = str(show.get("_action_feedback") or "").strip()
    t = float(show.get("_action_feedback_time") or 0)
    if msg and (time.time() - t) < duration:
        return msg
    return ""

