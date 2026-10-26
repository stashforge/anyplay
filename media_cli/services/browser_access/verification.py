"""Quiet-first verification transitions, independent of browser or TUI APIs."""

from __future__ import annotations

import math
from enum import Enum


class VerificationState(str, Enum):
    QUIET = "quiet"
    INTERACTION = "interaction"
    VERIFIED = "verified"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


def _finite_number(value: float) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


class VerificationGate:
    """QUIET requests a minimized headed window, not a headless browser.

    INTERACTION means the automatic grace period elapsed, not proof that a
    CAPTCHA requires a click. The owner performs window changes on transitions.
    """

    def __init__(self, started_at: float, *, quiet_seconds: float = 8, total_seconds: float = 90):
        if (not all(_finite_number(value) for value in (started_at, quiet_seconds, total_seconds))
                or total_seconds <= 0 or not 0 <= quiet_seconds <= total_seconds):
            raise ValueError("Invalid verification deadlines")
        self._last_time = started_at
        self._reveal_at = started_at + quiet_seconds
        self._deadline = started_at + total_seconds
        if not math.isfinite(self._reveal_at) or not math.isfinite(self._deadline):
            raise ValueError("Verification deadlines overflow")
        self.state = VerificationState.QUIET

    def observe(self, now: float, *, verified: bool = False, closed: bool = False,
                cancelled: bool = False) -> VerificationState:
        if self.state in {VerificationState.VERIFIED, VerificationState.CANCELLED, VerificationState.TIMED_OUT}:
            return self.state
        if not _finite_number(now) or now < self._last_time:
            raise ValueError("Verification observations require monotonic finite time")
        self._last_time = now
        if closed or cancelled:
            self.state = VerificationState.CANCELLED
        elif now >= self._deadline:
            self.state = VerificationState.TIMED_OUT
        elif verified:
            self.state = VerificationState.VERIFIED
        elif now >= self._reveal_at:
            self.state = VerificationState.INTERACTION
        return self.state
