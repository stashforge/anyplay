"""Keyboard card is bounded, grouped and independent of list selection."""
import pytest
from media_cli.core.terminal import strip_ansi, display_width
from media_cli.ui.key_guide import key_guide_frame, list_key_groups


@pytest.mark.parametrize("width,height", [(100, 40), (40, 18), (22, 9)])
def test_key_card_fits_terminal_and_can_scroll(width, height):
    groups = list_key_groups({"Enter": "Open title", "Type": "Old filter", "Ctrl+O": "Information"},
                             aliases=(("i", "Information"),))
    lines, maximum = key_guide_frame(groups, width, height)
    assert len(lines) == height
    assert all(display_width(line) <= width - 1 for line in lines)
    text = "\n".join(map(strip_ansi, lines))
    assert "Anyplay" in text
    assert "results" not in text
    assert "Old filter" not in text
    if maximum:
        final, _ = key_guide_frame(groups, width, height, offset=maximum)
        assert final != lines


def test_legacy_help_describes_actual_typing_behavior():
    groups = list_key_groups({"Type": "Filter visible items"}, filtered=False)
    assert ("Type", "Filter visible items") in dict(groups)["Filter"]


def test_footer_shows_one_key_but_guide_keeps_alternatives():
    from media_cli.ui.screen_chrome import shortcut_footer
    text = strip_ansi(shortcut_footer(100, (("enter/right", "open"),
        ("tab/ctrl+n", "next"), ("/", "filter"), ("esc/left", "back")))[1])
    assert "enter open" in text and "tab next" in text and "/ filter" in text
    assert "esc back" in text
    assert "right" not in text and "ctrl+n" not in text and "left" not in text
    groups = list_key_groups({"Enter": "Open", "Right Arrow": "Open", "Ctrl+N": "Next tab"})
    assert ("Right Arrow", "Open") in dict(groups)["Navigation"]
    assert ("Ctrl+N", "Next tab") in dict(groups)["Navigation"]
