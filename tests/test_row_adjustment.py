import pytest

from media_cli.ui.row_adjustment import adjustment_direction


@pytest.mark.parametrize(
    "key,query,cursor,expected",
    (
        ("LEFT", "", 0, -1),
        ("RIGHT", "", 0, 1),
        ("LEFT", "buffer", 0, -1),
        ("RIGHT", "buffer", 6, 1),
        ("LEFT", "buffer", 3, None),
        ("RIGHT", "buffer", 3, None),
        ("LEFT", "buffer", 6, None),
        ("RIGHT", "buffer", 0, None),
        ("ENTER", "buffer", 6, None),
        ("UP", "", 0, None),
    ),
)
def test_arrows_only_adjust_at_their_outward_filter_boundary(key, query, cursor, expected):
    assert adjustment_direction(key, query, cursor) == expected


@pytest.mark.parametrize("cursor", (-1, 7))
def test_invalid_cursor_position_fails(cursor):
    with pytest.raises(ValueError):
        adjustment_direction("LEFT", "buffer", cursor)


@pytest.mark.parametrize("key,query,cursor", ((None, "", 0), ("LEFT", None, 0), ("LEFT", "", True)))
def test_invalid_context_types_fail(key, query, cursor):
    with pytest.raises(TypeError):
        adjustment_direction(key, query, cursor)


def test_plain_selection_does_not_invoke_native_adjustment():
    from types import SimpleNamespace

    from media_cli.ui.plain_picker import plain_pick

    def adjust(index, direction):
        raise AssertionError("Plain mode has its own input vocabulary")

    assert (
        plain_pick(
            SimpleNamespace(),
            SimpleNamespace(),
            "Settings",
            ["Buffer"],
            adjust_fn=adjust,
            input_fn=lambda prompt: "1",
            output_fn=lambda *args: None,
        )
        == 0
    )
