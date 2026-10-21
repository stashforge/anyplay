import logging

from media_cli.core.anilist import fetch_anilist_by_ids
from media_cli.core.storage import get_source_anilist_matches, save_source_anilist_matches
from media_cli.domain.matching import choose_confident_match, reset_title_cache
from media_cli.domain.metadata import provider_status_is_authoritative, title_metadata_complete


def _merge_anilist_into_provider(provider_show, anilist_show):
    """
    Merges AniList metadata into a provider show in a strictly additive manner.
    It will not overwrite fields that the provider already populated,
    unless they are empty or 'Unknown'.
    """
    # Essential tracking fields
    provider_show["_allanime_name"] = provider_show.get("name")
    provider_show["_display_name"] = anilist_show.get("name") or provider_show.get("name")
    provider_show["_display_english_name"] = anilist_show.get("englishName") or provider_show.get("englishName")
    provider_show["_anilist_list"] = anilist_show.get("_anilist_list")
    provider_show["_anilist_progress"] = anilist_show.get("_anilist_progress")
    provider_show["_anilist_score"] = anilist_show.get("_anilist_score")

    match_source = provider_show.get("_match_source") or "fuzzy"
    provider_show["anilistMatch"] = {
        "id": anilist_show.get("_id"),
        "idMal": anilist_show.get("malId"),
        "source": match_source,
    }
    if anilist_show.get("_id"):
        provider_show["aniListId"] = str(anilist_show.get("_id"))
    if anilist_show.get("malId"):
        provider_show["malId"] = int(anilist_show["malId"])

    if anilist_show.get("_next_airing_ep"):
        provider_show["_next_airing_ep"] = anilist_show["_next_airing_ep"]
        provider_show["_next_airing_time"] = anilist_show["_next_airing_time"]
        provider_show["_next_airing_at"] = anilist_show.get("_next_airing_at")

    # Additive metadata merging
    for field in ("thumbnail", "score", "genres", "episodeCount", "airedStart",
                  "airedEnd", "season", "status", "type", "format", "description"):
        provider_val = provider_show.get(field)
        al_val = anilist_show.get(field)
        if (not provider_val or provider_val == "Unknown" or provider_val == []) and al_val:
            provider_show[field] = al_val

    if anilist_show.get("status"):
        provider_show["_anilist_status"] = anilist_show["status"]
        if anilist_show["status"] == "FINISHED" and not provider_status_is_authoritative(provider_show):
            provider_show["status"] = "FINISHED"
    if any(term in str(provider_show.get("status") or "").upper() for term in ("FINISHED", "COMPLETED", "ENDED")):
        provider_show["status"] = "FINISHED"

    # Advanced Alt-Title logic (swapping english/romaji based on provider's primary name)
    main_name = provider_show.get("_display_name")

    al_romaji = anilist_show.get("romajiName")
    al_english = anilist_show.get("englishName")

    alt_names = []
    if main_name == al_romaji and al_english:
        alt_names = [al_english]
    elif main_name == al_english and al_romaji:
        alt_names = [al_romaji]

    if not alt_names:
        alt_names = list(anilist_show.get("altNames") or [])

    provider_val = provider_show.get("altNames")
    if (not provider_val or provider_val == []) and alt_names:
        provider_show["altNames"] = alt_names

    loaded = set(provider_show.get("_metadata_fields_loaded") or ())
    loaded.update(field for field in ("description", "genres") if field in anilist_show)
    provider_show["_metadata_fields_loaded"] = tuple(sorted(loaded))

    return provider_show

def enrich_provider_results_fast(results, fuzzy_anilist_results=None):
    """
    Fast in-memory matching pass. Matches provider results against the AniList search results
    already in RAM without making any extra network calls.
    Returns (results, unmatched_shows_with_ids).
    """
    if not results:
        return results, []

    reset_title_cache()

    al_shows = fuzzy_anilist_results or []
    by_anilist_id = {str(m["_id"]): m for m in al_shows if m.get("_id")}
    by_mal_id = {str(m["malId"]): m for m in al_shows if m.get("malId")}

    pending = {}
    unmatched_ids = set()
    unmatched_shows_with_ids = []

    for s in results:
        matched = None
        if s.get("aniListId") and str(s["aniListId"]) in by_anilist_id:
            matched = by_anilist_id[str(s["aniListId"])]
            s["_match_source"] = "id"
        elif s.get("malId") and str(s["malId"]) in by_mal_id:
            matched = by_mal_id[str(s["malId"])]
            s["_match_source"] = "id"

        if matched:
            pending[id(s)] = matched
        elif not title_metadata_complete(s):
            unmatched_ids.add(id(s))
            if s.get("aniListId") or s.get("malId"):
                unmatched_shows_with_ids.append(s)

    stored_lookup = {}
    if unmatched_ids and al_shows:
        ids = [s.get("_id") for s in results if id(s) in unmatched_ids and s.get("_id")]
        stored_lookup = get_source_anilist_matches(ids)

    to_save = []
    for s in results:
        matched = pending.get(id(s))

        if not matched and id(s) in unmatched_ids and al_shows:
            stored = stored_lookup.get(s.get("_id")) or {}
            stored_id = str(stored.get("_id") or "")
            if stored_id:
                matched = next(
                    (c for c in al_shows if str(c.get("_id") or "") == stored_id),
                    None,
                )
            if matched:
                s["_match_source"] = "stored"
            else:
                matched = choose_confident_match(s, al_shows)
                if matched:
                    s["_match_source"] = "fuzzy"

        if matched:
            to_save.append((s, matched))
            _merge_anilist_into_provider(s, matched)
            # Remove from unmatched if fuzzy matched
            if s in unmatched_shows_with_ids:
                unmatched_shows_with_ids.remove(s)

    if to_save:
        save_source_anilist_matches(to_save)

    return results, unmatched_shows_with_ids


