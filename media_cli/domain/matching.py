"""Pure title normalization and provider-match scoring."""

import difflib
import re
import unicodedata
from functools import lru_cache

_title_cache = {}


def _cached_match_titles(item):
    key = tuple(str(value or "") for value in _match_title_values(item))
    cached = _title_cache.get(key)
    if cached is None:
        cached = tuple(match_titles(item))
        if len(_title_cache) >= 512:
            _title_cache.pop(next(iter(_title_cache)))
        _title_cache[key] = cached
    return cached


def reset_title_cache():
    """Call this once per batch (e.g. start of enrich_provider_results) to
    prevent unbounded growth in long-running processes."""
    _title_cache.clear()


def _positive_int(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def normalized_match_title(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\x27\u2019\u2018\u0060\u00b4]", "", text)
    text = re.sub(r"\b(\d+)(?:st|nd|rd|th)\b", r"\1", text)
    text = text.replace("&", " and ")
    tokens = re.findall(r"[\w]+", text, re.UNICODE)
    return " ".join(tokens)


def _match_title_values(item):
    return (
        item.get("name"),
        item.get("englishName"),
        item.get("romajiName"),
        item.get("nativeName"),
        item.get("_display_name"),
        item.get("_display_english_name"),
        item.get("_allanime_name"),
        item.get("_folder_name"),
        *(item.get("altNames") or []),
    )


def match_titles(item):
    titles = []
    seen = set()
    for value in _match_title_values(item):
        title = normalized_match_title(value)
        if title and title not in seen:
            titles.append(title)
            seen.add(title)
        if len(titles) >= 16:
            break
    return titles



def explicit_season_number(titles):
    numbers = set()
    patterns = (
        r"\bseason\s+(\d+)\b",
        r"\b(\d+)\s+season\b",
        r"\bs(\d+)\b",
        r"\b第\s*(\d+)\s*(?:期|季)\b",
    )
    for title in titles:
        for pattern in patterns:
            match = re.search(pattern, title)
            if match:
                numbers.add(int(match.group(1)))
    return next(iter(numbers)) if len(numbers) == 1 else None


def title_match_score(left_titles, right_titles):
    return _cached_title_match_score(tuple(left_titles), tuple(right_titles))


@lru_cache(maxsize=512)
def _cached_title_match_score(left_titles, right_titles):
    """Cache only immutable text inputs; metadata decisions remain uncached."""
    best = 0
    exact = False
    for left in left_titles:
        for right in right_titles:
            left_compact = left.replace(" ", "")
            right_compact = right.replace(" ", "")
            if left == right or left_compact == right_compact:
                best = max(best, 30)
                exact = True
                continue

            shorter = min(len(left_compact), len(right_compact))
            longer = max(len(left_compact), len(right_compact))
            if shorter >= 4 and (
                left_compact in right_compact
                or right_compact in left_compact
            ):
                coverage = shorter / max(1, longer)
                if coverage >= 0.70:
                    best = max(best, 18 + int(coverage * 6))

            ratios = (
                difflib.SequenceMatcher(None, left, right).ratio(),
                difflib.SequenceMatcher(
                    None, left_compact, right_compact
                ).ratio(),
                difflib.SequenceMatcher(
                    None,
                    " ".join(sorted(left.split())),
                    " ".join(sorted(right.split())),
                ).ratio(),
            )
            ratio = max(ratios)
            if ratio >= 0.82:
                best = max(best, int(ratio * 27))
    return best, exact


def _extract_item_year(item):
    if not isinstance(item, dict):
        return None
    # 1. Primary: explicit year in object date fields
    for d in (item.get("airedStart"), item.get("startDate"), item.get("season"), item.get("year"), item.get("releaseDate")):
        if isinstance(d, dict):
            y = _positive_int(d.get("year"))
            if y and y > 1900:
                return y
        elif isinstance(d, (int, float)) and d > 1900:
            return int(d)
        elif isinstance(d, str) and d.strip():
            parts = d.strip().split("-")
            if parts[0].isdigit() and int(parts[0]) > 1900:
                return int(parts[0])
    return None


def _extract_item_season(item, titles):
    # 1. Primary: explicit season in object metadata
    s_obj = item.get("season")
    if isinstance(s_obj, int) and s_obj > 0:
        return s_obj
    elif isinstance(s_obj, dict):
        num = _positive_int(s_obj.get("number") or s_obj.get("season"))
        if num:
            return num
    if item.get("_season") and isinstance(item.get("_season"), int) and item.get("_season") > 0:
        return item.get("_season")

    # 2. Secondary fallback: parse from titles
    return explicit_season_number(titles)


def _extract_item_part(item, titles):
    part = item.get("_part") or item.get("part")
    if isinstance(part, int) and part > 0:
        return part
    import re
    for t in titles:
        if not t:
            continue
        m = re.search(r"(?i)\b(?:part|cour)\s*(\d+)\b", str(t))
        if m:
            return int(m.group(1))
    return None


def match_score_details(anilist, allmanga):
    anilist_titles = _cached_match_titles(anilist)
    allmanga_titles = _cached_match_titles(allmanga)
    title_score, exact_title = title_match_score(
        anilist_titles, allmanga_titles
    )
    details = {
        "score": title_score,
        "title_score": title_score,
        "exact_title": exact_title,
        "contradiction": "",
    }

    # Signal 1: Native Title match
    al_nat = normalized_match_title(anilist.get("nativeName"))
    am_nat = normalized_match_title(allmanga.get("nativeName"))
    if al_nat and am_nat:
        if al_nat == am_nat:
            details["score"] += 20
            details["exact_title"] = True
        elif len(al_nat) >= 3 and len(am_nat) >= 3 and (al_nat in am_nat or am_nat in al_nat):
            details["score"] += 10

    # Signal 2: Franchise Base comparison
    al_fran = normalized_match_title(anilist.get("_franchise"))
    am_fran = normalized_match_title(allmanga.get("_franchise"))
    if al_fran and am_fran:
        if al_fran == am_fran or al_fran in am_fran or am_fran in al_fran:
            details["score"] += 15

    # Signal 3: Release Year (Object-first)
    anilist_year = _extract_item_year(anilist)
    allmanga_year = _extract_item_year(allmanga)
    if anilist_year and allmanga_year:
        year_gap = abs(anilist_year - allmanga_year)
        if year_gap > 1:
            details["contradiction"] = "year"
            return details
        details["score"] += 18 if year_gap == 0 else 8

    # Signal 4: Season Number (Object-first, title parsing secondary)
    anilist_season = _extract_item_season(anilist, anilist_titles)
    allmanga_season = _extract_item_season(allmanga, allmanga_titles)
    if anilist_season and allmanga_season and anilist_season != allmanga_season:
        details["contradiction"] = "season"
        return details
    elif anilist_season and anilist_season > 1 and allmanga_season is None and not exact_title:
        details["contradiction"] = "season"
        return details
    elif anilist_season and allmanga_season and anilist_season == allmanga_season:
        details["score"] += 12

    # Signal 4b: Part / Cour comparison
    anilist_part = _extract_item_part(anilist, anilist_titles)
    allmanga_part = _extract_item_part(allmanga, allmanga_titles)
    if anilist_part is not None and allmanga_part is not None and anilist_part != allmanga_part:
        details["contradiction"] = "part"
        return details
    elif (anilist_part is not None and anilist_part > 1 and allmanga_part is None) or (allmanga_part is not None and allmanga_part > 1 and anilist_part is None):
        details["contradiction"] = "part"
        return details
    elif anilist_part is not None and allmanga_part is not None and anilist_part == allmanga_part:
        details["score"] += 10

    # Signal 5: Media Type / Format (Object-first)
    anilist_type = str(anilist.get("format") or anilist.get("type") or "").upper().replace(" ", "_")
    allmanga_type = str(allmanga.get("format") or allmanga.get("type") or "").upper().replace(" ", "_")
    if anilist_type == "ANIME":
        anilist_type = str(anilist.get("format") or "").upper().replace(" ", "_")
    if allmanga_type == "ANIME":
        allmanga_type = str(allmanga.get("format") or "").upper().replace(" ", "_")

    ignored_types = {"", "UNKNOWN", "NONE", "ANIME"}
    if anilist_type not in ignored_types and allmanga_type not in ignored_types:
        compatible_types = {
            frozenset(("TV", "TV_SHORT")),
            frozenset(("SPECIAL", "TV_SPECIAL")),
            frozenset(("MOVIE", "ONA")),
        }
        if (
            anilist_type == allmanga_type
            or frozenset((anilist_type, allmanga_type)) in compatible_types
        ):
            details["score"] += 8
        else:
            details["contradiction"] = "type"
            return details

    # Signal 6: Episode Count (Object-first)
    anilist_episodes = _positive_int(anilist.get("episodeCount"))
    allmanga_episodes = _positive_int(allmanga.get("episodeCount")) or _positive_int((allmanga.get("availableEpisodes") or {}).get("sub"))
    if anilist_episodes and allmanga_episodes:
        if anilist_episodes == allmanga_episodes:
            details["score"] += 10
        elif (
            max(anilist_episodes, allmanga_episodes)
            <= 2 * min(anilist_episodes, allmanga_episodes)
            and abs(anilist_episodes - allmanga_episodes) <= 3
        ):
            details["score"] += 2
        elif abs(anilist_episodes - allmanga_episodes) > 3 and not (anilist.get("status") == "RELEASING" or allmanga.get("status") == "RELEASING"):
            details["contradiction"] = "episodes"
            return details

    # Signal 7: Quarter / Season (Object-first)
    al_s_obj = anilist.get("season")
    am_s_obj = allmanga.get("season")
    anilist_quarter = str(al_s_obj.get("quarter") if isinstance(al_s_obj, dict) else "").casefold()
    allmanga_quarter = str(am_s_obj.get("quarter") if isinstance(am_s_obj, dict) else "").casefold()
    if anilist_quarter and allmanga_quarter == anilist_quarter:
        details["score"] += 4
    return details


def score_match(anilist, allmanga):
    return match_score_details(anilist, allmanga)["score"]


def choose_confident_match(reference, candidates):
    ranked = []
    for candidate in candidates or []:
        details = match_score_details(reference, candidate)
        if details["contradiction"] or details["title_score"] < 15:
            continue
        ranked.append((details["score"], details["exact_title"], candidate))
    if not ranked:
        return None

    ranked.sort(key=lambda item: item[0], reverse=True)
    best_score, exact_title, best = ranked[0]
    minimum = 30 if exact_title else 42
    if best_score < minimum:
        return None
    if exact_title:
        return best
    if len(ranked) > 1 and best_score - ranked[1][0] < 8:
        return None
    return best


def best_provider_match(anilist_show, provider_shows):
    """Return only an unambiguous, title-supported provider match."""
    return choose_confident_match(anilist_show, provider_shows)


best_allanime_match = best_provider_match



def is_same_show(show1, show2, strict=False):
    """Robustly determine if two show dicts represent the same title."""
    if not show1 or not show2:
        return False

    id1 = str(show1.get("_id") or show1.get("id") or "")
    id2 = str(show2.get("_id") or show2.get("id") or "")
    if id1 and id1 == id2:
        return True

    p1 = str(show1.get("_provider") or show1.get("provider") or "").casefold()
    p2 = str(show2.get("_provider") or show2.get("provider") or "").casefold()
    if "torrent" in {p1, p2}:
        return False

    al1 = str(show1.get("aniListId") or show1.get("_anilist_id") or "")
    al2 = str(show2.get("aniListId") or show2.get("_anilist_id") or "")
    if al1 and al1 == al2:
        return True

    mal1 = str(show1.get("malId") or show1.get("myanimelist_id") or "")
    mal2 = str(show2.get("malId") or show2.get("myanimelist_id") or "")
    if mal1 and mal1 == mal2:
        return True

    # Definitive negative checks: If both have explicit IDs that differ, they are definitely NOT the same show
    if al1 and al2 and al1 != al2:
        return False
    if mal1 and mal2 and mal1 != mal2:
        return False

    if p1 and p2 and p1 == p2 and id1 and id2 and id1 != id2:
        return False

    if strict:
        return False

    # Multi-signal scoring with contradiction verification (score >= 60, or exact title match)
    details = match_score_details(show1, show2)
    if not details.get("contradiction"):
        if details.get("score", 0) >= 60:
            return True
        if details.get("exact_title"):
            return True

    return False



def extract_franchise_query(title: str) -> str:
    """Extract clean base franchise title suitable for broad provider search."""
    if not title:
        return ""
    from media_cli.brain import AnimeBrain
    try:
        bout = AnimeBrain.process(str(title).strip(), dict)
        franchise = bout.get("franchise")
        if franchise:
            clean = franchise.split(":")[0].split(" - ")[0].strip()
            clean = re.sub(r"^[-\s_:]+|[-\s_:]+$", "", clean).strip()
            if clean and len(clean) >= 3:
                return clean
    except Exception:
        pass

    t = str(title).strip()
    t = re.sub(r"(?i)\s*\((?:season|part|cour|tv|the)\b.*?\)", "", t)
    t = re.sub(r"(?i)\s*[:–-]\s*(?:season|part|cour|the final|s\d+)\b.*$", "", t)
    t = re.sub(r"(?i)\s+(?:\d+(?:st|nd|rd|th)?\s+season|season\s+\d+|part\s+\d+|cour\s+\d+|s\d+)\b.*$", "", t)
    t = re.sub(r"(?i)\s+(?:[ivx]+|\d+)\s*$", "", t)
    return t.strip()


def extract_matching_queries(anilist_show: dict) -> list[str]:
    """Extract prioritized list of query strings for cross-provider matching."""
    queries = []

    # 1. Franchise base queries (both Romaji and English)
    for key in ("name", "englishName"):
        val = str(anilist_show.get(key) or "").strip()
        franchise = extract_franchise_query(val)
        if franchise and franchise not in queries and len(franchise) >= 3:
            queries.append(franchise)

    # 2. Full primary and english names
    for key in ("name", "englishName"):
        val = str(anilist_show.get(key) or "").strip()
        if val and val not in queries:
            queries.append(val)

    # 3. Native title
    nat = str(anilist_show.get("nativeName") or "").strip()
    if nat and nat not in queries:
        queries.append(nat)

    # 4. Synonyms / AltNames
    for alt in (anilist_show.get("altNames") or anilist_show.get("synonyms") or []):
        alt_str = str(alt or "").strip()
        if not alt_str:
            continue
        alt_franchise = extract_franchise_query(alt_str)
        if alt_franchise and alt_franchise not in queries and len(alt_franchise) >= 3:
            queries.append(alt_franchise)
        if alt_str not in queries:
            queries.append(alt_str)

    return queries


def match_anilist_show_to_provider(anilist_show, ttype="sub", provider_id=None, status_cb=None):
    from ..core.enrichment import _merge_anilist_into_provider
    from ..core.storage import get_al_match, save_al_match
    from ..providers import (
        _DEFAULT_PROVIDER_ID,
        is_provider_active,
        provider_display_name,
        provider_key,
    )
    from ..services.catalog import get_show_by_id, search_anime


    def _notify(msg):
        if callable(status_cb):
            try:
                status_cb(msg)
            except Exception:
                pass

    al_id = str(anilist_show.get("_id") or anilist_show.get("id") or "")
    stored = get_al_match(al_id) if al_id else {}
    stored_pid = stored.get("_provider") or stored.get("provider")

    if stored_pid and not is_provider_active(stored_pid):
        stored_pid = None

    if not provider_id:
        provider_id = stored_pid or _DEFAULT_PROVIDER_ID

    provider_id = provider_key(provider_id)
    p_name = provider_display_name(provider_id)

    # Priority 1: Check saved 1:1 match for this provider
    if al_id and stored and stored.get("_id") and stored_pid == provider_id:
        _notify("Locating saved match…")
        direct = get_show_by_id(stored["_id"], provider_id=stored_pid)
        if direct:
            direct_name = str(direct.get("name") or "").strip()
            if direct_name and direct_name != al_id:
                _notify("Match found")
                direct["_match_source"] = stored.get("match_source") or "stored"
                return _merge_anilist_into_provider(direct, anilist_show)

    # Priority 2: Direct AniList ID lookup
    if al_id and provider_id in ("anikoto", "miruro"):
        _notify("Verifying ID…")
        try:
            direct = get_show_by_id(al_id, provider_id=provider_id)
            if direct:
                d_al = str(direct.get("aniListId") or "")
                d_id = str(direct.get("_id") or "")
                if (d_al and d_al == al_id) or (d_id and d_id == al_id):
                    _notify("Match found")
                    direct["_match_source"] = "id"
                    save_al_match(al_id, direct)
                    return _merge_anilist_into_provider(direct, anilist_show)
        except Exception:
            pass

    # Priority 3: Search queries on provider catalog
    queries = extract_matching_queries(anilist_show)
    seen_queries = set()
    p_shows = []
    query_count = 0
    for query in queries:
        q_lower = query.strip().lower()
        if not q_lower or q_lower in seen_queries:
            continue
        seen_queries.add(q_lower)
        query_count += 1

        if query_count == 1:
            _notify(f"Finding streams on {p_name}…")
        else:
            _notify("Trying alternate titles…")

        results = search_anime(query, ttype, provider_id=provider_id)
        if results:
            exact = next((p for p in results if al_id and str(p.get("aniListId") or "") == al_id), None)
            if exact:
                _notify("Match found")
                exact["_match_source"] = "id"
                save_al_match(al_id, exact)
                return _merge_anilist_into_provider(exact, anilist_show)
            p_shows.extend(results)

    # Priority 4: Fuzzy scored match
    if p_shows:
        _notify("Finding the best match…")
        seen_ids = set()
        unique_shows = []
        for show in p_shows:
            sid = show.get("_id")
            if sid and sid not in seen_ids:
                seen_ids.add(sid)
                unique_shows.append(show)

        matched = best_provider_match(anilist_show, unique_shows)
        if matched:
            _notify("Match found")
            matched["_match_source"] = "fuzzy"
            save_al_match(al_id, matched)
            return _merge_anilist_into_provider(matched, anilist_show)

    return None


def _find_fuzzy_anilist_candidate(provider_show, token):
    from ..core.anilist import fetch_anilist_media, search_anilist
    from ..services import normalize as anilist_normalize


    # Step 1: Exact provider aniListId
    provider_al_id = str(provider_show.get("aniListId") or "")
    if provider_al_id:
        try:
            media = fetch_anilist_media(token, provider_al_id)
            if media:
                norm = anilist_normalize.normalize_media(media)
                norm["_match_source"] = "id"
                return norm
        except Exception:
            pass

    # Step 2: Intelligent search across title variants
    queries = [
        provider_show.get("name"),
        provider_show.get("englishName"),
    ]
    seen = set()
    for query in queries:
        query = str(query or "").strip()
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        results = search_anilist(token, query)
        matched = choose_confident_match(provider_show, results)
        if matched:
            matched["_match_source"] = "title"
            return matched
    return None


def _run_anilist_match_search(flags, ui, provider_show, token):
    from ..core.enrichment import _merge_anilist_into_provider
    from ..core.storage import save_source_anilist_match
    from ..state.preferences import set_action_feedback
    from ..ui.display import with_loading
    from ..ui.modals import confirm_auto_anilist_match

    candidate = with_loading(
        "Finding AniList match…",
        _find_fuzzy_anilist_candidate,
        provider_show,
        token,
    )
    if candidate:
        choice = confirm_auto_anilist_match(flags, ui, provider_show, candidate)
        if choice == "link":
            save_source_anilist_match(provider_show, candidate)
            merged = _merge_anilist_into_provider(provider_show, candidate)
            src = candidate.get("_match_source")
            tag = "✔ Matched by ID" if src == "id" else "✔ Suggested match"
            set_action_feedback(merged, tag)
            return merged
        elif choice == "search":
            return _run_manual_anilist_match(flags, ui, provider_show, token)
        elif choice == "back":
            return None

    # Step 3: Directly enter manual search with info message in footer
    return _run_manual_anilist_match(flags, ui, provider_show, token, initial_error="No matching title found")


def _run_manual_anilist_match(flags, ui, provider_show, token, initial_error=""):
    from ..core.anilist import search_anilist
    from ..core.api import SearchFailure
    from ..core.enrichment import _merge_anilist_into_provider
    from ..core.storage import save_source_anilist_match
    from ..state.preferences import set_action_feedback
    from ..ui.display import with_loading
    from ..ui.help import picker_help, search_input_help
    from ..ui.info_panel import make_info_fn
    from ..ui.modals import (
        manual_anilist_input_header,
        search_cover_header,
        search_result_header,
    )
    from ..ui.panels import render_search_header
    from ..ui.picker import tui_pick

    source_title = provider_show.get("_allanime_name") or provider_show.get("name") or ""
    query = source_title
    search_error = initial_error
    while True:
        query = tui_pick(
            flags, ui, "Match AniList",
            [],
            header_fn=manual_anilist_input_header(provider_show, error=search_error),
            return_query_on_enter=True,
            initial_query=query,
            is_search=True,
            help_dict=search_input_help(),
        )
        if query == -2 or not query:
            return None
        query = str(query).strip()
        if not query:
            continue
        search_error = ""
        try:
            results = with_loading(
                "Searching AniList…",
                search_anilist,
                token,
                query,
                raise_errors=True
            )
        except SearchFailure as exc:
            search_error = str(exc)
            continue
        if not results:
            search_error = f'No results found for "{query}"'
            continue
        options = [show.get("name", "Unknown") for show in results]

        # Pre-compute headers for all shows (0-delay cursor navigation)
        _precomputed_headers: dict[int, str] = {}
        _get_error_fn = lambda: search_error
        for i, show in enumerate(results):
            _precomputed_headers[i] = render_search_header(
                "AniList",
                query,
                "sub",
                lambda: results,
                lambda: "",
                selected_idx=i,
                esc_action="cancel",
                get_error_fn=_get_error_fn,
                filter_query="",
                badges=None,
            )

        def _cached_header_fn(si):
            if ui.active_picker_query:
                return render_search_header(
                    "AniList",
                    query,
                    "sub",
                    lambda: results,
                    lambda: "",
                    selected_idx=si,
                    esc_action="cancel",
                    get_error_fn=_get_error_fn,
                    filter_query=ui.active_picker_query or "",
                    badges=None,
                )
            return _precomputed_headers.get(si, "")

        idx = tui_pick(
            flags, ui, "Match AniList",
            options,
            header_fn=_cached_header_fn,
            top_header_fn=search_cover_header(lambda: results),
            help_dict=picker_help("Link title", "Search again", "Cancel", info_label="View show info"),
            info_fn=make_info_fn(lambda: results, ui),
        )
        if idx == -2:
            return None
        if idx == -3:
            continue
        if 0 <= idx < len(results):
            chosen = results[idx]
            chosen["_match_source"] = "manual"
            save_source_anilist_match(provider_show, chosen)
            merged = _merge_anilist_into_provider(provider_show, chosen)
            set_action_feedback(merged, "✔ Linked title")
            return merged


def match_provider_show_to_anilist(flags, ui, provider_show, token, manual_on_fail=False):
    from ..core.anilist import fetch_anilist_media, search_anilist
    from ..core.enrichment import _merge_anilist_into_provider
    from ..core.storage import get_source_anilist_match, save_source_anilist_match
    from ..services import normalize as anilist_normalize
    from ..state.preferences import set_action_feedback

    show_id = str(provider_show.get("_id") or provider_show.get("id") or "")
    stored = get_source_anilist_match(show_id) if show_id else {}
    stored_id = str(stored.get("_id") or "")
    provider_al_id = str(provider_show.get("aniListId") or "")

    # Priority 1: Stored 1:1 match from local storage
    if stored_id:
        try:
            media = fetch_anilist_media(token, stored_id)
            if media:
                match_source = stored.get("match_source") or "stored"
                provider_show["_match_source"] = match_source
                normalized_media = anilist_normalize.normalize_media(media)
                save_source_anilist_match(provider_show, normalized_media)
                merged = _merge_anilist_into_provider(provider_show, normalized_media)
                set_action_feedback(merged, "✔ Found saved match")
                return merged
        except Exception:
            pass

    # Priority 2: Exact provider aniListId
    if provider_al_id:
        try:
            media = fetch_anilist_media(token, provider_al_id)
            if media:
                provider_show["_match_source"] = "id"
                normalized_media = anilist_normalize.normalize_media(media)
                save_source_anilist_match(provider_show, normalized_media)
                merged = _merge_anilist_into_provider(provider_show, normalized_media)
                set_action_feedback(merged, "✔ Matched by ID")
                return merged
        except Exception:
            pass

    # Priority 3: Title search & fuzzy match
    queries = [
        provider_show.get("name"),
        provider_show.get("englishName"),
    ]
    seen = set()
    for query in queries:
        query = str(query or "").strip()
        if not query or query.lower() in seen:
            continue
        seen.add(query.lower())
        results = search_anilist(token, query)
        matched = choose_confident_match(provider_show, results)
        if matched:
            provider_show["_match_source"] = "fuzzy"
            save_source_anilist_match(provider_show, matched)
            merged = _merge_anilist_into_provider(provider_show, matched)
            set_action_feedback(merged, "✔ Suggested match")
            return merged

    # Priority 4: Manual Picker
    if manual_on_fail:
        return _run_manual_anilist_match(flags, ui, provider_show, token)
    return None


def _run_manual_match_search(flags, ui, anilist_show, ttype, provider_id=None, allow_provider_change=False):
    from ..app.search_coordinator import make_provider_oneshot_search
    from ..core.enrichment import _merge_anilist_into_provider
    from ..core.storage import save_al_match
    from ..providers import provider_display_name, provider_key
    from ..ui.display import with_loading
    from ..ui.help import picker_help, search_input_help
    from ..ui.info_panel import make_info_fn
    from ..ui.modals import (
        confirm_auto_match,
        manual_match_input_header,
        no_match_prompt,
        search_cover_header,
        search_result_header,
        select_provider_for_match,
    )
    from ..ui.panels import render_search_header
    from ..ui.picker import tui_pick


    al_title = anilist_show.get("name") or anilist_show.get("englishName") or ""
    al_id = str(anilist_show.get("_id") or anilist_show.get("id") or "")
    current_pid = provider_key(provider_id or anilist_show.get("_provider") or getattr(ui, "ui_provider_ctx", None))
    target_pid = current_pid

    while True:
        if allow_provider_change:
            chosen_pid = select_provider_for_match(flags, ui, target_pid, anilist_show, ttype)
            if not chosen_pid:
                return None
            target_pid = chosen_pid
            p_name = provider_display_name(target_pid)

            # Auto-match on selected provider
            matched = with_loading(
                f"Matching title on {p_name}...",
                match_anilist_show_to_provider,
                anilist_show,
                ttype,
                provider_id=target_pid,
            )
            if matched:
                choice = confirm_auto_match(flags, ui, current_pid, target_pid, anilist_show, matched, ttype)
                if choice == "link":
                    matched["_match_source"] = matched.get("_match_source") or "auto"
                    if al_id:
                        save_al_match(al_id, matched)
                    return _merge_anilist_into_provider(matched, anilist_show)
                elif choice == "back":
                    continue
            else:
                choice = no_match_prompt(flags, ui, current_pid, target_pid, anilist_show, ttype)
                if choice == "back":
                    continue

        p_name = provider_display_name(target_pid)
        query = extract_franchise_query(al_title) or al_title
        search_error = ""

        while True:
            query = tui_pick(
                flags, ui, f"Match {p_name}",
                [],
                header_fn=manual_match_input_header(anilist_show, p_name=p_name, error=search_error),
                return_query_on_enter=True,
                initial_query=query,
                is_search=True,
                help_dict=search_input_help()
            )
            if query == -2 or not query:
                if allow_provider_change:
                    break  # Break out to provider selection loop
                return None
            query = str(query).strip()
            if not query:
                continue
            search_error = ""

            live_fn, get_results, get_loading, get_error = make_provider_oneshot_search(query, ttype, target_pid)
            initial_opts = [s.get("name", "Unknown") for s in get_results()]

            # Pre-compute headers for all shows (0-delay cursor navigation)
            _precomputed_headers: dict[int, str] = {}
            _get_error_fn = lambda: get_error()
            shows_list = get_results()
            for i, show in enumerate(shows_list):
                _precomputed_headers[i] = render_search_header(
                    p_name,
                    query,
                    ttype,
                    get_results,
                    get_loading,
                    selected_idx=i,
                    esc_action="cancel",
                    get_error_fn=_get_error_fn,
                    filter_query="",
                    badges=None,
                )

            def _cached_header_fn(si):
                if not ui.active_picker_query and si in _precomputed_headers:
                    return _precomputed_headers[si]
                return render_search_header(
                    p_name,
                    query,
                    ttype,
                    get_results,
                    get_loading,
                    selected_idx=si,
                    esc_action="cancel",
                    get_error_fn=_get_error_fn,
                    filter_query=ui.active_picker_query or "",
                    badges=None,
                )

            idx = tui_pick(
                flags, ui, f"Match {p_name}",
                initial_opts,
                header_fn=_cached_header_fn,
                top_header_fn=search_cover_header(get_results),
                live_fn=live_fn,
                help_dict=picker_help("Link title", "Search again", "Cancel", info_label="View show info"),
                info_fn=make_info_fn(get_results, ui),
            )

            if idx == -2:
                if allow_provider_change:
                    break
                return None
            if idx == -3:
                continue
            if idx == -4:
                search_error = get_error() or f'No results found for "{query}"'
                continue
            if idx >= 0:
                shows = get_results()
                if idx >= len(shows):
                    continue
                chosen = shows[idx]
                chosen["_match_source"] = "manual"
                if al_id:
                    save_al_match(al_id, chosen)
                return _merge_anilist_into_provider(chosen, anilist_show)


match_allanime_show_to_anilist = match_provider_show_to_anilist
match_anilist_show_to_allanime = match_anilist_show_to_provider


