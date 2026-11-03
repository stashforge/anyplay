"""Ready-data animation version grouping and existing quality preference policy."""

from ..media.sources import stream_quality_preference_key


def version_number(row):
    try:
        return max(1, int(row["raw"]["entry"].get("version") or 1))
    except (TypeError, ValueError):
        return 1


def best_variant(variants, quality, version=None):
    if version is None:
        version = min(version_number(row) for row in variants)
    return min((row for row in variants if version_number(row) == version),
               key=lambda row: stream_quality_preference_key(row["stream"], quality))
