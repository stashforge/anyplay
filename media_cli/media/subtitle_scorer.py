"""Subtitle quality evaluator and hallucination detector.

Calculates weighted heuristic metrics (repetition ratio, word density,
vocabulary diversity) to classify subtitle streams as VALID, SUSPICIOUS,
or JUNK (e.g. AI speech-to-text hallucination loops).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# Cache evaluated results by url/content hash
_SCORER_CACHE: dict[str, dict[str, Any]] = {}


def normalize_subtitle_text(text: str) -> str:
    """Strip styling, tags, punctuation, and excess whitespace."""
    if not text:
        return ""
    text = text.lower()
    text = re.sub(r"<[^>]+>", "", text)          # HTML / WebVTT tags
    text = re.sub(r"\{[^\}]*\}", "", text)        # ASS styling tags
    text = text.replace(r"\N", " ").replace(r"\n", " ")
    text = re.sub(r"[^\w\s]", "", text)          # Punctuation
    return re.sub(r"\s+", " ", text).strip()


def parse_subtitle_cues(sub_text: str) -> list[tuple[float, float, str]]:
    """Parse WebVTT, SRT, or ASS content into a list of (start_s, end_s, text)."""
    if not sub_text:
        return []

    cues: list[tuple[float, float, str]] = []

    # Detect ASS format
    if "[Events]" in sub_text or "[Script Info]" in sub_text:
        in_events = False
        for line in sub_text.splitlines():
            stripped = line.strip()
            if stripped.lower() == "[events]":
                in_events = True
                continue
            if in_events and stripped.startswith("[") and stripped.endswith("]"):
                in_events = False
                continue
            if in_events and stripped.startswith("Dialogue:"):
                parts = stripped[len("Dialogue:"):].split(",", 9)
                if len(parts) >= 10:
                    start_s = _parse_time_str(parts[1])
                    end_s = _parse_time_str(parts[2])
                    cue_text = parts[9].strip()
                    if cue_text:
                        cues.append((start_s, end_s, cue_text))
        return cues

    # WebVTT / SRT format
    lines = sub_text.splitlines()
    i = 0
    time_pat = re.compile(
        r"(?:(\d{1,2}):)?(\d{2}):(\d{2})[\.,](\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}):(\d{2})[\.,](\d{3})"
    )

    while i < len(lines):
        line = lines[i].strip()
        m = time_pat.search(line)
        if m:
            h1, m1, s1, ms1, h2, m2, s2, ms2 = m.groups()
            start_s = (int(h1) if h1 else 0) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000.0
            end_s = (int(h2) if h2 else 0) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000.0
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                next_line = lines[i].strip()
                if time_pat.search(next_line):
                    break
                text_lines.append(next_line)
                i += 1
            cue_text = " ".join(text_lines).strip()
            if cue_text:
                cues.append((start_s, end_s, cue_text))
            continue
        i += 1

    return cues


def _parse_time_str(t_str: str) -> float:
    parts = t_str.strip().split(":")
    if len(parts) == 3:
        try:
            h = int(parts[0])
            m = int(parts[1])
            s = float(parts[2])
            return h * 3600 + m * 60 + s
        except ValueError:
            return 0.0
    return 0.0


def score_subtitle(
    cues: list[tuple[float, float, str]],
    video_duration_s: float = 0.0,
) -> dict[str, Any]:
    """Evaluate subtitle quality and detect broken/hallucinated AI caption loops.

    Returns dict:
      label: 'VALID', 'SUSPICIOUS', or 'JUNK'
      score: float (0.0 clean, 1.0 definitely junk)
      repetition_ratio: float
      word_density: float (words per minute)
      vocab_ratio: float (unique words / total words)
      top_line: str
    """
    if not cues:
        return {
            "label": "JUNK",
            "score": 1.0,
            "reason": "empty",
            "repetition_ratio": 0.0,
            "word_density": 0.0,
            "vocab_ratio": 0.0,
            "top_line": "",
        }

    # Infer duration from last cue if video_duration_s is missing or 0
    last_cue_end = cues[-1][1] if cues else 0.0
    effective_duration = max(video_duration_s or 0.0, last_cue_end)
    duration_min = max(effective_duration / 60.0, 1.0)

    # Merge contiguous identical cues (e.g. frame-by-frame ASS typesetting/karaoke animations)
    merged_cues: list[tuple[float, float, str]] = []
    for c in cues:
        norm = normalize_subtitle_text(c[2])
        if not norm:
            continue
        if (
            merged_cues
            and normalize_subtitle_text(merged_cues[-1][2]) == norm
            and abs(c[0] - merged_cues[-1][1]) <= 0.25
        ):
            merged_cues[-1] = (merged_cues[-1][0], max(merged_cues[-1][1], c[1]), merged_cues[-1][2])
        else:
            merged_cues.append(c)

    valid_lines = [normalize_subtitle_text(c[2]) for c in merged_cues]
    valid_lines = [line for line in valid_lines if line]

    if not valid_lines:
        return {
            "label": "JUNK",
            "score": 1.0,
            "reason": "no_valid_text",
            "repetition_ratio": 0.0,
            "word_density": 0.0,
            "vocab_ratio": 0.0,
            "top_line": "",
        }

    words = [w for line in valid_lines for w in line.split()]
    total_words = len(words)
    cue_count = len(valid_lines)
    word_density = total_words / duration_min

    # Top repeated line
    counts = Counter(valid_lines)
    top_line, top_count = counts.most_common(1)[0]
    repetition_ratio = top_count / cue_count

    # Vocabulary diversity
    vocab_ratio = len(set(words)) / total_words if total_words else 0.0

    # Weighted scoring (0 = clean, 1 = definitely junk)
    score = 0.0

    # 1. Repetition penalty (primary signal, weighted at 0.55)
    if cue_count >= 8:
        score += min(repetition_ratio / 0.55, 1.0) * 0.55
    elif repetition_ratio > 0.75:
        score += 0.35

    # 2. Word density penalty (below 25 words per minute, weighted at 0.30)
    if word_density < 25.0:
        score += min(1.0 - (word_density / 25.0), 1.0) * 0.30

    # 3. Vocabulary diversity penalty (below 5% unique words, weighted at 0.15)
    if vocab_ratio < 0.05 and total_words > 50:
        score += min(1.0 - (vocab_ratio / 0.05), 1.0) * 0.15

    score = max(0.0, min(score, 1.0))

    if score >= 0.70:
        label = "JUNK"
    elif score >= 0.35:
        label = "SUSPICIOUS"
    else:
        label = "VALID"

    return {
        "label": label,
        "score": round(score, 3),
        "repetition_ratio": round(repetition_ratio, 3),
        "word_density": round(word_density, 1),
        "vocab_ratio": round(vocab_ratio, 3),
        "top_line": top_line,
    }


def evaluate_subtitle_content(
    content: str | bytes,
    video_duration_s: float = 0.0,
    cache_key: str | None = None,
) -> dict[str, Any]:
    """Parse and score raw subtitle content, with optional caching."""
    if cache_key and cache_key in _SCORER_CACHE:
        return _SCORER_CACHE[cache_key]

    if isinstance(content, bytes):
        if content.startswith(b"\x1f\x8b"):
            try:
                import gzip
                content = gzip.decompress(content)
            except Exception:
                pass
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content)

    cues = parse_subtitle_cues(text)
    res = score_subtitle(cues, video_duration_s=video_duration_s)
    if cache_key:
        _SCORER_CACHE[cache_key] = res
    return res


def filter_subtitles(
    subtitles: list[dict],
    headers: dict | None = None,
    referer: str | None = None,
    video_duration_s: float = 0.0,
    timeout: float = 3.0,
) -> list[dict]:
    """Filter out JUNK subtitles (e.g. AI hallucination loops) and demote SUSPICIOUS tracks."""
    import urllib.parse
    import urllib.request

    if not subtitles:
        return []

    filtered = []
    for sub in subtitles:
        url = sub.get("url") or sub.get("file")
        if not url:
            continue

        if url in _SCORER_CACHE:
            res = _SCORER_CACHE[url]
        elif (
            "auto" in url.lower()
            or "auto" in str(sub.get("label", "")).lower()
            or len(subtitles) <= 3
        ):
            try:
                actual_url = url
                req_hdrs = dict(headers or {})
                if referer:
                    req_hdrs["Referer"] = referer
                req = urllib.request.Request(actual_url, headers=req_hdrs)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = resp.read()

                # If m3u8 playlist, follow to first segment
                if ".m3u8" in actual_url.lower():
                    m3u8_text = data.decode("utf-8", errors="replace")
                    base = actual_url.rsplit("/", 1)[0] + "/"
                    for line in m3u8_text.splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            seg_url = urllib.parse.urljoin(base, line)
                            with urllib.request.urlopen(urllib.request.Request(seg_url, headers=req_hdrs), timeout=timeout) as s_resp:
                                data = s_resp.read()
                            break

                res = evaluate_subtitle_content(data, video_duration_s=video_duration_s, cache_key=url)
            except Exception:
                res = {"label": "VALID", "score": 0.0}
        else:
            res = {"label": "VALID", "score": 0.0}

        if res.get("label") == "JUNK":
            continue

        item = dict(sub)
        item["sub_score"] = res.get("score", 0.0)
        item["sub_label_status"] = res.get("label", "VALID")
        if res.get("label") == "SUSPICIOUS":
            item["default"] = False
        filtered.append(item)

    return filtered