def enrich_provider_results_background(unmatched_shows, token="", on_updated_callback=None):
    """
    Background worker that quietly backfills AniList metadata for shows whose IDs
    were not present in the initial query results.
    """
    unmatched_shows = [show for show in (unmatched_shows or ()) if not title_metadata_complete(show)]
    if not unmatched_shows:
        return

    al_ids = [s.get("aniListId") for s in unmatched_shows if s.get("aniListId")]
    mal_ids = [s.get("malId") for s in unmatched_shows if s.get("malId")]

    if not al_ids and not mal_ids:
        return

    try:
        exact_matches = fetch_anilist_by_ids(token or "", anilist_ids=al_ids, mal_ids=mal_ids)
        if not exact_matches:
            return

        by_al = {str(m["_id"]): m for m in exact_matches if m.get("_id")}
        by_mal = {str(m["malId"]): m for m in exact_matches if m.get("malId")}

        to_save = []
        updated = False
        for s in unmatched_shows:
            matched = None
            if s.get("aniListId") and str(s["aniListId"]) in by_al:
                matched = by_al[str(s["aniListId"])]
            elif s.get("malId") and str(s["malId"]) in by_mal:
                matched = by_mal[str(s["malId"])]

            if matched:
                s["_match_source"] = "id"
                to_save.append((s, matched))
                _merge_anilist_into_provider(s, matched)
                updated = True

        if to_save:
            save_source_anilist_matches(to_save)

        if updated and on_updated_callback:
            on_updated_callback()
    except Exception as e:
        logging.getLogger(__name__).debug(f"Background AniList enrich error: {e}")


def _persist_enrichment_state(show: dict):
    """Persist enrichment attempts and known field coverage to history."""
    if not show:
        return
    # Try multiple ID fields for matching
    show_id = str(show.get("_id") or show.get("_provider_id") or show.get("id") or "")
    if not show_id:
        return
    try:
        from ..core.storage import patch_history_entry_show
        patch_history_entry_show(
            show_id, None, show,
            fields=("_title_enriched", "_enrichment_attempted", "_metadata_fields_loaded"),
        )
    except Exception as e:
        from ..core.reporting import debug_warn
        debug_warn(f"Failed to persist enrichment state for {show_id}", e)


def enrich_provider_results(results, token, fuzzy_anilist_results=None):
    """
    Legacy sync wrapper. Performs fast in-memory enrichment first, and fetches any missing
    IDs synchronously if requested.
    """
    enriched, unmatched = enrich_provider_results_fast(results, fuzzy_anilist_results)
    if unmatched:
        enrich_provider_results_background(unmatched, token=token)
    return enriched


