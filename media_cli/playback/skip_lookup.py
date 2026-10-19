"""Bind remote skips to a playback and its observed video duration."""

import logging
import math
from collections.abc import Iterable
from concurrent.futures import Future
from ..core.workers import DaemonExecutor

from ..media.aniskip import fetch_skip_times, validated_skip_intervals

log = logging.getLogger(__name__)
SkipInterval = dict[str, object]


class SkipLookup:
    """One worker, no queued lookups, and completions consumed by the player only."""

    def __init__(self) -> None:
        self._pool: DaemonExecutor | None = None
        self._future: Future[list[SkipInterval]] | None = None
        self._request: tuple[int, float] | None = None
        self._generation = 0
        self.reset(None, None, ())

    def reset(
        self,
        mal_id: int | None,
        episode: float | None,
        candidates: Iterable[SkipInterval] = (),
    ) -> None:
        self._generation += 1
        self._mal_id = mal_id
        self._episode = episode
        self._candidates = tuple(dict(item) for item in candidates)
        self._attempted: set[float] = set()
        self._active: tuple[SkipInterval, ...] = ()
        self._duration: float | None = None
        # A running request cannot be cancelled. Keep ownership until it finishes
        # so repeated navigation cannot create an unbounded queue of requests.
        if self._future is not None:
            self._future.cancel()

    def poll(self, duration: float) -> tuple[SkipInterval, ...]:
        """Return currently validated skips without waiting on network work."""
        try:
            duration = float(duration)
        except (TypeError, ValueError):
            duration = 0.0
        valid_duration = math.isfinite(duration) and duration > 0
        request_duration = float(round(duration)) if valid_duration else 0.0
        if self._future is not None and self._future.done():
            future, request = self._future, self._request
            self._future = self._request = None
            if not future.cancelled():
                try:
                    results = future.result()
                except Exception:
                    log.exception("AniSkip background lookup failed")
                else:
                    if request == (self._generation, request_duration):
                        compatible = validated_skip_intervals(self._candidates, duration)
                        replaced_types = {item["type"] for item in results}
                        self._candidates = tuple(dict(item) for item in results) + tuple(
                            item for item in compatible if item["type"] not in replaced_types
                        )
                        self._duration = None
                    else:
                        log.debug("AniSkip ignored stale lookup completion: %r", request)

        if duration != self._duration:
            self._active = tuple(validated_skip_intervals(self._candidates, duration))
            self._duration = duration
        if (
            valid_duration
            and (not self._active or len(self._active) < len(self._candidates))
            and self._mal_id
            and self._episode is not None
            and self._future is None
            and request_duration not in self._attempted
        ):
            self._attempted.add(request_duration)
            if self._pool is None:
                self._pool = DaemonExecutor(max_workers=1)
            self._request = (self._generation, request_duration)
            self._future = self._pool.submit(
                fetch_skip_times,
                self._mal_id,
                self._episode,
                episode_length=duration,
            )
        return self._active

    def close(self) -> None:
        self.reset(None, None)
        if self._pool is not None:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None
