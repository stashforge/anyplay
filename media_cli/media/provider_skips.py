"""Shared provider-first skip timing normalization and playback policy."""

import math

from .aniskip import validated_skip_intervals


def normalize_provider_skips(payload) -> list[dict]:
    if isinstance(payload, dict):
        rows = [dict(value, type=kind) for kind, value in payload.items()
                if kind in ("op", "ed") and isinstance(value, dict)]
    elif isinstance(payload, (list, tuple)):
        rows = payload
    else:
        return []
    intervals = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("type") not in ("op", "ed"):
            continue
        try:
            if isinstance(row["start"], bool) or isinstance(row["end"], bool):
                continue
            start, end = float(row["start"]), float(row["end"])
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            continue
        kind = row["type"]
        if kind in seen:
            continue
        seen.add(kind)
        intervals.append({"type": kind, "label": "Opening" if kind == "op" else "Ending",
                          "start": start, "end": end, "source": "provider"})
    return intervals


def playback_skip_intervals(intervals, duration: float) -> list[dict]:
    try:
        duration = float(duration)
    except (TypeError, ValueError, OverflowError):
        return []
    if not math.isfinite(duration) or duration <= 0:
        return []
    native = normalize_provider_skips([row for row in intervals if row.get("source") == "provider"])
    native = [row for row in native if row["end"] <= duration]
    remote = validated_skip_intervals([row for row in intervals if row.get("source") != "provider"], duration)
    return merge_skip_intervals(native, remote)


def skip_kind(interval: dict) -> str:
    return {"mixed-op": "op", "mixed-ed": "ed"}.get(interval.get("type"), interval.get("type", ""))


def merge_skip_intervals(preferred, fallback) -> list[dict]:
    kinds = {skip_kind(row) for row in preferred}
    return list(preferred) + [row for row in fallback if skip_kind(row) not in kinds]


def needs_skip_fallback(intervals) -> bool:
    return not {"op", "ed"}.issubset({skip_kind(row) for row in intervals})
