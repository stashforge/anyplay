"""Pure mutations for playback preferences."""

import time


def _canonical_provider(provider_id):
    from ..provider_manifest import _PROVIDER_ALIASES

    provider = str(provider_id).strip().casefold()
    return _PROVIDER_ALIASES.get(provider, provider)


def _provider_title_key(show_id, provider_id=None):
    if not provider_id:
        return str(show_id)  # Compatibility access; never guess a legacy owner.
    return f"provider:{_canonical_provider(provider_id)}:{show_id}"


def preferred_mirror(preferences, show_id, provider_id=None):
    return preferences.get(_provider_title_key(show_id, provider_id), {})


def toggle_preferred_mirror(preferences, show_id, source_name, resolution, provider_id=None):
    key = _provider_title_key(show_id, provider_id)
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


def episode_order(preferences, show_id, default, provider_id=None):
    return preferences.get(_provider_title_key(show_id, provider_id), {}).get("episode_order", default)


def toggle_episode_order(preferences, show_id, default, provider_id=None):
    key = _provider_title_key(show_id, provider_id)
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
    from ..domain.titles import title_provider_key
    key = _provider_title_key(show_id, title_provider_key(show))
    if show_id and "sync_enabled" in preferences.get(key, {}):
        return bool(preferences[key]["sync_enabled"])
    key = f"al:{anilist_id}"
    if anilist_id and "sync_enabled" in preferences.get(key, {}):
        return bool(preferences[key]["sync_enabled"])
    return None


def set_title_sync(preferences, show, enabled):
    from ..domain.titles import title_provider_key
    keys = []
    if show.get("_id"):
        keys.append(_provider_title_key(show["_id"], title_provider_key(show)))
    if show.get("aniListId"):
        keys.append(f"al:{show['aniListId']}")
    for key in keys:
        current = preferences.get(key, {})
        current["sync_enabled"] = bool(enabled)
        preferences[key] = current
    return preferences


def _custom_skip_keys(show, fallback_show_id=None):
    try:
        from ..media.custom_skips import stable_title_keys
        return stable_title_keys(show, fallback_show_id)
    except Exception:
        # Missing identity support must not re-enable ambiguous bare-ID access.
        return []


def custom_skips(preferences, show, fallback_show_id=None):
    for key in _custom_skip_keys(show, fallback_show_id):
        entries = preferences.get(str(key), {}).get("custom_skips")
        if isinstance(entries, list):
            return entries
    return []


def set_custom_skips(preferences, show, fallback_show_id, entry):
    keys = _custom_skip_keys(show, fallback_show_id)
    if not keys:
        return preferences
    clean_entry = {
        "providers": list(entry.get("providers") or ["all"]),
        "exclude_providers": list(entry.get("exclude_providers") or []),
        "intervals": list(entry.get("intervals") or []),
    }
    for key in keys:
        current = preferences.get(str(key), {})
        current["custom_skips"] = [clean_entry] if clean_entry["intervals"] else []
        preferences[str(key)] = current
    return preferences


def clear_custom_skips(preferences, show, fallback_show_id=None):
    for key in _custom_skip_keys(show, fallback_show_id):
        current = preferences.get(str(key), {})
        current.pop("custom_skips", None)
        if current:
            preferences[str(key)] = current
        else:
            preferences.pop(str(key), None)
    return preferences


def resume_time(preferences, show_id, episode, provider_id=None):
    show_resumes = preferences.get(_provider_title_key(show_id, provider_id), {}).get("resumes", {})
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


def save_resume_time(preferences, show_id, episode, position, provider_id=None):
    key = _provider_title_key(show_id, provider_id)
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



def pending_completion(preferences, show_id, provider_id=None):
    pending = (
        preferences.get(_provider_title_key(show_id, provider_id), {})
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
        provider_id=None,
        *,
        timestamp=None):
    key = _provider_title_key(show_id, provider_id)
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


def clear_pending_completion(preferences, show_id, provider_id=None):
    key = _provider_title_key(show_id, provider_id)
    current = preferences.get(key, {})
    if "pending_completion" in current:
        current.pop("pending_completion", None)
        preferences[key] = current
    return preferences


def anilist_match(preferences, anilist_id):
    entry = preferences.get(f"al:{anilist_id}", {})
    return entry.get("al_match", {})


def _stored_source_key(source):
    provider = source.get("_provider") or source.get("provider")
    sid = str(source.get("_id") or source.get("id") or "")
    return _provider_title_key(sid, provider) if sid and provider else None


def _remove_reverse_match(preferences, source_key, anilist_id):
    current = preferences.get(source_key, {}) if source_key else {}
    if str((current.get("anilist_match") or {}).get("_id") or "") == str(anilist_id):
        current.pop("anilist_match", None)
        if not current:
            preferences.pop(source_key, None)


def save_anilist_match(preferences, anilist_id, source_show):
    from ..domain.titles import title_provider_key

    al_id = str(anilist_id or "")
    source_id = str(source_show.get("_id") or source_show.get("id") or "")
    if not al_id or not source_id:
        return preferences
    provider = _canonical_provider(title_provider_key(source_show))
    source_key = _provider_title_key(source_id, provider)
    key = f"al:{al_id}"
    current = preferences.get(key, {})
    current.pop("provider_matches", None)

    # Remove only a link still pointing back to this exact provider title.
    previous_id = str(source_anilist_match(preferences, source_id, provider).get("_id") or "")
    if previous_id and previous_id != al_id:
        previous_key = f"al:{previous_id}"
        previous = preferences.get(previous_key, {})
        if _stored_source_key(previous.get("al_match") or {}) == source_key:
            previous.pop("al_match", None)
            if not previous:
                preferences.pop(previous_key, None)
    old_key = _stored_source_key(current.get("al_match") or {})
    if old_key and old_key != source_key:
        _remove_reverse_match(preferences, old_key, al_id)

    title_name = source_show.get("name") or source_show.get("englishName") or ""
    match_source = source_show.get("_match_source") or "unknown"
    current["al_match"] = {"_id": source_id, "_provider": provider,
                           "name": title_name, "match_source": match_source}
    preferences[key] = current
    reverse = preferences.setdefault(source_key, {})
    reverse["anilist_match"] = {"_id": al_id, "name": title_name, "match_source": match_source}
    return preferences


def source_anilist_match(preferences, show_id, provider_id=None):
    key = _provider_title_key(show_id, provider_id)
    scoped = preferences.get(key, {}).get("anilist_match", {})
    if scoped or not provider_id:
        return scoped
    legacy = preferences.get(str(show_id), {}).get("anilist_match", {})
    al_id = str(legacy.get("_id") or "")
    forward = preferences.get(f"al:{al_id}", {}).get("al_match") or {}
    # Old reverse records lack an owner. An exact forward pointer can prove it;
    # leave ambiguous records untouched instead of assigning a default provider.
    return legacy if al_id and _stored_source_key(forward) == key else {}


def save_source_anilist_match(preferences, source_show, anilist_show):
    show_id = str((source_show or {}).get("_id") or (source_show or {}).get("id") or "")
    anilist_id = str((anilist_show or {}).get("_id") or (anilist_show or {}).get("id") or "")
    if not show_id or not anilist_id:
        return preferences
    return save_anilist_match(preferences, anilist_id, source_show)


def clear_anilist_match(preferences, anilist_id):
    key = f"al:{anilist_id}"
    if key in preferences:
        old_match = preferences[key].get("al_match") or {}
        _remove_reverse_match(preferences, _stored_source_key(old_match), anilist_id)
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
