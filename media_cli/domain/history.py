"""Watch-history formatting and filtering."""

import time

from .episodes import clean_episode_identifier, episode_id_at, episode_index_for_id
from .metadata import positive_int
from .titles import get_show_display_title


def format_relative_time(timestamp, now=None):
    try:
        if not timestamp:
            return ""
        seconds = int((time.time() if now is None else now) - float(timestamp))
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        if seconds < 86400:
            return f"{seconds // 3600}h ago"
        if seconds < 604800:
            return f"{seconds // 86400}d ago"
        return f"{seconds // 604800}w ago"
    except Exception:
        return ""


def _episode_ids_for_translation(show, translation_type):
    if not show:
        return []
    stored_type = show.get("_episode_ids_ttype")
    if stored_type and stored_type != translation_type:
        return []
    return show.get("_episode_ids") or []

def local_progress(entries, show, translation_type="sub"):
    if not show:
        return None
    show_id = str(show.get("_id") or "")
    from .matching import is_same_show
    target_entry = None
    fallback_entry = None
    for entry in entries:
        entry_show = entry.get("show", {})
        e_id = str(entry_show.get("_id") or entry_show.get("id") or "")
        if show_id and e_id and e_id == show_id:
            same = True
        else:
            same = is_same_show(entry_show, show)
        if not same:
            continue

        if entry.get("translation_type", "sub") == translation_type:
            target_entry = entry
            break
        elif fallback_entry is None:
            fallback_entry = entry
    if target_entry is None:
        target_entry = fallback_entry
    if not target_entry:
        return None
    episode_id = target_entry.get("episode", 0)
    if str(episode_id) in ("0", "0.0"):
        return 0
    episode_ids = (
        _episode_ids_for_translation(show, translation_type)
        or _episode_ids_for_translation(target_entry.get("show", {}), translation_type)
        or []
    )
    labels = (
        show.get("_episode_labels")
        or target_entry.get("show", {}).get("_episode_labels")
        or {}
    )
    if episode_ids:
        if labels and str(episode_id) in labels:
            clean_lbl = clean_episode_identifier(labels[str(episode_id)])
            if clean_lbl:
                return clean_lbl

        clean_eid = clean_episode_identifier(str(episode_id))
        if clean_eid and not str(episode_id).startswith(("http://", "https://", "/")):
            return clean_eid

        index = episode_index_for_id(
            [str(episode) for episode in episode_ids],
            episode_id,
            labels=labels,
        )
        if index is not None:
            cand_id = episode_ids[index]
            if labels and str(cand_id) in labels:
                clean_lbl = clean_episode_identifier(labels[str(cand_id)])
                if clean_lbl:
                    return clean_lbl
            return index + 1
        return None
    try:
        return max(0, int(float(str(episode_id))))
    except (TypeError, ValueError):
        return None
    return None



def playback_episode(
        entry,
        *,
        translation_type=None,
        episode_ids,
        resume_time):
    show = entry.get("show", {})
    show_id = show.get("_id")
    history_episode = entry.get("episode", 1)
    (
        translation_type
        or entry.get("translation_type", "sub")
    )
    if episode_ids:
        if str(history_episode) in ("0", "0.0"):
            return episode_id_at(episode_ids, 0)
        history_index = episode_index_for_id(
            episode_ids,
            history_episode,
            labels=show.get("_episode_labels"),
        )
        if history_index is None:
            import decimal
            try:
                hist_num = decimal.Decimal(str(history_episode))
                for idx, eid in enumerate(episode_ids):
                    try:
                        if decimal.Decimal(str(eid)) == hist_num:
                            history_index = idx
                            break
                    except decimal.InvalidOperation:
                        continue
            except decimal.InvalidOperation:
                pass
        if history_index is None:
            return episode_id_at(episode_ids, 0)
        if show_id and resume_time(show_id, history_episode) > 0:
            return episode_id_at(episode_ids, history_index)
        return episode_id_at(
            episode_ids,
            min(history_index + 1, len(episode_ids) - 1),
        )

    try:
        history_episode = max(1, int(float(str(history_episode))))
    except (TypeError, ValueError):
        history_episode = 1
    if show_id and resume_time(show_id, history_episode) > 0:
        return history_episode
    return history_episode + 1

def history_entry_progress(
        entry,
        *,
        prepare_display_state=None,
        get_local_progress=None):
    show = entry.get("show", {})
    translation_type = entry.get("translation_type", "sub")
    if "_local_progress" not in show:
        show["_local_progress"] = entry.get("episode", 0)
    if "_local_episode_label" not in show:
        show["_local_episode_label"] = entry.get("episode", 0)
    if callable(prepare_display_state):
        prepare_display_state(show, translation_type)
    label = "LOCAL"
    raw_val = entry.get("episode")
    from .episodes import clean_episode_identifier
    progress_val = clean_episode_identifier(raw_val) if raw_val else "0"

    import decimal
    try:
        progress_num = decimal.Decimal(str(progress_val))
    except decimal.InvalidOperation:
        progress_val = "0"
        progress_num = decimal.Decimal(0)

    available = history_available_episode_count(entry)
    full = history_full_episode_count(entry)

    total = full or available

    if total is not None:
        try:
            total_dec = decimal.Decimal(str(total))
            if progress_num > total_dec:
                total = progress_val
        except decimal.InvalidOperation:
            pass

    return label, progress_val, total


