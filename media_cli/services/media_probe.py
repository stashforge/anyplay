"""HTTP evidence shared by media probes; callers choose their retention policy."""

from enum import Enum


class MediaProbeOutcome(Enum):
    AVAILABLE = 'available'
    MISSING = 'missing'
    INCONCLUSIVE = 'inconclusive'


def classify_media_status(status: int) -> MediaProbeOutcome:
    if 200 <= status < 400:
        return MediaProbeOutcome.AVAILABLE
    if status in (404, 410):
        return MediaProbeOutcome.MISSING
    # Access restrictions, rate limits and server/network errors do not prove
    # that an already resolved resource has disappeared.
    return MediaProbeOutcome.INCONCLUSIVE
