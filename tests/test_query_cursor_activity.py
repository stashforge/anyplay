import pytest

from media_cli.ui.query_cursor import QueryCursor


def test_entry_hint_expires_without_a_filter():
    cursor = QueryCursor(10.0)
    assert cursor.phase(10.0) == (True, True)
    assert cursor.phase(13.0) == (False, False)


def test_option_navigation_quiets_entry_hint_immediately():
    cursor = QueryCursor(10.0)
    cursor.quiet()
    assert cursor.phase(10.1) == (False, False)


def test_typing_or_actual_cursor_edit_wakes_feedback_again():
    cursor = QueryCursor(10.0)
    cursor.quiet()
    cursor.edit(15.0)
    assert cursor.phase(15.0) == (True, True)
    assert cursor.phase(18.0) == (False, False)


@pytest.mark.parametrize("elapsed, visible", ((0.1, True), (0.8, False), (1.3, True), (1.8, False), (2.3, True)))
def test_blink_keeps_existing_solid_then_half_second_phases(elapsed, visible):
    assert QueryCursor(10.0).phase(10.0 + elapsed) == (True, visible)


def test_quiet_cursor_does_not_wake_with_time():
    cursor = QueryCursor(0.0)
    cursor.quiet()
    assert cursor.phase(100.0) == (False, False)


@pytest.mark.parametrize("now", (13.0, 20.0, 100.0))
def test_nonempty_filter_keeps_cursor_active_after_idle_timeout(now):
    assert QueryCursor(10.0).phase(now, filtering=True)[0]


def test_restored_quiet_cursor_still_blinks_with_a_filter():
    cursor = QueryCursor(None)
    first = cursor.phase(100.0, filtering=True)
    second = cursor.phase(100.5, filtering=True)
    assert first[0] and second[0]
    assert first[1] != second[1]