def enrich_show_if_missing(show: dict) -> bool:
    if not show or not isinstance(show, dict):
        return False

    has_desc = bool(show.get("description"))
    has_genres = bool(show.get("genres"))
    has_al = bool(show.get("aniListId") or show.get("anilistMatch"))

    if title_metadata_complete(show):
        already_recorded = show.get("_title_enriched") and show.get("_enrichment_attempted")
        show["_title_enriched"] = True
        show["_enrichment_attempted"] = True
        # Persist the enriched state to history
        if not already_recorded:
            _persist_enrichment_state(show)
        return False

    # Don't re-attempt enrichment if we already tried and failed (no data to show for it)
    if show.get("_enrichment_attempted") and not (has_desc and has_genres and has_al):
        return False

    if show.get("_enrichment_in_progress"):
        return False

    show["_enrichment_in_progress"] = True
    show["_enrichment_attempted"] = True
    _persist_enrichment_state(show)
    updated = False

    try:
        from ..core.anilist import search_anilist
        from ..core.reporting import debug_warn
        from ..core.storage import load_config
        from ..domain.matching import choose_confident_match
        from ..providers import get_provider
        from ..providers.shared.models import title_provider_id, title_provider_key
        from ..services.http import request_json as _req

        show_id = title_provider_id(show)
        pkey = title_provider_key(show)

        # 1. Provider title fetch (if provider implements get_title)
        if show_id and pkey:
            try:
                provider = get_provider(pkey, _req)
                get_title_fn = getattr(provider, "get_title", None)
                if get_title_fn:
                    title_data = get_title_fn(show_id)
                    if title_data and isinstance(title_data, dict):
                        for k, v in title_data.items():
                            if k == "_metadata_fields_loaded":
                                loaded = tuple(sorted(set(show.get(k) or ()) | set(v or ())))
                                if show.get(k) != loaded:
                                    show[k] = loaded
                                    updated = True
                            elif k in ("status", "episodeCount", "_next_airing_ep", "_next_airing_at", "_next_airing_time", "aniListId", "malId", "format", "score") and v:
                                if show.get(k) != v:
                                    show[k] = v
                                    updated = True
                            elif v and not show.get(k):
                                show[k] = v
                                updated = True
                            elif k == "availableEpisodes" and isinstance(v, dict):
                                show.setdefault(k, {})
                                for ep_k, ep_v in v.items():
                                    if ep_v > show[k].get(ep_k, 0):
                                        show[k][ep_k] = ep_v
                                        updated = True
            except Exception as e:
                debug_warn("Provider get_title enrichment failed", e)

        # 2. Direct AniList ID lookup
        al_id = show.get("aniListId")
        if not al_id and show_id and str(show_id).isdigit() and pkey in ("anikoto", "miruro"):
            al_id = str(show_id)
        mal_id = show.get("malId")

        token = ""
        try:
            token = load_config().get("anilist_token") or ""
        except Exception:
            pass

        if (al_id or mal_id) and not title_metadata_complete(show):
            try:
                al_ids = [int(al_id)] if al_id else None
                mal_ids = [int(mal_id)] if mal_id else None
                al_data = fetch_anilist_by_ids(token, anilist_ids=al_ids, mal_ids=mal_ids)
                if al_data:
                    _merge_anilist_into_provider(show, al_data[0])
                    updated = True
            except Exception as e:
                debug_warn("AniList ID enrichment failed", e)

        # 3. Fallback AniList search if description or ID is still missing
        if not title_metadata_complete(show) and (
            not show.get("description") or not (show.get("aniListId") or show.get("anilistMatch"))
        ):
            titles_to_try = []
            for t in (
                show.get("name"),
                show.get("englishName"),
                show.get("romajiName"),
                title_data.get("name") if 'title_data' in locals() and title_data else None,
                show.get("name", "").replace("-", "") if "-" in show.get("name", "") else None,
            ):
                if t and t not in titles_to_try:
                    titles_to_try.append(t)

            for title_name in titles_to_try:
                try:
                    candidates = search_anilist(token, title_name)
                    if not candidates:
                        import re
                        clean_name = re.sub(r'\s*[\(\[]\s*\d{4}\s*[\)\]]', '', title_name).strip()
                        if clean_name and clean_name != title_name:
                            candidates = search_anilist(token, clean_name)
                    if candidates:
                        best = choose_confident_match(show, candidates) or candidates[0]
                        _merge_anilist_into_provider(show, best)
                        updated = True
                        break
                except Exception as e:
                    debug_warn("AniList fallback title search failed", e)

        # 4. Cache poster image to disk
        thumb_url = show.get("thumbnail")
        if thumb_url and thumb_url.startswith("http"):
            try:
                import hashlib
                import os

                from ..core.storage import cover_cache_dir
                from ..ui.covers import enforce_cache_limits, fetch_cover_bytes
                w_dir = cover_cache_dir()
                os.makedirs(w_dir, exist_ok=True)
                out_path = os.path.join(w_dir, f"{hashlib.sha256(thumb_url.encode('utf-8')).hexdigest()[:32]}.jpg")
                if not os.path.exists(out_path):
                    img_data = fetch_cover_bytes(thumb_url)
                    if img_data:
                        with open(out_path, "wb") as f:
                            f.write(img_data)
                        enforce_cache_limits(w_dir)
            except Exception:
                pass

        # 5. Persist to history.json if this show is present in history
        # Only mark as enriched if we actually got useful data
        if title_metadata_complete(show):
            show["_title_enriched"] = True

        if show_id:
            try:
                from ..core.storage import patch_history_entry_show
                patch_history_entry_show(
                    show_id, None, show,
                    fields=(
                        "description", "genres", "banner", "format", "type", "score",
                        "status", "season", "airedStart", "airedEnd", "thumbnail",
                        "englishName", "romajiName", "nativeName", "altNames", "preferredAltName", "aniListId", "malId",
                        "anilistMatch", "_next_airing_ep", "_next_airing_at", "_next_airing_time",
                        "_anilist_score", "_title_enriched", "_enrichment_attempted",
                        "_metadata_fields_loaded",
                    ),
                )
            except Exception as e:
                debug_warn("Failed to persist enriched show to history", e)

        if updated:
            try:
                from ..ui.info_panel import invalidate_panel_cache
                invalidate_panel_cache()
            except Exception:
                pass

        return updated
    finally:
        show.pop("_enrichment_in_progress", None)
