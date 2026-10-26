"""Fixed-field transport decisions; never log cookies, identity or request paths."""

from __future__ import annotations

import logging

from .policy import BrowserPolicy, _origin

_logger = logging.getLogger(__name__)
_EVENTS = frozenset({"cache_hit", "cache_miss", "cache_save_attempt", "cache_saved", "cache_save_skipped",
                     "cache_invalidated", "normal_http", "replay_http", "replay_error", "browser_start",
                     "browser_reused", "cookie_export", "browser_retained", "browser_closed",
                     "http_client_created", "http_client_reused", "fresh_connection_retry"})
_REASONS = frozenset({"", "missing", "unsafe", "invalid_or_expired", "storage_unavailable", "cancelled",
                      "invalid_snapshot", "too_large", "verification_challenge", "no_clearance", "export_failed",
                      "http_error", "transport_error", "deadline", "cached_clearance_rejected",
                      "http_replay_succeeded", "native_session", "private_memory_only"})


def record(policy: BrowserPolicy, url: str, event: str, *, reason: str = "", status: int | None = None,
           cookies: int | None = None, partitioned: bool | None = None, private: bool | None = None) -> None:
    if not _logger.isEnabledFor(logging.DEBUG) or event not in _EVENTS or reason not in _REASONS:
        return
    try:
        policy.validate_url(url)
        origin = _origin(url)
    except (TypeError, ValueError):
        return
    _logger.debug("Browser access: provider=%s origin=%s event=%s reason=%s status=%s cookies=%s partitioned=%s private=%s",
                  policy.provider_id, origin, event, reason or "-",
                  status if type(status) is int and 100 <= status <= 599 else "-",
                  cookies if type(cookies) is int and 0 <= cookies <= 1024 else "-",
                  partitioned if type(partitioned) is bool else "-", private if type(private) is bool else "-")
