"""Lightweight entry-to-first-picker-frame diagnostics, without secrets."""

import time

_started: float | None = None
_phases: tuple[tuple[str, float], ...] = ()


def begin_startup() -> None:
    global _started, _phases
    _started = time.monotonic()
    _phases = ()


def startup_checkpoint(name: str) -> None:
    global _phases
    if _started is not None:
        _phases += ((name, time.monotonic()),)


def startup_first_frame() -> None:
    global _started, _phases
    if _started is None:
        return
    import logging

    startup_checkpoint("first_frame")
    previous = _started
    durations = []
    for name, timestamp in _phases:
        durations.append(f"{name}={(timestamp - previous) * 1000:.1f}ms")
        previous = timestamp
    total = (previous - _started) * 1000
    _started, _phases = None, ()
    logging.getLogger(__name__).debug("Startup first picker frame: total=%.1fms phases=%s", total, " ".join(durations))
