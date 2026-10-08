"""Network connectivity utilities."""

import socket
import threading
import time

_lock = threading.Lock()
_last_check_time = 0.0
_last_status = True
_consecutive_failures = 0

# Fast public endpoints. Port 53 can be blocked on some networks, so include
# HTTPS ports before treating a connection as unavailable.
PROBE_TARGETS = [("1.1.1.1", 443), ("8.8.8.8", 443), ("1.1.1.1", 53), ("8.8.8.8", 53)]
OFFLINE_FAILURE_THRESHOLD = 2


def is_online(timeout: float = 1.5, cache_ttl: float = 15.0) -> bool:
    """Return True if internet connectivity is available.

    Probes standard public DNS endpoints with a short timeout.
    Results are cached for *cache_ttl* seconds to prevent UI rendering delays.
    """
    global _last_check_time, _last_status, _consecutive_failures
    now = time.time()
    with _lock:
        if (now - _last_check_time) < cache_ttl:
            return _last_status

    status = False
    for host, port in PROBE_TARGETS:
        try:
            s = socket.create_connection((host, port), timeout=timeout)
            s.close()
            status = True
            break
        except (OSError, socket.timeout):
            continue

    with _lock:
        _last_check_time = now
        _last_status = status
        if status:
            _consecutive_failures = 0
        else:
            _consecutive_failures += 1

    return status


def force_check_online(timeout: float = 1.5) -> bool:
    """Bypass cache and immediately probe internet connectivity."""
    return is_online(timeout=timeout, cache_ttl=0.0)


def is_confirmed_offline() -> bool:
    """Return True only after repeated failed probes.

    UI badges should be quieter than internal online checks because a single
    transient probe failure is common on VPNs, Wi-Fi wakeups, and restricted DNS.
    """
    with _lock:
        return (not _last_status) and _consecutive_failures >= OFFLINE_FAILURE_THRESHOLD