def format_history_entry(
        entry,
        *,
        prepare_display_state=None,
        get_local_progress=None,
        now=None):
    show = entry.get("show", {})
    translation_type = entry.get("translation_type", "sub")
    name = get_show_display_title(show, "?")

    suffix = f" ({translation_type})" if translation_type != "sub" else ""
    return f"{name}{suffix}"


def history_provider_is_completed(show):
    al_status = str(show.get("_anilist_status") or "").upper()
    if any(term in al_status for term in ("COMPLETED", "FINISHED", "ENDED")):
        return True
    status = str(show.get("status") or "").upper()
    if any(term in status for term in ("COMPLETED", "FINISHED", "ENDED")):
        return True
    return False

def history_available_episode_count(entry):
    from media_cli.domain.episodes import highest_episode_number
    show = entry.get("show", {})
    ttype = entry.get("translation_type", "sub")

    if show.get("_episode_ids_ttype") == ttype:
        ep_ids = show.get("_episode_ids") or []
        if ep_ids:
            return highest_episode_number(ep_ids)

    avail = (show.get("availableEpisodes") or {}).get(ttype)
    import decimal
    try:
        if avail is not None:
            avail_dec = decimal.Decimal(str(avail))
            if avail_dec >= 0:
                return int(avail_dec) if avail_dec % 1 == 0 else str(avail_dec)
    except decimal.InvalidOperation:
        pass

    next_episode = positive_int(show.get("_next_airing_ep"))
    if next_episode:
        airing_at = show.get("_next_airing_at")
        try:
            airing_passed = bool(airing_at and float(airing_at) <= time.time())
        except (TypeError, ValueError):
            airing_passed = False
        return next_episode if airing_passed else max(0, next_episode - 1)

    return None

def history_full_episode_count(entry):
    show = entry.get("show", {})
    try:
        count = int(show.get("episodeCount") or show.get("_anilist_episode_count") or 0)
        if count > 0:
            return count
    except (TypeError, ValueError):
        pass
    return history_available_episode_count(entry)

def history_entry_category(
        entry,
        *,
        prepare_display_state=None,
        get_local_progress=None):
    show = entry.get("show", {})

    _, local_progress, _ = history_entry_progress(
        entry,
        prepare_display_state=prepare_display_state,
        get_local_progress=get_local_progress,
    )

    import decimal
    try:
        local_num = decimal.Decimal(str(local_progress))
    except decimal.InvalidOperation:
        local_num = decimal.Decimal(0)

    available_count = history_available_episode_count(entry)
    full_count = history_full_episode_count(entry)
    provider_completed = history_provider_is_completed(show)

    target_count = full_count if provider_completed else available_count
    try:
        target_dec = decimal.Decimal(str(target_count)) if target_count is not None else None
    except decimal.InvalidOperation:
        target_dec = None

    if provider_completed:
        if target_dec is not None and local_num >= target_dec:
            entry["was_caught_up"] = False
            entry["has_new_release"] = False
            return "Completed"
        return "Active"

    if target_dec is not None:
        if local_num < target_dec:
            if entry.get("was_caught_up"):
                entry["has_new_release"] = True
            return "Active"
        entry["was_caught_up"] = True
        entry["has_new_release"] = False
        return "Up to date"

    return "Active"

def filter_history_entries(
        history,
        mode,
        *,
        prepare_display_state=None,
        get_local_progress=None):
    mode = str(mode or "Active").capitalize()
    if mode == "Up To Date":
        mode = "Up to date"
    if mode == "All":
        return list(history)

    return [
        entry
        for entry in history
        if history_entry_category(
            entry,
            prepare_display_state=None,
            get_local_progress=None,
        ) == mode
    ]


def playback_ep_from_history_entry(h, ttype=None, *, ensure_episode_ids_fn=None, get_resume_time_fn=None):
    show = h.get("show", {})
    tt = ttype or h.get("translation_type", "sub")
    if ensure_episode_ids_fn is None:
        from ..services.catalog import ensure_episode_ids as ensure_episode_ids_fn
    if get_resume_time_fn is None:
        from ..core.storage import get_resume_time as get_resume_time_fn
    episode_ids = ensure_episode_ids_fn(show, tt)
    return playback_episode(
        h,
        translation_type=tt,
        episode_ids=episode_ids,
        resume_time=get_resume_time_fn,
    )


