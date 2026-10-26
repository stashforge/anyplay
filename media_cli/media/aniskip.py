"""AniSkip API client for fetching anime Opening, Ending, and Recap skip intervals."""

import json
import logging
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from http.client import HTTPException

from ..core.reporting import debug_warn

log = logging.getLogger(__name__)
ANISKIP_API_URL = "https://api.aniskip.com/v2/skip-times"
_SKIP_LABELS = {"op": "Opening", "ed": "Ending", "recap": "Recap", "mixed-op": "Opening", "mixed-ed": "Ending"}
_ANISKIP_CACHE: dict[tuple[int, float, float, tuple[str, ...]], list[dict]] = {}


def _request_key(mal_id, episode_number, episode_length, skip_types):
    if not mal_id or episode_number is None:
        return None
    try:
        mal_id = int(mal_id)
        episode = float(episode_number)
        duration = float(episode_length)
        types = tuple(sorted(set(skip_types or _SKIP_LABELS)))
        if mal_id <= 0 or not math.isfinite(episode) or not math.isfinite(duration) or duration < 0:
            return None
        if any(stype not in _SKIP_LABELS for stype in types):
            return None
        return mal_id, episode, duration, types
    except (ValueError, TypeError, OverflowError):
        return None


def _parsed_interval(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    try:
        stype = item["type"]
        if any(isinstance(item[key], bool) for key in ("start", "end", "episode_length")):
            return None
        start = float(item["start"])
        end = float(item["end"])
        reference = float(item["episode_length"])
        if stype not in _SKIP_LABELS:
            return None
        if not all(math.isfinite(value) for value in (start, end, reference)):
            return None
        if start < 0 or end <= start or reference <= 0 or end > reference:
            return None
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    return dict(item, start=start, end=end, episode_length=reference)


def validated_skip_intervals(intervals: Iterable[dict], duration: float) -> list[dict]:
    """Filter AniSkip candidates against the actual runtime without adjusting timestamps.

    Custom skip intervals do not require AniSkip reference metadata and should not
    be passed through this helper.
    """
    try:
        duration = float(duration)
        if not math.isfinite(duration) or duration <= 0:
            return []
        candidates = iter(intervals)
    except (ValueError, TypeError, OverflowError):
        return []

    results = []
    for item in candidates:
        parsed = _parsed_interval(item)
        if parsed is None:
            log.debug(
                "[aniskip] Rejected malformed interval: reference duration %r, actual duration %.3fs.",
                item.get("episode_length") if isinstance(item, dict) else None,
                duration,
            )
            continue
        reference = parsed["episode_length"]
        if parsed["end"] > duration or abs(reference - duration) > 20.0:
            log.debug(
                "[aniskip] Rejected interval %.3fs - %.3fs: reference duration %.3fs, actual duration %.3fs.",
                parsed["start"],
                parsed["end"],
                reference,
                duration,
            )
            continue
        results.append(parsed)
    return results


def cached_skip_times(
    mal_id: int | None,
    episode_number: float,
    *,
    episode_length: float = 0.0,
    skip_types: list[str] | None = None,
) -> list[dict]:
    """Return copies of an exact cached request, or an empty list, without I/O."""
    key = _request_key(mal_id, episode_number, episode_length, skip_types)
    return [dict(item) for item in _ANISKIP_CACHE.get(key, [])] if key is not None else []


def fetch_skip_times(
    mal_id: int | None,
    episode_number: float,
    *,
    episode_length: float = 0.0,
    skip_types: list[str] | None = None,
    timeout: float = 3.0,
) -> list[dict]:
    """Fetch skip intervals for a given MAL anime ID and episode number.

    Returns a list of dicts with:
      - 'type': 'op' | 'ed' | 'recap' | 'mixed-op' | 'mixed-ed'
      - 'start': float (start time in seconds)
      - 'end': float (end time in seconds)
      - 'label': str (e.g. 'Opening', 'Ending', 'Recap')
      - 'episode_length': float (AniSkip's reference runtime in seconds)
      - 'skip_id': str | None (AniSkip's interval identifier, if provided)

    With unknown episode_length (0), candidates are retained for later validation.
    """
    if not mal_id:
        log.debug("[aniskip] Skipped: No MAL ID available for this title.")
        return []

    if episode_number is None:
        log.debug("[aniskip] Skipped: Episode number is missing.")
        return []

    cache_key = _request_key(mal_id, episode_number, episode_length, skip_types)
    if cache_key is None:
        log.debug("[aniskip] Skipped: Invalid MAL ID, episode number, duration, or skip types.")
        return []

    if cache_key in _ANISKIP_CACHE:
        return [dict(item) for item in _ANISKIP_CACHE[cache_key]]
    mal_id_int, ep_num, episode_length, requested_types = cache_key
    if ep_num.is_integer():
        ep_num = int(ep_num)

    query_params = [("types", st) for st in requested_types]
    query_params.append(("episodeLength", str(episode_length)))
    url = f"{ANISKIP_API_URL}/{mal_id_int}/{ep_num}?{urllib.parse.urlencode(query_params)}"

    log.debug(
        "[aniskip] Fetching skip times: MAL ID %s, EP %s, request runtime %.3fs.", mal_id_int, ep_num, episode_length
    )

    req = urllib.request.Request(
        url,
        headers={"User-Agent": "anyplay/0.8.8", "Accept": "application/json"},
    )

    results = []
    should_cache = False
    max_attempts = 3
    for attempt in range(max_attempts):
        request_started = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if not isinstance(data, dict) or not isinstance(data.get("found"), bool):
                        log.debug("[aniskip] Malformed AniSkip response.")
                        break
                    if data.get("found") and not isinstance(data.get("results"), list):
                        log.debug("[aniskip] Malformed AniSkip results.")
                        break
                    if data.get("found") and data.get("results"):
                        for item in data["results"]:
                            try:
                                interval = item["interval"]
                                stype = item["skipType"].lower()
                                parsed = _parsed_interval(
                                    {
                                        "type": stype,
                                        "start": interval["startTime"],
                                        "end": interval["endTime"],
                                        "label": _SKIP_LABELS.get(stype, ""),
                                        "episode_length": item["episodeLength"],
                                        "skip_id": item.get("skipId"),
                                    }
                                )
                            except (KeyError, ValueError, TypeError, AttributeError):
                                parsed = None
                            if parsed is None:
                                log.debug("[aniskip] Rejected malformed response interval: %r", item)
                            elif stype in requested_types:
                                results.append(parsed)
                        if episode_length > 0:
                            results = validated_skip_intervals(results, episode_length)
                        if results:
                            summary = ", ".join(
                                [f"{r['label']} ({r['start']:.1f}s - {r['end']:.1f}s)" for r in results]
                            )
                            log.debug(f"[aniskip] Found {len(results)} interval(s): {summary}")
                    else:
                        log.debug(f"[aniskip] No skip times recorded on AniSkip for MAL ID {mal_id_int} EP {ep_num}.")
                    should_cache = True
                    break
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                log.debug(
                    f"[aniskip] No skip times recorded on AniSkip for MAL ID {mal_id_int} EP {ep_num} (HTTP 404)."
                )
                break
            elif exc.code in (500, 502, 503, 504):
                log.debug(f"[aniskip] AniSkip HTTP {exc.code} on attempt {attempt + 1}/{max_attempts}.")
                if attempt < max_attempts - 1:
                    time.sleep(0.3)
                    continue
            else:
                log.debug(f"[aniskip] AniSkip HTTP error {exc.code}: {exc.reason}")
                break
        except (json.JSONDecodeError, UnicodeError) as exc:
            log.debug("[aniskip] Invalid AniSkip JSON response: %s", exc)
            break
        except (urllib.error.URLError, OSError, HTTPException) as exc:
            log.debug(f"[aniskip] AniSkip request error on attempt {attempt + 1}/{max_attempts}: {exc}")
            if attempt < max_attempts - 1:
                time.sleep(0.3)
                continue
            break
        finally:
            log.debug(
                "[aniskip] Request attempt %s/%s completed in %.3fs (request runtime %.3fs).",
                attempt + 1,
                max_attempts,
                time.monotonic() - request_started,
                episode_length,
            )

    if should_cache:
        _ANISKIP_CACHE[cache_key] = results
    return [dict(item) for item in results]


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
        if item.get("end") is None:
            continue
        end = max(start, float(item["end"]))
        label = item.get("label", "Chapter")
        if start > curr:
            name = "Intro" if curr == 0.0 and item.get("type") in ("op", "mixed-op") else "Episode"
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
