"""Language normalization and track filtering for subtitles and audio."""

import re
from typing import Any

# ISO 639-1 (2-letter) to ISO 639-2 (3-letter) and aliases
_LANG_LOOKUP: dict[str, tuple[str, str, set[str]]] = {
    "en": ("en", "eng", {"en", "eng", "english", "en-us", "en-gb"}),
    "ja": ("ja", "jpn", {"ja", "jp", "jpn", "japanese", "japan", "nihongo"}),
    "es": ("es", "spa", {"es", "spa", "spanish", "espanol", "español", "es-la", "es-es", "castellano"}),
    "fr": ("fr", "fra", {"fr", "fra", "fre", "french", "francais", "français"}),
    "pt": ("pt", "por", {"pt", "por", "portuguese", "portugues", "português", "pt-br", "pt-pt"}),
    "de": ("de", "deu", {"de", "deu", "ger", "german", "deutsch"}),
    "zh": ("zh", "zho", {"zh", "zho", "chi", "chinese", "mandarin", "cantonese", "zh-cn", "zh-tw"}),
    "ko": ("ko", "kor", {"ko", "kor", "korean", "hangul"}),
    "it": ("it", "ita", {"it", "ita", "italian", "italiano"}),
    "ru": ("ru", "rus", {"ru", "rus", "russian", "russkiy"}),
    "ar": ("ar", "ara", {"ar", "ara", "arabic"}),
    "hi": ("hi", "hin", {"hi", "hin", "hindi"}),
    "id": ("id", "ind", {"id", "ind", "indonesian", "bahasa"}),
    "vi": ("vi", "vie", {"vi", "vie", "vietnamese"}),
    "th": ("th", "tha", {"th", "tha", "thai"}),
    "pl": ("pl", "pol", {"pl", "pol", "polish"}),
    "tr": ("tr", "tur", {"tr", "tur", "turkish"}),
    "nl": ("nl", "nld", {"nl", "nld", "dut", "dutch"}),
}

# Reverse lookup for fast resolution
_ALIAS_TO_CANONICAL: dict[str, tuple[str, str]] = {}
for code2, (c2, c3, aliases) in _LANG_LOOKUP.items():
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = (c2, c3)


def normalize_lang_code(val: str, target: str = "alpha2") -> str:
    """Normalize language code or label into canonical ISO code.

    target: 'alpha2' (e.g. 'en', 'ja') or 'alpha3' (e.g. 'eng', 'jpn').
    """
    if not val:
        return "und" if target == "alpha3" else "un"

    raw = str(val).strip().lower()
    # Strip common suffixes like [CC], (Full), (Original), etc.
    cleaned = re.sub(r'[\(\[\{].*?[\)\]\}]', '', raw).strip()
    words = re.findall(r'[a-zA-Z]+', cleaned)

    for word in words:
        if word in _ALIAS_TO_CANONICAL:
            c2, c3 = _ALIAS_TO_CANONICAL[word]
            return c3 if target == "alpha3" else c2

    # Check raw string directly
    clean_raw = "".join(c for c in raw if c.isalnum() or c == "-")
    if clean_raw in _ALIAS_TO_CANONICAL:
        c2, c3 = _ALIAS_TO_CANONICAL[clean_raw]
        return c3 if target == "alpha3" else c2

    # If unrecognized, return 2 or 3 letter prefix if plausible
    if len(clean_raw) in (2, 3):
        return clean_raw
    return clean_raw[:3] if target == "alpha3" else clean_raw[:2]


def parse_language_list(val: Any) -> list[str]:
    """Parse a config string, list, or keyword into a list of normalized 2-letter codes.

    Returns ['multi'] if all languages are accepted.
    """
    if val is None or val == "":
        return ["multi"]

    if isinstance(val, (list, tuple, set)):
        items = [str(x).strip() for x in val if str(x).strip()]
    elif isinstance(val, str):
        raw = val.strip().lower()
        if raw in ("multi", "all", "*", "auto"):
            return ["multi"]
        items = [p.strip().strip("'\"") for p in re.split(r"[,;|\s]+", val) if p.strip()]
    else:
        items = [str(val).strip()]

    if not items or any(x.lower() in ("multi", "all", "*", "auto") for x in items):
        return ["multi"]

    normalized = []
    seen = set()
    for item in items:
        code = normalize_lang_code(item, target="alpha2")
        if code and code not in seen:
            seen.add(code)
            normalized.append(code)

    return normalized or ["multi"]


def match_track_language(track: dict, requested_langs: set[str]) -> bool:
    """Check if a subtitle or audio track matches any of the requested normalized codes."""
    if "multi" in requested_langs or "all" in requested_langs:
        return True

    track_lang = track.get("language") or track.get("lang") or ""
    track_label = track.get("label") or track.get("name") or ""

    # 1. Match track's language code directly
    if track_lang:
        c2 = normalize_lang_code(track_lang, target="alpha2")
        if c2 in requested_langs:
            return True

    # 2. Match track label/title
    if track_label:
        c2 = normalize_lang_code(track_label, target="alpha2")
        if c2 in requested_langs:
            return True

        # Check word-boundary aliases in label
        label_lower = track_label.lower()
        for req in requested_langs:
            # Check if canonical name or aliases appear in label
            lookup = _LANG_LOOKUP.get(req)
            if lookup:
                _, _, aliases = lookup
                for alias in aliases:
                    if re.search(r'\b' + re.escape(alias) + r'\b', label_lower):
                        return True

    return False


def filter_tracks_by_language(
    tracks: list[dict],
    requested: Any,
    is_audio: bool = False,
) -> list[dict]:
    """Filter subtitle or audio tracks according to requested language list.

    For audio: if no tracks match the requested language, falls back to the
    default / primary track to ensure video is never rendered without audio.
    """
    if not tracks:
        return []

    req_list = parse_language_list(requested)
    if "multi" in req_list:
        return list(tracks)

    req_set = set(req_list)
    matched = [t for t in tracks if match_track_language(t, req_set)]

    if matched:
        return matched

    # If is_audio and nothing matched, fall back to default or primary track
    if is_audio and tracks:
        default_track = next((t for t in tracks if t.get("default")), tracks[0])
        return [default_track]

    return []
