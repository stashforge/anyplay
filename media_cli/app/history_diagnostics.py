"""Session-local History timings, aggregated only when debug logging is enabled."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_log = logging.getLogger(__name__)


class HistoryDiagnostics:
    def __init__(self, *, logger: logging.Logger = _log,
                 clock: Callable[[], float] = time.perf_counter) -> None:
        self.enabled = logger.isEnabledFor(logging.DEBUG)
        self._logger, self._clock = logger, clock
        self._started: float | None = None
        self._counts: dict[str, int] = {}
        self._phases: dict[str, tuple[int, float, float]] = {}

    def _begin(self) -> float:
        now = self._clock()
        if self._started is None:
            self._started = now
        return now

    def count(self, name: str, amount: int = 1) -> None:
        if self.enabled:
            self._begin()
            self._counts[name] = self._counts.get(name, 0) + amount

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        if not self.enabled:
            yield
            return
        started = self._begin()
        try:
            yield
        finally:
            elapsed = max(0.0, self._clock() - started)
            count, total, maximum = self._phases.get(name, (0, 0.0, 0.0))
            self._phases[name] = count + 1, total + elapsed, max(maximum, elapsed)

    def report(self, *, force: bool = False) -> None:
        if not self.enabled or self._started is None:
            return
        elapsed = max(0.0, self._clock() - self._started)
        if not force and elapsed < 1.0:
            return
        fields = [f'{name}={count}' for name, count in sorted(self._counts.items())]
        for name, (count, total, maximum) in sorted(self._phases.items()):
            fields.extend((f'{name}_count={count}', f'{name}_total_ms={total * 1000:.3f}',
                           f'{name}_max_ms={maximum * 1000:.3f}'))
        self._logger.debug('History responsiveness: window=%.3fs %s', elapsed, ' '.join(fields))
        self._started = None
        self._counts.clear()
        self._phases.clear()
