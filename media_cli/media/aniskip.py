"""AniSkip API client for fetching anime Opening, Ending, and Recap skip intervals."""

import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

from ..core.reporting import debug_warn

log = logging.getLogger(__name__)
ANISKIP_API_URL = "https://api.aniskip.com/v2/skip-times"
_ANISKIP_CACHE: dict[str, list[dict]] = {}


def fetch_skip_times(
    mal_id: Optional[int],
    episode_number: float,
    *,
    skip_types: Optional[list[str]] = None,
    timeout: float = 3.0,
) -> list[dict]:
    """Fetch skip intervals for a given MAL anime ID and episode number.

    Returns a list of dicts with:
      - 'type': 'op' | 'ed' | 'recap' | 'mixed-op' | 'mixed-ed'
      - 'start': float (start time in seconds)
      - 'end': float (end time in seconds)
      - 'label': str (e.g. 'Opening', 'Ending', 'Recap')
    """
    if not mal_id:
        log.debug("[aniskip] Skipped: No MAL ID available for this title.")
        return []

    if episode_number is None:
        log.debug("[aniskip] Skipped: Episode number is missing.")
        return []

    try:
        mal_id_int = int(mal_id)
        ep_num = float(episode_number)
        if ep_num.is_integer():
            ep_num = int(ep_num)
    except (ValueError, TypeError) as exc:
        debug_warn("AniSkip invalid MAL ID or episode number", exc)
        return []

    cache_key = f"{mal_id_int}:{ep_num}"
    if cache_key in _ANISKIP_CACHE:
        return _ANISKIP_CACHE[cache_key]

    if not skip_types:
        skip_types = ["op", "ed", "recap", "mixed-op", "mixed-ed"]

    query_params = [("types", st) for st in skip_types]
    query_params.append(("episodeLength", "0"))
    url = f"{ANISKIP_API_URL}/{mal_id_int}/{ep_num}?{urllib.parse.urlencode(query_params)}"

    log.debug(f"[aniskip] Fetching skip times: MAL ID {mal_id_int}, EP {ep_num}...")

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "allmanga-cli/1.0", "Accept": "application/json"},
    )

    results = []
    should_cache = False
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("found") and data.get("results"):
                        for item in data["results"]:
                            interval = item.get("interval", {})
                            start = float(interval.get("startTime", 0))
                            end = float(interval.get("endTime", 0))
                            if end > start:
                                stype = item.get("skipType", "").lower()
                                if "op" in stype:
                                    label = "Opening"
                                elif "ed" in stype:
                                    label = "Ending"
                                elif "recap" in stype:
                                    label = "Recap"
                                else:
                                    label = stype.upper()

                                results.append({
                                    "type": stype,
                                    "start": start,
                                    "end": end,
                                    "label": label,
                                })
                        if results:
                            summary = ", ".join([f"{r['label']} ({r['start']:.1f}s - {r['end']:.1f}s)" for r in results])
                            log.debug(f"[aniskip] Found {len(results)} interval(s): {summary}")
                    else:
                        log.debug(f"[aniskip] No skip times recorded on AniSkip for MAL ID {mal_id_int} EP {ep_num}.")
                    should_cache = True
                    break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log.debug(f"[aniskip] No skip times recorded on AniSkip for MAL ID {mal_id_int} EP {ep_num} (HTTP 404).")
                should_cache = True
                break
            elif exc.code in (500, 502, 503, 504):
                log.debug(f"[aniskip] AniSkip HTTP {exc.code} on attempt {attempt + 1}/{max_attempts}.")
                if attempt < max_attempts - 1:
                    import time
                    time.sleep(0.3)
                    continue
            else:
                log.debug(f"[aniskip] AniSkip HTTP error {exc.code}: {exc.reason}")
                debug_warn("AniSkip HTTP error", exc)
                break
        except Exception as exc:
            log.debug(f"[aniskip] AniSkip request error on attempt {attempt + 1}/{max_attempts}: {exc}")
            if attempt < max_attempts - 1:
                import time
                time.sleep(0.3)
                continue
            break

    if should_cache or results:
        _ANISKIP_CACHE[cache_key] = results
    return results


def generate_chapters_file(skip_intervals: list[dict], filepath: str) -> str:
    """Generate a chapters file in FFMETADATA format for MPV progress marks.
    Always overwrites the file, writing empty metadata if no intervals exist.
    """
    if not filepath:
        return ""

    try:
        import os
        parent_dir = os.path.dirname(filepath)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
    except Exception:
        pass

    if not skip_intervals:
        try:
            import os
            if os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass
        return ""

    intervals = sorted(skip_intervals, key=lambda x: x["start"])
    points = []
    curr = 0.0
    for item in intervals:
        start = max(0.0, float(item["start"]))
        end = max(start, float(item["end"]))
        label = item.get("label", "Chapter")
        if start > curr:
            name = "Intro" if curr == 0.0 else "Episode"
            points.append((curr, start, name))
        points.append((start, end, label))
        curr = end

    points.append((curr, curr + 300.0, "Episode" if any("op" in item.get("type", "") for item in intervals) and curr < 1200 else "Outro"))

    lines = [";FFMETADATA1"]
    for start_s, end_s, title in points:
        start_ms = int(round(start_s * 1000))
        end_ms = int(round(end_s * 1000))
        if end_ms <= start_ms:
            continue
        lines.append("")
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={start_ms}")
        lines.append(f"END={end_ms}")
        lines.append(f"TITLE={title}")

    content = "\n".join(lines) + "\n"
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as exc:
        debug_warn("Failed to write chapters file", exc)
    return filepath