def refresh_history_entry_provider_catalog(entry):
    import time

    from ..core.reporting import debug_warn
    from ..providers.shared.models import title_provider_key
    from ..services.catalog import (
        _provider_for_title,
        fetch_episode_catalog,
        get_allanime_show,
        update_available_count_from_episode_ids,
    )
    from .metadata import apply_provider_metadata_to_history_show

    show = (entry or {}).get("show", {})
    ttype = entry.get("translation_type", "sub")
    show_id = show.get("_id")

    changed = False
    if not show_id:
        return False

    pkey = title_provider_key(show)
    if pkey == "allanime":
        try:
            allanime_show = get_allanime_show(show_id)
            if allanime_show:
                if apply_provider_metadata_to_history_show(show, allanime_show):
                    changed = True
        except Exception as e:
            debug_warn("Failed to fetch show metadata during AllAnime refresh", e)

        try:
            catalog = fetch_episode_catalog(show_id, ttype)
            if catalog.get("state") == "loaded":
                old_avail = (show.get("availableEpisodes") or {}).get(ttype)
                new_avail = len(catalog.get("ids", []))

                current_ids = show.get("_episode_ids") or []
                if old_avail != new_avail or show.get("_episode_ids_ttype") != ttype or current_ids != catalog["ids"]:
                    if not show.get("availableEpisodes"):
                        show["availableEpisodes"] = {}
                    update_available_count_from_episode_ids(show, ttype, catalog["ids"], catalog.get("detail"))
                    changed = True
                    if old_avail is not None:
                        try:
                            if int(new_avail) > int(old_avail) and entry.get("was_caught_up"):
                                entry["has_new_release"] = True
                        except (ValueError, TypeError):
                            pass

                if show.get("_episode_catalog_state") != "loaded":
                    show["_episode_catalog_state"] = "loaded"
                    changed = True

                new_checked = int(time.time())
                if show.get("_allanime_checked_at") != new_checked:
                    show["_allanime_checked_at"] = new_checked
                    changed = True
        except Exception as e:
            debug_warn("Failed to fetch episode catalog during AllAnime refresh", e)
            # Ensure state is set even on failure to prevent repeated retries
            if show.get("_episode_catalog_state") != "loaded":
                show["_episode_catalog_state"] = "loaded"
            show["_provider_catalog_checked_at"] = int(time.time())
    else:
        try:
            prov = _provider_for_title(show)
            if hasattr(prov, "get_title"):
                try:
                    prov_show = prov.get_title(show_id)
                    if prov_show and isinstance(prov_show, dict):
                        from .metadata import apply_provider_metadata_to_history_show
                        if apply_provider_metadata_to_history_show(show, prov_show):
                            changed = True
                except Exception as e:
                    debug_warn(f"Failed to refresh title metadata for {pkey}", e)

            catalog = prov.episode_catalog(show_id, ttype=ttype)
            if catalog and catalog.get("state") == "loaded":
                old_avail = (show.get("availableEpisodes") or {}).get(ttype)
                new_avail = len(catalog.get("ids", []))
                current_ids = show.get("_episode_ids") or []
                if old_avail != new_avail or show.get("_episode_ids_ttype") != ttype or current_ids != catalog.get("ids", []):
                    if not show.get("availableEpisodes"):
                        show["availableEpisodes"] = {}
                    show["availableEpisodes"][ttype] = new_avail
                    show["_episode_ids"] = catalog.get("ids", [])
                    show["_episode_labels"] = catalog.get("labels", {})
                    show["_episode_ids_ttype"] = ttype
                    changed = True
                    if old_avail is not None:
                        try:
                            if int(new_avail) > int(old_avail) and entry.get("was_caught_up"):
                                entry["has_new_release"] = True
                        except (ValueError, TypeError):
                            pass
            # Always mark catalog as checked, even if fetch failed
            if show.get("_episode_catalog_state") != "loaded":
                show["_episode_catalog_state"] = "loaded"
                changed = True
            show["_provider_catalog_checked_at"] = int(time.time())
            if "aniListId" not in show and catalog and catalog.get("aniListId"):
                show["aniListId"] = str(catalog["aniListId"])
                changed = True
        except Exception as e:
            debug_warn(f"Failed to refresh episode catalog for {pkey}", e)
            # Ensure state is set even on failure to prevent repeated retries
            if show.get("_episode_catalog_state") != "loaded":
                show["_episode_catalog_state"] = "loaded"
            show["_provider_catalog_checked_at"] = int(time.time())

    if not show.get("description") or not show.get("genres"):
        try:
            from ..core.enrichment import enrich_show_if_missing
            if enrich_show_if_missing(show):
                changed = True
        except Exception:
            pass

    return changed


refresh_history_entry_allanime_catalog = refresh_history_entry_provider_catalog

