"""Episode catalog normalization and index helpers."""

import decimal
import re
from typing import Any


def normalize_episode_ids(values):
    episode_ids = []
    seen = set()
    for value in values or []:
        episode_id = str(value).strip()
        if not episode_id or episode_id in seen:
            continue
        episode_ids.append(episode_id)
        seen.add(episode_id)
    return episode_ids


def is_contiguous_legacy_catalog(episode_ids):
    return episode_ids == [
        str(index) for index in range(1, len(episode_ids) + 1)
    ]


def parse_episode_dual_numbers(raw: str) -> tuple[str, str | None]:
    """Parse primary episode number and optional bracket/franchise number (e.g. '1 [77]' -> ('1', '77'))."""
    s = str(raw or "").strip()
    if not s:
        return "", None
    m_bracket = re.search(r"\[\s*([0-9]+(?:\.[0-9]+)?)\s*\]|\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)", s)
    secondary = None
    if m_bracket:
        bracket_val = m_bracket.group(1) or m_bracket.group(2)
        if bracket_val:
            secondary = bracket_val.lstrip("0") or "0"
    clean_s = re.sub(r"\[.*?\]|\(.*?\)", "", s).strip()
    cleaned = clean_episode_identifier(clean_s)
    if cleaned and cleaned.replace(".", "", 1).isdigit():
        primary = cleaned
    else:
        m_prim = re.search(r"(?:^|[-_.\s]|(?:ep|episode|part)\s*)([0-9]+(?:\.[0-9]+)?)(?:[-_.\s]|$)", clean_s, re.I)
        primary = ""
        if m_prim:
            primary = m_prim.group(1).lstrip("0") or "0"
    return primary, secondary


def episode_index_for_id(episode_ids, episode_id, labels=None):
    if not episode_ids or episode_id is None:
        return None
    episode_string = str(episode_id).strip()
    if not episode_string:
        return None
    if episode_string in episode_ids:
        return episode_ids.index(episode_string)

    target_prim, target_sec = parse_episode_dual_numbers(episode_string)
    target_clean = clean_episode_identifier(episode_string)
    target_num = target_prim or target_clean

    exact_matches = []
    secondary_matches = []

    for index, candidate in enumerate(episode_ids):
        candidate_str = str(candidate).strip()
        lbl = str(labels.get(candidate, "")) if labels else ""
        if not lbl:
            lbl = str(labels.get(candidate_str, "")) if labels else ""

        # Exact string/label equality
        if lbl and lbl.strip().lower() == episode_string.lower():
            return index

        prim, sec = parse_episode_dual_numbers(lbl or candidate_str)
        clean_cand = clean_episode_identifier(lbl or candidate_str)

        if target_num and (prim == target_num or clean_cand == target_num):
            exact_matches.append(index)
        elif sec and (sec == target_num or (target_sec and sec == target_sec)):
            secondary_matches.append(index)

    if len(exact_matches) == 1:
        return exact_matches[0]
    if not exact_matches and len(secondary_matches) == 1:
        return secondary_matches[0]

    # If ambiguous (multiple matches), return None so interactive picker can handle it
    return None


def episode_id_at(episode_ids, index):
    if not episode_ids or index is None:
        return None
    bounded_index = max(0, min(int(index), len(episode_ids) - 1))
    return episode_ids[bounded_index]


