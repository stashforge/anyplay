"""Diagnostic deadlines use a fake clock, never blocking waits."""
from contextvars import Context

import pytest

from media_cli.health import limits


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(limits, 'monotonic', lambda: now[0])
    return now


def test_no_budget_preserves_configured_timeout(clock):
    assert limits.remaining_timeout(12) == 12.0
    assert limits.check_budget() is None


def test_default_budget_clips_timeouts_to_remaining_time(clock):
    with limits.diagnostic_budget():
        assert limits.remaining_timeout(1000) == 900.0
        clock[0] += 895
        assert limits.remaining_timeout(12) == 5.0
        assert limits.remaining_timeout(2) == 2.0
        assert limits.check_budget() is None


def test_longer_nested_budget_cannot_extend_parent(clock):
    with limits.diagnostic_budget(10):
        clock[0] += 3
        with limits.diagnostic_budget(100):
            assert limits.remaining_timeout(50) == 7.0
        assert limits.remaining_timeout(50) == 7.0
    assert limits.remaining_timeout(50) == 50.0


def test_shorter_nested_budget_restores_parent_on_exception(clock):
    with limits.diagnostic_budget(20):
        with pytest.raises(RuntimeError, match='operation failed'):
            with limits.diagnostic_budget(5):
                clock[0] += 2
                assert limits.remaining_timeout(50) == 3.0
                raise RuntimeError('operation failed')
        assert limits.remaining_timeout(50) == 18.0


@pytest.mark.parametrize('elapsed', [5.0, 6.0])
def test_expired_budget_raises_for_checks_and_timeouts_and_resets(clock, elapsed):
    with pytest.raises(limits.DiagnosticBudgetExceeded):
        with limits.diagnostic_budget(5):
            clock[0] += elapsed
            with pytest.raises(limits.DiagnosticBudgetExceeded):
                limits.remaining_timeout(10)
            limits.check_budget()
    assert limits.remaining_timeout(10) == 10.0
    assert limits.check_budget() is None


def test_cleanup_even_on_base_exception(clock):
    with pytest.raises(KeyboardInterrupt):
        with limits.diagnostic_budget(1):
            raise KeyboardInterrupt
    clock[0] += 2
    assert limits.check_budget() is None


def test_budget_is_local_to_context(clock):
    with limits.diagnostic_budget(3):
        assert Context().run(limits.remaining_timeout, 50) == 50.0
        assert limits.remaining_timeout(50) == 3.0


@pytest.mark.parametrize('value', [0, -1, float('inf'), float('-inf'), float('nan'),
                                  10 ** 1000, True, '5', None])
def test_invalid_duration_is_rejected_without_overflow_or_context_leak(clock, value):
    with limits.diagnostic_budget(20):
        with pytest.raises(ValueError):
            with limits.diagnostic_budget(value):
                pytest.fail('invalid budget entered')
        with pytest.raises(ValueError):
            limits.remaining_timeout(value)
        assert limits.remaining_timeout(50) == 20.0
