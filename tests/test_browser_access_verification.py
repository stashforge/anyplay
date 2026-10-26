"""Quiet-first verification is a bounded state machine, not a CAPTCHA detector."""

import pytest

from media_cli.services.browser_access.verification import VerificationGate
from media_cli.services.browser_access.verification import VerificationState as State


def test_automatic_success_never_requests_a_window():
    gate = VerificationGate(100, quiet_seconds=8, total_seconds=90)
    assert gate.observe(101) is State.QUIET
    assert gate.observe(103, verified=True) is State.VERIFIED
    assert gate.observe(110) is State.VERIFIED


def test_grace_period_reveals_only_when_still_unverified():
    gate = VerificationGate(100, quiet_seconds=8, total_seconds=90)
    assert gate.observe(107.9) is State.QUIET
    assert gate.observe(108) is State.INTERACTION
    assert gate.observe(120) is State.INTERACTION
    assert gate.observe(121, verified=True) is State.VERIFIED


def test_verified_at_reveal_boundary_stays_unrevealed():
    gate = VerificationGate(100, quiet_seconds=8, total_seconds=90)
    assert gate.observe(108, verified=True) is State.VERIFIED


@pytest.mark.parametrize("flag", ["closed", "cancelled"])
def test_close_or_cancel_wins_over_simultaneous_success(flag):
    gate = VerificationGate(100)
    assert gate.observe(101, verified=True, **{flag: True}) is State.CANCELLED
    assert gate.observe(102, verified=True) is State.CANCELLED


def test_timeout_rejects_late_success():
    gate = VerificationGate(100, quiet_seconds=8, total_seconds=90)
    assert gate.observe(190, verified=True) is State.TIMED_OUT
    assert gate.observe(191, verified=True) is State.TIMED_OUT


@pytest.mark.parametrize("started,quiet,total", [
    (float("nan"), 8, 90), (0, -1, 90), (0, 91, 90), (0, 8, 0),
    (0, 8, float("inf")), (0, True, 90),
])
def test_invalid_deadlines_rejected(started, quiet, total):
    with pytest.raises(ValueError):
        VerificationGate(started, quiet_seconds=quiet, total_seconds=total)


def test_clock_must_not_move_backwards():
    gate = VerificationGate(100)
    assert gate.observe(102) is State.QUIET
    with pytest.raises(ValueError):
        gate.observe(101)


def test_zero_grace_allows_immediate_interaction():
    assert VerificationGate(100, quiet_seconds=0).observe(100) is State.INTERACTION


def test_finite_inputs_must_not_overflow_deadlines():
    with pytest.raises(ValueError):
        VerificationGate(1e308, quiet_seconds=1e308, total_seconds=1e308)