def clean_episode_identifier(raw: str) -> str:
    """Extract a clean episode number, special type, or short label from an identifier, URL, path, or slug."""
    s = str(raw or "").strip()
    if not s:
        return ""

    # 1. Clean number or standard short label like "Episode 1", "EP 1", "1"
    m_std = re.match(r"^(?:(?:ep|episode|part)\s*)?([0-9]+(?:\.[0-9]+)?)$", s, re.I)
    if m_std:
        return m_std.group(1).lstrip("0") or "0"

    m_spec_std = re.match(r"^(?:(ova|movie|special)\s*)([0-9]+(?:\.[0-9]+)?)?$", s, re.I)
    if m_spec_std:
        kind = m_spec_std.group(1).upper()
        num = m_spec_std.group(2)
        return f"{kind} {num}".strip() if num else kind

    # 2. Query parameter like ?ep=5, ?episode=5, ?num=5, ?id=5
    m_query = re.search(r"[?&](?:ep|episode|num|ep_num|episode_num|id)=([0-9]+(?:\.[0-9]+)?)(?:&|$)", s, re.I)
    if m_query:
        return m_query.group(1).lstrip("0") or "0"

    # Strip query parameters, html/php/asp extensions, and trailing slashes
    path = s.split("?")[0].rstrip("/")
    if path.endswith((".html", ".htm", ".php", ".asp", ".aspx")):
        path = path.rsplit(".", 1)[0]

    # Extract slug from path
    slug = path.split("/")[-1] if "/" in path else path

    # 3. Explicit episode patterns like episode-5, ep-5, ep_5, ep.5, ep05
    m = re.search(r"(?:^|[-_.])(?:episode|ep)[-_. ]?([0-9]+(?:\.[0-9]+)?)(?:[-_. ]|$)", slug, re.I)
    if m:
        return m.group(1).lstrip("0") or "0"

    # 4. Special types (OVA / Movie / Special)
    m_spec = re.search(r"(?:^|[-_.])(ova|movie|special)[-_. ]?([0-9]+(?:\.[0-9]+)?)?", slug, re.I)
    if m_spec:
        kind = m_spec.group(1).upper()
        num = m_spec.group(2)
        return f"{kind} {num}".strip() if num else kind

    # 5. Trailing numbers like -05, -5-sub, _05_dub, etc.
    m_num = re.search(r"[-_.]([0-9]+(?:\.[0-9]+)?)(?:[-_.](?:sub|dub|raw|eng|v\d+|end))?$", slug, re.I)
    if m_num:
        return m_num.group(1).lstrip("0") or "0"

    return slug


def episode_label(episode_id, labels=None):
    label = ""
    if labels:
        label = str(labels.get(str(episode_id)) or "").strip()
    if not label:
        label = clean_episode_identifier(str(episode_id or ""))
    label = label or str(episode_id or "")
    lowered = label.casefold()
    if lowered.startswith(("episode ", "ep ", "part ", "ova", "movie", "special")):
        return label
    return f"Episode {label}"


def episode_progress_number(episode_id, fallback=0):
    if not episode_id:
        return fallback
    clean = clean_episode_identifier(str(episode_id))
    try:
        value = decimal.Decimal(clean)
        return max(0, int(value))
    except (decimal.InvalidOperation, ValueError):
        pass
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", str(episode_id))
    if m:
        try:
            return max(0, int(decimal.Decimal(m.group(1))))
        except (decimal.InvalidOperation, ValueError):
            pass
    if fallback:
        try:
            return max(0, int(fallback))
        except (TypeError, ValueError):
            pass
    return fallback

def highest_episode_number(episode_ids, labels=None):
    if not episode_ids:
        return 0
    # Fast path: check last element first (ordered episode list)
    last_eid = episode_ids[-1]
    lbl = str(labels.get(last_eid, "")) if labels else ""
    if not lbl and labels:
        lbl = str(labels.get(str(last_eid), ""))
    clean_num = clean_episode_identifier(lbl or str(last_eid))
    try:
        val = decimal.Decimal(clean_num)
        if val % 1 == 0:
            return int(val)
        return str(val)
    except decimal.InvalidOperation:
        pass

    max_val = None
    has_numeric = False
    for eid in episode_ids:
        lbl = str(labels.get(eid, "")) if labels else ""
        if not lbl and labels:
            lbl = str(labels.get(str(eid), ""))
        clean_num = clean_episode_identifier(lbl or str(eid))
        try:
            val = decimal.Decimal(clean_num)
            if max_val is None or val > max_val:
                max_val = val
            has_numeric = True
        except decimal.InvalidOperation:
            continue
    if not has_numeric:
        return len(episode_ids)
    if max_val % 1 == 0:
        return int(max_val)
    return str(max_val)

