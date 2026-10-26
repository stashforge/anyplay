"""Anime progress, airing, year, and metadata-line formatting."""

import time

DIM = "\033[38;5;248m"


def format_time(secs):
    if not secs:
        return ""
    days, seconds = divmod(secs, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    pieces = []
    if days > 0:
        pieces.append(f"{int(days)}d")
    if hours > 0:
        pieces.append(f"{int(hours)}h")
    if minutes > 0 and len(pieces) < 2:
        pieces.append(f"{int(minutes)}m")
    return " ".join(pieces[:2])


def format_video_time(secs):
    if not secs or int(secs) <= 0:
        return "0:00"
    secs = int(secs)
    hours, remainder = divmod(secs, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def set_next_airing_fields(show, next_air):
    if not show or not next_air:
        return
    show["_next_airing_ep"] = next_air.get("episode")
    remaining = next_air.get("timeUntilAiring")
    show["_next_airing_time"] = remaining
    try:
        airing_at = int(next_air.get("airingAt"))
    except (TypeError, ValueError):
        airing_at = None
    if airing_at:
        show["_next_airing_at"] = airing_at
        return
    try:
        show["_next_airing_at"] = int(time.time()) + int(remaining)
    except (TypeError, ValueError):
        pass


def positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def has_untrusted_releasing_total(anime, available_count=None):
    provider = str((anime or {}).get("_provider") or (anime or {}).get("provider") or "").casefold()
    anime_type = str((anime or {}).get("type") or "").casefold()
    status = str((anime or {}).get("status") or "").upper()
    if "RELEASING" not in status:
        return False
    if provider not in {"animexin", "lucifer", "animekhor"} and anime_type != "ona":
        return False
    total = positive_int((anime or {}).get("episodeCount"))
    if not total:
        return False
    available = positive_int(available_count)
    return bool(available and total <= available)


def provider_status_is_authoritative(anime):
    provider = str((anime or {}).get("_provider") or (anime or {}).get("provider") or "").casefold()
    media_type = str((anime or {}).get("type") or "").casefold()
    media_format = str((anime or {}).get("format") or "").casefold()
    return provider in {"animexin", "lucifer", "animekhor"} and (
        media_type in {"ona", "donghua"} or media_format == "donghua"
    )


def format_ep_progress(label, progress, total, local_only=False):
    import decimal as _dec
    from .episodes import parse_episode_dual_numbers

    progress_text = str(progress).strip()
    primary, secondary = parse_episode_dual_numbers(progress_text)
    if primary:
        progress = primary
    try:
        p = _dec.Decimal(str(progress))
        if p < 0:
            p = _dec.Decimal(0)
        # Display as int when whole, preserve decimal string otherwise
        progress = int(p) if p == p.to_integral_value() else str(p.normalize())
    except (_dec.InvalidOperation, TypeError, ValueError):
        progress = progress_text
        if progress.lower().startswith("episode "):
            progress = progress[8:].strip()
        elif progress.lower() in ("movie", "special", "ova") or not progress.replace(".", "", 1).isdigit():
            progress = "0"
        if not progress or progress.lower() == "none":
            return ""
    prefix = f"\033[38;5;244mWatched{DIM}"
    value = f"{progress}/{total}" if total else str(progress)
    if secondary:
        value += f" [{secondary}]"
    return f"{prefix} {value}"


def format_total_episodes(anime, ttype="sub"):
    total = positive_int(anime.get("episodeCount"))
    if not total:
        avail_map = anime.get("availableEpisodes") or {}
        if isinstance(avail_map, dict):
            total = positive_int(avail_map.get(ttype)) or positive_int(avail_map.get("sub")) or positive_int(avail_map.get("dub"))
        if not total:
            total = positive_int(anime.get("_provider_latest"))
    return f"\033[38;5;244mEP{DIM} {total}" if total else ""


def normalize_anilist_list_status(status):
    normalized = str(status or "").upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "WATCHING": "CURRENT",
        "PLAN_TO_WATCH": "PLANNING",
        "REWATCHING": "REPEATING",
    }
    return aliases.get(normalized, normalized)


def _progress_display_values(anime, local_only=False, ttype="sub"):
    """Resolve displayed watched value and total before adding text or colors."""
    total = positive_int(anime.get("episodeCount"))
    orig_total = positive_int(anime.get("originalEpisodeCount"))
    if orig_total:
        total = max(total or 0, orig_total)

    avail_count = (anime.get("availableEpisodes") or {}).get(ttype) if isinstance(anime.get("availableEpisodes"), dict) else None
    try:
        avail_count = int(avail_count) if avail_count is not None else None
    except (TypeError, ValueError):
        avail_count = None
    if avail_count is None and anime.get("_episode_ids"):
        avail_count = len(anime["_episode_ids"])
    if total and avail_count and (total < avail_count or has_untrusted_releasing_total(anime, avail_count)):
        total = None

    local_progress = anime.get("_local_progress")
    local_label = anime.get("_local_episode_label")
    if not local_label:
        local_label = local_progress

    from .episodes import parse_episode_dual_numbers, resolve_dual_episode_label
    dual_lbl = resolve_dual_episode_label(anime, local_label or local_progress)
    if dual_lbl:
        local_label = dual_lbl

    import decimal

    local_prim, local_sec = parse_episode_dual_numbers(str(local_label or ""))
    if not local_prim:
        local_label = local_progress
    try:
        local_num = decimal.Decimal(str(local_prim or local_label))
    except (decimal.InvalidOperation, TypeError, ValueError):
        local_num = decimal.Decimal(0)

    # Only adjust total if not a dual-number bracket tag
    if total is not None and not local_sec:
        try:
            total_dec = decimal.Decimal(str(total))
            if local_num > total_dec:
                total = local_label
        except decimal.InvalidOperation:
            pass

    if not local_only:
        sync_enabled = bool(anime.get("_sync_enabled"))
        anilist_progress = anime.get("_anilist_progress")
        authority = anime.get("_progress_authority")
        if anime.get("_sync_conflict") and local_progress is not None:
            return local_label, total
        anilist_num = 0
        if anilist_progress is not None:
            try:
                anilist_num = int(float(str(anilist_progress)))
            except (ValueError, TypeError):
                pass

        anilist_context = bool(anime.get("_anilist_context"))
        if (sync_enabled or anilist_context) and anilist_progress is not None:
            if local_num > anilist_num and not local_sec:
                return local_label, total
            display_p = local_label if local_sec else anilist_progress
            return display_p, total
        if authority == "AL" and sync_enabled and anilist_progress is not None:
            display_p = local_label if local_sec else anilist_progress
            return display_p, total
    if local_progress is not None:
        return local_label, total
    return None, total


def format_progress(anime, local_only=False, ttype="sub"):
    from .episodes import episode_display_summary, parse_episode_dual_numbers

    progress, total = _progress_display_values(anime, local_only, ttype)
    primary, secondary = parse_episode_dual_numbers(str(progress or ""))
    if primary and not secondary:
        summary = episode_display_summary(anime, ttype)
        if summary is not None:
            latest_primary, latest_secondary = parse_episode_dual_numbers(summary[1])
            if primary == latest_primary and latest_secondary:
                progress = f"{primary} [{latest_secondary}]"
    return format_ep_progress("LOCAL", progress, total, local_only) if progress is not None else ""


def format_available_episodes(anime, ttype="sub", local_only=False):
    import decimal
    from .episodes import episode_display_summary, extract_episode_tags, parse_episode_dual_numbers

    if str(anime.get("status") or "").upper() != "RELEASING":
        return ""

    eids = anime.get("_episode_ids") or []
    sec_tag = None
    latest_label = ""

    summary = episode_display_summary(anime, ttype)
    if summary is not None:
        _count, last_lbl = summary
        _primary, sec_tag = parse_episode_dual_numbers(last_lbl)
        latest_label, _latest_tags = extract_episode_tags(last_lbl)
        latest_label = latest_label.strip()
        if latest_label.lower().startswith("episode "):
            latest_label = latest_label[8:].strip()
        elif latest_label.lower().startswith("ep "):
            latest_label = latest_label[3:].strip()
        latest_label = latest_label.removesuffix(" END").strip()
        numeric_label = latest_label.partition("[")[0].strip()
        if not numeric_label.replace(".", "", 1).isdigit():
            latest_label = ""

    available = anime.get("availableEpisodes", {}).get(ttype) if isinstance(anime.get("availableEpisodes"), dict) else None
    try:
        available = int(available)
    except (TypeError, ValueError):
        available = None

    if (available is None or available <= 0) and eids:
        available = len(eids)

    watched, total = _progress_display_values(anime, local_only, ttype)

    def render_available(count):
        if count <= 0:
            return ""
        primary, _secondary = parse_episode_dual_numbers(str(watched or ""))
        try:
            if decimal.Decimal(str(primary or watched)) == decimal.Decimal(str(count)):
                return ""
        except decimal.InvalidOperation:
            pass
        if sec_tag:
            display = latest_label or f"{count} [{sec_tag}]"
            return f"Avail {display}"
        if total is not None and str(count) == str(total):
            return ""
        return f"Avail {latest_label or count}"

    if available is not None and available > 0:
        return render_available(available)

    next_episode = positive_int(anime.get("_next_airing_ep"))
    if next_episode:
        airing_at = anime.get("_next_airing_at")
        try:
            airing_passed = bool(airing_at and float(airing_at) <= time.time())
        except (TypeError, ValueError):
            airing_passed = False
        available_count = next_episode if airing_passed else max(0, next_episode - 1)
        return render_available(available_count)

    return ""


def format_next_airing(anime, now=None):
    episode = anime.get("_next_airing_ep")
    airing_at = anime.get("_next_airing_at")
    if not episode or not airing_at:
        return ""
    # If this episode is already available in the catalog, do not show countdown or aired message
    avail_count = (anime.get("availableEpisodes") or {}).get("sub") if isinstance(anime.get("availableEpisodes"), dict) else None
    if avail_count is None:
        avail_count = len(anime.get("_episode_ids") or [])
    try:
        if avail_count and int(avail_count) >= int(episode):
            return ""
    except (TypeError, ValueError):
        pass
    try:
        current_time = time.time() if now is None else now
        remaining = int(float(airing_at)) - int(current_time)
    except (TypeError, ValueError):
        return ""
    if remaining <= 0:
        return f"Next EP {episode} aired"
    return f"Next EP {episode} in {format_time(remaining)}"


def format_years(start_year, end_year, status=None):
    start_year = positive_int(start_year)
    end_year = positive_int(end_year)
    status = str(status or "").upper()
    if start_year and end_year and start_year != end_year:
        return f"{start_year} - {end_year}"
    if start_year:
        if ("RELEASING" in status or status == "AIRING") and not any(term in status for term in ("FINISHED", "COMPLETED", "ENDED")):
            return f"{start_year} -"
        return str(start_year)
    if end_year:
        return str(end_year)
    if any(term in status for term in ("NOT_YET_RELEASED", "NOT YET RELEASED", "UPCOMING")):
        return "TBA"
    return ""


def should_refresh_anilist(anime, now=None):
    if str(anime.get("status") or "").upper() != "RELEASING":
        return False
    airing_at = anime.get("_next_airing_at")
    if not airing_at:
        return bool(
            anime.get("_next_airing_ep") or anime.get("_next_airing_time")
        )
    try:
        current_time = time.time() if now is None else now
        return int(float(airing_at)) <= int(current_time)
    except (TypeError, ValueError):
        return True


def anilist_list_status_label(anime, local_only=False, hide_status=None):
    if not local_only and (anime.get("_sync_enabled") or anime.get("_anilist_context")):
        show_anilist_status = bool(anime.get("_anilist_list"))
        anilist_list = normalize_anilist_list_status(anime.get("_anilist_list")) if show_anilist_status else ""
        if anilist_list and hide_status and anilist_list == normalize_anilist_list_status(hide_status):
            return ""
        if anilist_list in ("CURRENT", "WATCHING"):
            return f"\033[32mAL WATCHING{DIM}"
        if anilist_list == "COMPLETED":
            return f"\033[36mAL COMPLETED{DIM}"
        if anilist_list in ("PLANNING", "PLAN_TO_WATCH"):
            return f"\033[33mAL PLANNING{DIM}"
        if anilist_list == "DROPPED":
            return f"\033[31mAL DROPPED{DIM}"
        if anilist_list == "PAUSED":
            return f"\033[35mAL PAUSED{DIM}"
        if anilist_list in ("REPEATING", "REWATCHING"):
            return f"\033[32mAL REWATCHING{DIM}"
    return ""



def anime_status_label(anime):
    provider_authority = provider_status_is_authoritative(anime)
    status = str(
        anime.get("status")
        if provider_authority
        else anime.get("_anilist_status") or anime.get("status") or ""
    ).upper()
    color = "\033[38;5;250m"
    if any(term in status for term in ("FINISHED", "COMPLETED", "ENDED")):
        return f"{color}FINISHED{DIM}"
    if "RELEASING" in status or status == "AIRING":
        return f"{color}AIRING{DIM}"
    if any(term in status for term in ("NOT_YET_RELEASED", "NOT YET RELEASED", "UPCOMING")):
        return f"{color}UPCOMING{DIM}"
    if "CANCELLED" in status:
        return f"{color}CANCELLED{DIM}"
    if "HIATUS" in status:
        return f"{color}HIATUS{DIM}"
    return ""


def anilist_status_label(anime, local_only=False, hide_status=None):
    return anilist_list_status_label(
        anime,
        local_only=local_only,
        hide_status=hide_status,
    ) or anime_status_label(anime)


def _info_metadata_fields(
    anime,
    ttype="sub",
    now=None,
    override_ep_str=None,
    local_only=False,
    hide_anilist_status=None,
):
    details = []
    anilist_label = anilist_list_status_label(
        anime,
        local_only=local_only,
        hide_status=hide_anilist_status,
    )
    media_status_label = anime_status_label(anime)
    if override_ep_str:
        if override_ep_str.lower() in ("movie", "full"):
            progress = f"\033[38;5;244m{override_ep_str.title()}{DIM}"
        else:
            progress = f"\033[38;5;244mEP{DIM} {override_ep_str}"
    else:
        progress = format_progress(anime, local_only=local_only, ttype=ttype)
        if not progress:
            progress = format_total_episodes(anime, ttype=ttype)

    available = format_available_episodes(anime, ttype, local_only=local_only)
    next_airing = format_next_airing(anime, now)
    anime_type = str(anime.get("type") or "UNKNOWN").upper()
    if anime_type == "UNKNOWN":
        anime_type = "UNKNOWN TYPE"

    sub_dub_label = str(ttype or "sub").upper()
    def _ext_year(val):
        if isinstance(val, dict): return val.get("year")
        if isinstance(val, str):
            import re
            m = re.search(r'\b(20\d{2}|19\d{2})\b', val)
            return m.group(1) if m else val
        return None

    aired_start = anime.get("airedStart")
    aired_end = anime.get("airedEnd")
    years_status = anime.get("status") if provider_status_is_authoritative(anime) else anime.get("_anilist_status") or anime.get("status")
    if any(term in str(anime.get("status") or "").upper() for term in ("FINISHED", "COMPLETED", "ENDED")):
        years_status = "FINISHED"
    years = format_years(
        _ext_year(aired_start),
        _ext_year(aired_end),
        years_status,
    )
    score = anime.get("score")
    score_text = f"★ {score}" if score else "★ -"

    if anilist_label:
        status_role = {
            "CURRENT": "anilist.watching", "COMPLETED": "anilist.completed",
            "PLANNING": "anilist.planning", "DROPPED": "anilist.dropped",
            "PAUSED": "anilist.paused", "REPEATING": "anilist.rewatching",
        }[normalize_anilist_list_status(anime.get("_anilist_list"))]
        details.append((status_role, anilist_label))
    try:
        from media_cli.core.network import is_confirmed_offline
        if is_confirmed_offline():
            details.append(("metadata.offline", "\033[38;2;230;80;80mOFFLINE\033[0m"))
    except Exception:
        pass
    if media_status_label:
        details.append(("metadata.status", media_status_label))
    if anime_type:
        details.append(("details", anime_type))
    if sub_dub_label:
        details.append(("details", sub_dub_label))
    if progress:
        details.append(("progress", progress))
    if available:
        details.append(("details", available))
    if next_airing:
        details.append(("metadata.airing", f"\033[38;5;117m{next_airing}{DIM}"))
    if years:
        details.append(("details", years))
    details.append(("details", score_text))
    return tuple(details)


def format_info_metadata_line(
    anime, ttype="sub", now=None, override_ep_str=None, local_only=False, hide_anilist_status=None,
):
    """Compatibility formatter; preserve existing string callers and colors."""
    fields = _info_metadata_fields(anime, ttype, now, override_ep_str, local_only, hide_anilist_status)
    return " • ".join(text for _role, text in fields)


def format_info_metadata_parts(
    anime, ttype="sub", now=None, override_ep_str=None, local_only=False, hide_anilist_status=None,
) -> tuple[tuple[str, str], ...]:
    """Return plain (text, role) pairs using the same metadata decisions.

    This temporary domain adapter removes styling from known legacy producers.
    Roles come from field provenance, never from ANSI colors or UI text guessing.
    Progress producers have a fixed label/value shape, including local totals.
    """
    from ..core.terminal import sanitize_terminal_text, strip_ansi

    fields = _info_metadata_fields(anime, ttype, now, override_ep_str, local_only, hide_anilist_status)
    parts = []
    for index, (role, legacy_text) in enumerate(fields):
        if index:
            parts.append((" • ", "metadata.separator"))
        if role == "progress":
            for position, progress in enumerate(strip_ansi(legacy_text).split(" • ")):
                if position:
                    parts.append((" • ", "metadata.separator"))
                label, space, value = progress.partition(" ")
                if label:
                    parts.append((sanitize_terminal_text(label), "muted"))
                if space:
                    parts.append((" " + sanitize_terminal_text(value), "details"))
        else:
            parts.append((sanitize_terminal_text(legacy_text), role))
    return tuple(parts)


def title_metadata_complete(show: dict | None) -> bool:
    """Distinguish authoritative empty fields from details not yet requested."""
    if not show or not (show.get("aniListId") or show.get("anilistMatch")):
        return False
    loaded = show.get("_metadata_fields_loaded") or ()
    if not isinstance(loaded, (tuple, list)):
        loaded = ()
    return all(show.get(field) or field in loaded for field in ("description", "genres"))


def _apply_local_watch_state(show, hist_progress, hist_label, *, prefer_watched_episodes=False):
    """Apply one progress policy to individual titles and batch/list snapshots."""
    from .episodes import clean_episode_identifier, episode_id_at, episode_index_for_id, parse_episode_dual_numbers

    watched = [str(ep) for ep in show.get("watched_episodes", [])]
    if watched and (prefer_watched_episodes or (hist_progress is None and hist_label is None)):
        episode_ids = [str(ep) for ep in (show.get("_episode_ids") or [])]
        labels = show.get("_episode_labels") or {}
        best_idx = None
        for watched_ep in watched:
            idx = episode_index_for_id(episode_ids, watched_ep, labels=labels)
            if idx is not None and (best_idx is None or idx > best_idx):
                best_idx = idx
        if best_idx is not None:
            best_eid = episode_id_at(episode_ids, best_idx)
            best_label = labels.get(best_eid) or labels.get(str(best_eid)) or best_eid
            primary, _secondary = parse_episode_dual_numbers(str(best_label))
            clean = primary or clean_episode_identifier(str(best_label)) or str(best_idx + 1)
            try:
                show["_local_progress"] = int(float(clean))
            except (TypeError, ValueError):
                show["_local_progress"] = best_idx + 1
            show["_local_episode_label"] = best_label
            return

        def safe_num(value):
            try:
                return float(clean_episode_identifier(str(value)) or value)
            except (TypeError, ValueError):
                return -1

        show["_local_progress"] = len(watched)
        show["_local_episode_label"] = max(watched, key=safe_num)
        return
    show["_local_progress"] = hist_progress if hist_progress is not None else hist_label
    show["_local_episode_label"] = hist_label


def prepare_show_display_state(show, ttype="sub", sync_enabled=None, prefer_watched_episodes=False,
                               *, history_entries=None):
    if not show:
        return show
    from media_cli.context import FLAGS as runtime_flags
    from media_cli.core.storage import get_local_episode_label, get_local_progress, get_title_sync

    if runtime_flags.incognito_mode:
        show["_sync_enabled"] = False
        show.pop("_anilist_progress", None)
        show.pop("_anilist_list", None)
        show.pop("watched_episodes", None)
        show["_progress_authority"] = "LOCAL"
        if history_entries is None:
            show["_local_progress"] = get_local_progress(show, ttype)
            show["_local_episode_label"] = get_local_episode_label(show, ttype)
        else:
            show["_local_progress"] = get_local_progress(show, ttype, history_entries=history_entries)
            show["_local_episode_label"] = get_local_episode_label(show, ttype, history_entries=history_entries)
        return show
    raw_anilist_show = bool(
        show.get("_anilist_list")
        and not show.get("_allanime_name")
        and not show.get("aniListId")
    )
    if raw_anilist_show:
        show["aniListId"] = str(show.get("_id"))
    if runtime_flags.sync_force_off:
        sync_enabled = False
    elif sync_enabled is None and raw_anilist_show:
        sync_enabled = True
    elif sync_enabled is None and runtime_flags.sync_force_on and show.get("aniListId"):
        sync_enabled = True
    elif sync_enabled is None:
        sync_enabled = get_title_sync(show)
    show["_sync_enabled"] = bool(sync_enabled)

    hist_progress = get_local_progress(show, ttype)
    hist_label = get_local_episode_label(show, ttype)
    _apply_local_watch_state(show, hist_progress, hist_label, prefer_watched_episodes=prefer_watched_episodes)

    if not show.get("aniListId") and not show.get("_episode_ids"):
        if not show.get("_hist_backfill_checked"):
            show["_hist_backfill_checked"] = True
            from media_cli.core.storage import get_history_entry
            entry = get_history_entry(show, ttype)
            if entry and isinstance(entry.get("show"), dict):
                hist_show = entry["show"]
                for k in (
                    "altNames",
                    "englishName",
                    "nativeName",
                    "status",
                    "episodeCount",
                    "aniListId",
                    "malId",
                    "score",
                    "genres",
                    "_next_airing_ep",
                    "_next_airing_at",
                    "_next_airing_time",
                    "_episode_ids",
                    "_episode_labels",
                ):
                    if not show.get(k) and hist_show.get(k):
                        show[k] = hist_show[k]
    if not show.get("_progress_authority"):
        show["_progress_authority"] = "AL" if sync_enabled else "LOCAL"
    return show


def batch_prepare_shows_display_state(shows, ttype="sub"):
    """Pre-warm cheap metadata and watch progress for a list of shows in a single in-memory pass."""
    if not shows:
        return shows
    from media_cli.context import FLAGS as runtime_flags
    from media_cli.core.storage import load_history, load_prefs
    from media_cli.domain.history import local_progress
    from media_cli.domain.matching import is_same_show
    from media_cli.state import preferences as preference_state

    if runtime_flags.incognito_mode:
        history_list = load_history()
        for s in shows:
            if isinstance(s, dict):
                prepare_show_display_state(s, ttype, history_entries=history_list)
        return shows

    try:
        history_list = load_history()
    except Exception:
        history_list = []

    try:
        prefs = load_prefs()
    except Exception:
        prefs = {}

    # Build fast O(1) history indexes
    id_to_entry = {}
    al_to_entry = {}
    name_to_entry = {}
    for entry in history_list:
        if not isinstance(entry, dict):
            continue
        es = entry.get("show")
        if not isinstance(es, dict):
            continue
        eid = es.get("_id")
        entry_ttype = entry.get("translation_type", "sub")
        if eid:
            id_to_entry[(str(eid), entry_ttype)] = entry
        al_id = es.get("aniListId")
        if al_id:
            al_to_entry[(str(al_id), entry_ttype)] = entry
        ename = es.get("name")
        if ename:
            name_to_entry[(str(ename).strip().lower(), entry_ttype)] = entry

    preferred_history = tuple(entry for entry in history_list if entry.get("translation_type", "sub") == ttype)
    fallback_history = tuple(entry for entry in history_list if entry.get("translation_type", "sub") != ttype)
    matching_history = preferred_history + fallback_history
    for show in shows:
        if not isinstance(show, dict):
            continue

        raw_anilist_show = bool(
            show.get("_anilist_list")
            and not show.get("_allanime_name")
            and not show.get("aniListId")
        )
        if raw_anilist_show:
            show["aniListId"] = str(show.get("_id"))

        if runtime_flags.sync_force_off:
            sync_enabled = False
        elif raw_anilist_show or (runtime_flags.sync_force_on and show.get("aniListId")):
            sync_enabled = True
        else:
            sync_pref = preference_state.title_sync_preference(prefs, show)
            sync_enabled = sync_pref is True
        show["_sync_enabled"] = bool(sync_enabled)

        sid = str(show.get("_id") or "")
        sal = str(show.get("aniListId") or "")
        sname = str(show.get("name") or "").strip().lower()

        matched_entry = id_to_entry.get((sid, ttype)) or al_to_entry.get((sal, ttype)) or name_to_entry.get((sname, ttype))
        if matched_entry is None:
            for entry in matching_history:
                entry_show = entry.get("show")
                if is_same_show(entry_show, show):
                    matched_entry = entry
                    break

        if matched_entry:
            eid = matched_entry.get("episode", 0)
            if str(eid) in ("0", "0.0"):
                show["_local_progress"] = 0
                show["_local_episode_label"] = "0"
            else:
                labels = (show or {}).get("_episode_labels") or (matched_entry.get("show") or {}).get("_episode_labels") or {}
                lbl = None
                if labels and str(eid) in labels:
                    lbl = str(labels[str(eid)])
                elif matched_entry.get("episode_label"):
                    lbl = matched_entry["episode_label"]
                elif str(eid).startswith(("http://", "https://", "/")):
                    from ..domain.episodes import clean_episode_identifier
                    lbl = clean_episode_identifier(str(eid))
                show["_local_episode_label"] = lbl or str(eid)
                progress = local_progress((matched_entry,), show, ttype)
                hist_label = show["_local_episode_label"] if matched_entry.get("translation_type", "sub") == ttype else None
                _apply_local_watch_state(show, progress, hist_label)
            hist_show = matched_entry.get("show") or {}
            if not show.get("aniListId") and not show.get("_episode_ids"):
                for k in (
                    "altNames", "preferredAltName", "romajiName", "englishName", "nativeName", "status", "episodeCount",
                    "aniListId", "malId", "score", "genres", "_next_airing_ep",
                    "_next_airing_at", "_next_airing_time", "_episode_ids", "_episode_labels"
                ):
                    if not show.get(k) and hist_show.get(k):
                        show[k] = hist_show[k]
        else:
            _apply_local_watch_state(show, None, None)

        if not show.get("_progress_authority"):
            show["_progress_authority"] = "AL" if sync_enabled else "LOCAL"

    return shows


def apply_provider_metadata_to_history_show(show, provider_show):
    changed = False

    if provider_show.get("_metadata_fields_loaded"):
        loaded = tuple(sorted(set(show.get("_metadata_fields_loaded") or ()) |
                              set(provider_show["_metadata_fields_loaded"])))
        if tuple(show.get("_metadata_fields_loaded") or ()) != loaded:
            show["_metadata_fields_loaded"] = loaded
            changed = True

    for key in ("status", "episodeCount", "airedStart", "score", "type", "season"):
        val = provider_show.get(key)
        if key == "status" and val and any(term in str(val).upper() for term in ("FINISHED", "COMPLETED", "ENDED")):
            val = "FINISHED"
        if val is not None and str(show.get(key)) != str(val):
            show[key] = val
            changed = True

    for key in ("thumbnail", "name", "englishName", "nativeName", "description", "banner", "format"):
        val = provider_show.get(key)
        if val and not show.get(key):
            show[key] = val
            changed = True

    for key in ("genres", "altNames"):
        val = provider_show.get(key)
        if val and not show.get(key):
            show[key] = list(val)
            changed = True

    if provider_show.get("aniListId") and not show.get("aniListId"):
        show["aniListId"] = str(provider_show["aniListId"])
        changed = True

    if provider_show.get("malId") and not show.get("malId"):
        show["malId"] = provider_show["malId"]
        changed = True

    avail = provider_show.get("availableEpisodes")
    if avail and isinstance(avail, dict):
        current_avail = show.get("availableEpisodes") or {}
        if current_avail != avail:
            show["availableEpisodes"] = dict(avail)
            changed = True

    # Reconcile airing fields: clear if provider_show has no airing ep or if already available
    avail_sub = (show.get("availableEpisodes") or {}).get("sub") or 0
    try:
        avail_sub = int(avail_sub)
    except (ValueError, TypeError):
        avail_sub = 0

    prov_airing_ep = provider_show.get("_next_airing_ep")
    if prov_airing_ep is not None:
        try:
            if int(prov_airing_ep) <= avail_sub:
                prov_airing_ep = None
        except (ValueError, TypeError):
            pass

    if prov_airing_ep is None:
        for key in ("_next_airing_ep", "_next_airing_at", "_next_airing_time"):
            if show.get(key) is not None:
                show[key] = None
                changed = True
    else:
        for key in ("_next_airing_ep", "_next_airing_at", "_next_airing_time"):
            val = provider_show.get(key)
            if show.get(key) != val:
                show[key] = val
                changed = True

    return changed


apply_allanime_metadata_to_history_show = apply_provider_metadata_to_history_show
