"""Network connectivity utilities."""

import socket
import threading
import time

_lock = threading.Lock()
_last_check_time = 0.0
_last_status = True

# Fast public DNS resolvers
PROBE_TARGETS = [("1.1.1.1", 53), ("8.8.8.8", 53)]


def is_online(timeout: float = 0.5, cache_ttl: float = 15.0) -> bool:
    """Return True if internet connectivity is available.

    Probes standard public DNS endpoints with a short timeout.
    Results are cached for *cache_ttl* seconds to prevent UI rendering delays.
    """
    global _last_check_time, _last_status
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

    return status


def force_check_online(timeout: float = 0.5) -> bool:
    """Bypass cache and immediately probe internet connectivity."""
    return is_online(timeout=timeout, cache_ttl=0.0)