def detect_next_episode_gap(current_ep, next_ep):
    if current_ep is None or next_ep is None:
        return False, ""
    try:
        curr = decimal.Decimal(str(current_ep))
        nxt = decimal.Decimal(str(next_ep))
    except decimal.InvalidOperation:
        return False, ""

    if curr % 1 != 0 or nxt % 1 != 0:
        return False, ""

    curr_int = int(curr)
    nxt_int = int(nxt)

    if nxt_int <= curr_int + 1:
        return False, ""

    start_missing = curr_int + 1
    end_missing = nxt_int - 1

    if start_missing == end_missing:
        return True, f"missing {start_missing}"
    return True, f"missing {start_missing}-{end_missing}"

def parse_episode_label(label):
    import decimal
    import math
    try:
        val = decimal.Decimal(str(label))
        if not val.is_finite():
            raise decimal.InvalidOperation
        is_int = (val % 1 == 0)
        return {
            "original": str(label),
            "numeric": val,
            "is_integer_like": is_int,
            "floor": int(math.floor(val)),
            "ceil": int(math.ceil(val))
        }
    except (decimal.InvalidOperation, TypeError, ValueError):
        return {
            "original": str(label),
            "numeric": None,
            "is_integer_like": False,
            "floor": None,
            "ceil": None
        }

def anilist_progress_target_for_episode(label, fallback=0):
    from decimal import ROUND_FLOOR, Decimal, InvalidOperation
    try:
        value = Decimal(str(label))
    except (InvalidOperation, TypeError, ValueError):
        return fallback

    if not value.is_finite():
        return fallback

    return int(value.to_integral_value(rounding=ROUND_FLOOR))


def build_progress_entries(show: dict, episode_ids: list[str] | None = None, labels: dict | None = None, ttype: str = "sub") -> list[dict]:
    """Build a list of explicit episode progress dictionaries with canonical numbers, labels, provider IDs, and AniList counts."""
    show = show or {}
    episode_ids = episode_ids or show.get("_episode_ids") or []
    labels = labels or show.get("_episode_labels") or {}

    al_id = show.get("aniListId")
    al_total = show.get("episodeCount")

    entries = [{
        "id": "0",
        "ep_number": 0,
        "label": "0 / Unwatched",
        "provider_id": "0",
        "anilist_progress": 0,
        "is_available": True,
        "index": -1,
    }]

    if not episode_ids:
        total = int(al_total or show.get("availableEpisodes", {}).get(ttype, 0) or 0)
        for num in range(1, total + 1):
            entries.append({
                "id": str(num),
                "ep_number": num,
                "label": f"Episode {num}",
                "provider_id": str(num),
                "anilist_progress": num,
                "is_available": True,
                "index": num - 1,
            })
        return entries

    first_num = None
    if episode_ids:
        first_lbl = str(labels.get(episode_ids[0], labels.get(str(episode_ids[0]), episode_ids[0])))
        prim, sec = parse_episode_dual_numbers(first_lbl)
        try:
            first_num = int(prim or clean_episode_identifier(first_lbl))
        except (ValueError, TypeError):
            first_num = 1

    has_offset = bool(al_id and first_num and first_num > 1)

    for idx, ep_id in enumerate(episode_ids):
        ep_id_str = str(ep_id)
        raw_lbl = str(labels.get(ep_id, labels.get(ep_id_str, ep_id_str)))
        prim, sec = parse_episode_dual_numbers(raw_lbl)
        clean = clean_episode_identifier(raw_lbl)

        # Dual label logic
        if has_offset and not sec:
            rel_num = idx + 1
            abs_num = first_num + idx
            display_lbl = f"Episode {rel_num} [{abs_num}]"
            al_num = rel_num
            local_num = abs_num
        elif sec: # e.g. Donghua with '1 [77]'
            display_lbl = raw_lbl if raw_lbl.lower().startswith("episode") else f"Episode {raw_lbl}"
            al_num = int(prim) if prim and prim.isdigit() else idx + 1
            try:
                local_num = int(sec)
            except (ValueError, TypeError):
                local_num = sec
        else:
            display_lbl = raw_lbl if raw_lbl.lower().startswith("episode") else f"Episode {raw_lbl}"
            try:
                local_num = int(prim) if prim and prim.isdigit() else (int(clean) if clean and clean.isdigit() else idx + 1)
            except (ValueError, TypeError):
                local_num = prim or clean or str(idx + 1)
            al_num = local_num if isinstance(local_num, int) else idx + 1

        entries.append({
            "id": ep_id_str,
            "ep_number": local_num,
            "label": display_lbl,
            "provider_id": ep_id_str,
            "anilist_progress": al_num,
            "is_available": True,
            "index": idx,
        })
    return entries


