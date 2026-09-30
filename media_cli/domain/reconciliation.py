"""Pure AniList/local progress reconciliation decisions."""


COMPLETED_STATUSES = {"COMPLETED", "REPEATING", "REWATCHING"}
CURRENT_STATUSES = {"CURRENT", "WATCHING"}


def reconcile_status(show, progress, positive_int):
    status = str(show.get("_anilist_list") or "").upper()
    total = positive_int(show.get("episodeCount"))
    if not total:
        total = len(show.get("_episode_ids") or []) or None
    if total and progress >= total:
        return "COMPLETED"
    if status in COMPLETED_STATUSES:
        return "REPEATING"
    if status not in CURRENT_STATUSES:
        return "CURRENT"
    return None


def decide_progress_reconciliation(
        *,
        local,
        remote,
        last_synced,
        status,
        anilist_source,
        sync_enabled):
    status = str(status or "").upper()

    if anilist_source and not sync_enabled:
        if local is None or last_synced is None or local == last_synced:
            return {"action": "import", "authority": "AL"}
        return {
            "action": "local",
            "authority": "LOCAL",
            "progress": local,
        }

    if anilist_source and last_synced is None:
        return {"action": "import", "authority": "AL"}

    if not sync_enabled:
        return {
            "action": "local",
            "authority": "LOCAL",
            "progress": local or 0,
        }

    if local is None:
        return {"action": "import", "authority": "AL"}
    if local == remote:
        return {
            "action": "equal",
            "authority": "AL",
            "progress": local,
        }

    if last_synced is not None:
        local_changed = local != last_synced
        remote_changed = remote != last_synced
        if (
            local_changed
            and remote_changed
            and status in COMPLETED_STATUSES
        ):
            return {
                "action": "conflict",
                "authority": "LOCAL",
                "local": local,
                "anilist": remote,
            }
        if local_changed and not remote_changed:
            return {"action": "push", "progress": local}
        if remote_changed and not local_changed:
            return {"action": "import", "authority": "AL"}

    if status == "COMPLETED" and local < remote:
        return {"action": "push", "progress": local}
    if local > remote:
        return {"action": "push", "progress": local}
    return {"action": "import", "authority": "AL"}
