"""Settings cycle through caller-provided values, independently of key bindings."""

import pytest

from media_cli.ui.row_adjustment import cycle_value


@pytest.mark.parametrize("current,direction,expected", [
    (False, 1, True), (True, 1, False), (False, -1, True), (True, -1, False),
])
def test_binary_values_toggle_in_either_direction(current, direction, expected):
    assert cycle_value(current, (False, True), direction) is expected


def test_multiple_values_wrap_and_leave_options_unchanged():
    values = ["off", "once", "song", "collection"]
    assert cycle_value("collection", values) == "off"
    assert cycle_value("off", values, -1) == "collection"
    assert cycle_value("once", values) == "song"
    assert cycle_value("song", values, -1) == "once"
    assert values == ["off", "once", "song", "collection"]
    assert cycle_value("only", ("only",)) == "only"


@pytest.mark.parametrize("current,values,direction", [
    ("off", (), 1), ("missing", ("off", "on"), 1),
    ("off", ("off", "on"), 0), ("off", ("off", "on"), 2),
])
def test_invalid_cycle_declarations_fail_clearly(current, values, direction):
    with pytest.raises(ValueError):
        cycle_value(current, values, direction)