def resolve_dual_episode_label(anime, ep_val) -> str:
    """Resolve episode value to dual formatted string (e.g. '15 [87]') if season offset exists."""
    if ep_val is None or str(ep_val).strip() == "" or str(ep_val) == "0":
        return ""
    s_val = str(ep_val).strip()
    prim, sec = parse_episode_dual_numbers(s_val)
    if sec:
        return f"{prim} [{sec}]"

    anime = anime or {}
    al_id = anime.get("aniListId")
    eids = anime.get("_episode_ids") or []
    labels = anime.get("_episode_labels") or {}

    if al_id and eids:
        first_lbl = str(labels.get(eids[0], labels.get(str(eids[0]), eids[0])))
        p_f, s_f = parse_episode_dual_numbers(first_lbl)
        try:
            first_num = int(p_f or clean_episode_identifier(first_lbl))
        except (ValueError, TypeError):
            first_num = 1

        if first_num > 1:
            try:
                cur_num = int(prim or clean_episode_identifier(s_val))
                if cur_num >= first_num:
                    rel_num = cur_num - first_num + 1
                    return f"{rel_num} [{cur_num}]"
            except (ValueError, TypeError):
                pass

    return prim or clean_episode_identifier(s_val) or s_val


def extract_episode_tags(raw: str) -> tuple[str, list[str]]:
    """Extract quality/audio tags like [4K], [1080p], [Dub] and return (cleaned_text, tags)."""
    s = str(raw or "").strip()
    if not s:
        return "", []
    tags = []
    tag_pattern = re.compile(
        r"\[\s*([0-9]{3,4}p|[248]k|sub|dub|raw|multi(?:-sub)?|softsub|hardsub|end)\s*\]|"
        r"\(\s*([0-9]{3,4}p|[248]k|sub|dub|raw|multi(?:-sub)?|softsub|hardsub|end)\s*\)",
        re.I
    )
    def _collect(m):
        val = m.group(1) or m.group(2)
        if val:
            tags.append(val.strip().upper())
        return ""
    clean = tag_pattern.sub(_collect, s)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean, tags


def collapse_duplicate_episode_label(raw: str) -> str:
    """Collapse labels like ``Episode 1. Episode 1`` without removing real titles."""
    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.match(
        r"^(?P<label>(?:episode|ep|part)\s+[0-9]+(?:\.[0-9]+)?)\s*(?:[.\-:|/]+|\s+)\s*(?P=label)$",
        s,
        re.I,
    )
    if m:
        label = m.group("label")
        if label.lower().startswith("ep "):
            return "Episode " + label[3:].strip()
        return label[0].upper() + label[1:]
    return s


