import pytest

from media_cli.ui.key_codes import decode_key_bytes


@pytest.mark.parametrize(
    "raw,key",
    (
        (b"\r", "ENTER"),
        (b"\n", "ENTER"),
        (b"\x03", "CTRL_C"),
        (b"\x1b", "ESC"),
        (b"\x1b[A", "UP"),
        (b"\x1bOB", "DOWN"),
        (b"\x1b[C", "RIGHT"),
        (b"\x1bOD", "LEFT"),
        (b"\x1b[Z", "SHIFT_TAB"),
        (b"\x1b[5~", "PAGE_UP"),
        (b"\x1b[6~", "PAGE_DOWN"),
        (b"\x1b[15~", "F5"),
        (b"\x1b[7~", "HOME"),
        (b"\x1b[8~", "END"),
        (b"\x1b[3~", "DELETE"),
        (b"\x12", "CTRL_R"),
        (b"\x1br", "ALT_R"),
        (b"\t", "TAB"),
        (b"x", "x"),
        (b" ", " "),
        (b"\x1bOQ", "UNKNOWN"),
        (b"\x01", "UNKNOWN"),
    ),
)
def test_complete_keys_use_the_same_names_for_picker_and_loading(raw, key):
    assert decode_key_bytes(raw) == key
