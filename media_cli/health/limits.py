"""Context-local time budget for diagnostic operations only."""
from contextlib import contextmanager
from contextvars import ContextVar
from math import isfinite
from time import monotonic


class DiagnosticBudgetExceeded(Exception):
    """The shared diagnostic deadline has been reached."""


_deadline: ContextVar[float | None] = ContextVar('diagnostic_deadline', default=None)


def _positive_seconds(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('Diagnostic duration must be positive and finite')
    try:
        seconds = float(value)
    except OverflowError:
        raise ValueError('Diagnostic duration must be positive and finite') from None
    if not isfinite(seconds) or seconds <= 0:
        raise ValueError('Diagnostic duration must be positive and finite')
    return seconds


@contextmanager
def diagnostic_budget(seconds=900):
    """Share a deadline within this context; nested budgets cannot extend it."""
    deadline = monotonic() + _positive_seconds(seconds)
    if not isfinite(deadline):
        raise ValueError('Diagnostic deadline must be finite')
    parent = _deadline.get()
    if parent is not None:
        deadline = min(deadline, parent)
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def _remaining():
    deadline = _deadline.get()
    if deadline is None:
        return None
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise DiagnosticBudgetExceeded('Diagnostic run deadline exceeded')
    return remaining


def remaining_timeout(configured: float) -> float:
    """Clip an operation timeout to the remaining shared run budget."""
    configured = _positive_seconds(configured)
    remaining = _remaining()
    return configured if remaining is None else min(configured, remaining)


def check_budget() -> None:
    """Raise when an active diagnostic budget has expired."""
    _remaining()