def create_episode_entry(
    item: Any,
    *,
    episode_id: str | None = None,
    label: str | None = None,
    url: str | None = None,
    title: str | None = None,
    translation_type: str = "sub",
    season: int = 1,
    index: int | None = None,
) -> dict[str, Any]:
    """Create a unified, rich episode dictionary from raw provider data."""
    if isinstance(item, dict):
        raw_id = str(episode_id or item.get("id") or item.get("episode") or item.get("url") or "").strip()
        raw_label = str(label or item.get("label") or item.get("title") or raw_id).strip()
        raw_url = str(url or item.get("url") or ("" if not raw_id.startswith(("http://", "https://")) else raw_id)).strip() or None
        raw_title = str(title or item.get("title") or "").strip()
        ttype = str(translation_type or item.get("translationType") or "sub").strip().lower()
        season_val = int(item.get("season") or season or 1)
    else:
        raw_id = str(episode_id or item or "").strip()
        raw_label = str(label or raw_id).strip()
        raw_url = str(url or ("" if not raw_id.startswith(("http://", "https://")) else raw_id)).strip() or None
        raw_title = str(title or "").strip()
        ttype = str(translation_type or "sub").strip().lower()
        season_val = int(season or 1)

    # 1. Extract quality/audio tags from label and title
    clean_label, label_tags = extract_episode_tags(raw_label)
    clean_label = collapse_duplicate_episode_label(clean_label)
    clean_title, title_tags = extract_episode_tags(raw_title)
    clean_title = collapse_duplicate_episode_label(clean_title)
    all_tags = []
    for t in label_tags + title_tags:
        if t not in all_tags:
            all_tags.append(t)

    # 2. Parse dual/bracket numbers (e.g. "92 [156]")
    prim, sec = parse_episode_dual_numbers(clean_label)
    if not prim and clean_title:
        prim, t_sec = parse_episode_dual_numbers(clean_title)
        if not sec:
            sec = t_sec

    # 3. Fallback to clean_episode_identifier on ID/URL if no number in label
    if not prim and raw_id:
        prim = clean_episode_identifier(raw_id)
        if not prim and clean_label:
            prim = clean_episode_identifier(clean_label)

    # 4. Canonical numeric values
    num_val = 0.0
    disp_num = ""
    if prim:
        try:
            val = decimal.Decimal(prim)
            num_val = float(val)
            disp_num = str(int(val)) if (val % 1 == 0) else str(val)
        except (decimal.InvalidOperation, ValueError):
            disp_num = prim
            if index is not None:
                num_val = float(index + 1)
    elif index is not None:
        num_val = float(index + 1)
        disp_num = str(index + 1)

    # 5. Format display label
    if clean_label and (clean_label.lower().startswith(("episode ", "ep ", "part ", "ova", "movie", "special")) or re.search(r"[a-zA-Z]", clean_label)):
        final_label = clean_label
    elif disp_num and disp_num.isdigit():
        final_label = f"Episode {disp_num}"
    elif disp_num:
        final_label = f"Episode {disp_num}" if not disp_num.lower().startswith(("ova", "movie", "special")) else disp_num
    else:
        final_label = clean_label or raw_id

    # Append franchise number if present
    if sec and f"[{sec}]" not in final_label:
        final_label = f"{final_label} [{sec}]"

    # Append tags if not already present in final_label
    for tag in all_tags:
        if f"[{tag}]" not in final_label:
            final_label = f"{final_label} [{tag}]"

    return {
        "id": raw_id,
        "url": raw_url,
        "number": num_val,
        "display_number": disp_num or "0",
        "franchise_number": sec,
        "season": season_val,
        "label": final_label.strip(),
        "title": clean_title,
        "tags": all_tags,
        "translation_type": ttype,
    }


def build_episode_map(
    raw_episodes: list[Any] | None,
    translation_type: str = "sub",
    season: int = 1,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Deduplicate, numerically sort, and build an ordered Episode Map and secondary by_number index."""
    if not raw_episodes:
        return {}, {}

    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_numbers: set[tuple[int, float]] = set()

    for idx, item in enumerate(raw_episodes):
        entry = create_episode_entry(
            item,
            translation_type=translation_type,
            season=season,
            index=idx,
        )
        eid = entry["id"]
        if not eid or eid in seen_ids:
            continue

        num_tuple = (entry["season"], entry["number"])
        # If a valid nonzero episode number was already seen for this season, it is a duplicate
        if entry["number"] > 0 and num_tuple in seen_numbers:
            continue

        seen_ids.add(eid)
        if entry["number"] > 0:
            seen_numbers.add(num_tuple)
        entries.append(entry)

    # Sort strictly by (season, number, display_number) ascending
    def _sort_key(ep):
        return (ep.get("season", 1), ep.get("number", 0.0), ep.get("display_number", ""))

    entries.sort(key=_sort_key)

    episodes_map: dict[str, dict[str, Any]] = {}
    by_number: dict[str, str] = {}

    for ep in entries:
        eid = ep["id"]
        episodes_map[eid] = ep

        disp = ep.get("display_number")
        if disp and disp not in by_number:
            by_number[disp] = eid

        f_num = ep.get("franchise_number")
        if f_num and f_num not in by_number:
            by_number[f_num] = eid

    return episodes_map, by_number
