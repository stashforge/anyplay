import os
from unittest.mock import patch

from media_cli.ui import viewport


def test_background_default_and_small_screen_clamp():
    from media_cli.state.theme import MIDNIGHT_THEME
    with patch('media_cli.state.theme.MINT_SCREEN_THEME', MIDNIGHT_THEME), patch.object(viewport, 'configured_padding', return_value=None):
        assert viewport.padding_for(80, 24) == (1, 2, 1, 2)
        assert viewport.padding_for(30, 10) == (0, 0, 0, 0)
        assert viewport.padding_for(42, 13) == (0, 1, 1, 1)


def test_explicit_zero_and_coordinate_translation():
    physical = os.terminal_size((80, 24))
    with patch.object(viewport, 'configured_padding', return_value=(0, 0, 0, 0)):
        assert viewport.padding_for(80, 24) == (0, 0, 0, 0)
    with patch.object(viewport, 'configured_padding', return_value=(1, 2, 1, 2)), patch.object(viewport.os, 'get_terminal_size', return_value=physical):
        assert viewport.content_size() == (76, 22)
        assert viewport.offset_output('\033[Htitle\033[22;76H') == '\033[2;3Htitle\033[23;78H'


def test_css_padding_values():
    for value, expected in [('0', (0, 0, 0, 0)), ('2', (2, 2, 2, 2)), ('1 3', (1, 3, 1, 3)), ('1 2 3', (1, 2, 3, 2)), ('1 2 3 4', (1, 2, 3, 4)), ('-1', None), ('', None)]:
        viewport.configured_padding.cache_clear()
        with patch('media_cli.state.config.load_config_file', return_value={'padding': value}):
            assert viewport.configured_padding() == expected
    viewport.configured_padding.cache_clear()


def test_centered_width_respects_padding_and_full_width_override():
    with patch.object(viewport, 'configured_padding', return_value=(1, 2, 1, 2)), patch.object(viewport, 'configured_max_width', return_value=120):
        assert viewport.padding_for(200, 40) == (1, 40, 1, 40)
        assert viewport.padding_for(201, 40) == (1, 41, 1, 40)
        assert viewport.padding_for(80, 24) == (1, 2, 1, 2)
        with patch.object(viewport.os, 'get_terminal_size', return_value=os.terminal_size((200, 40))):
            assert viewport.content_size() == (120, 38)
            assert viewport.offset_output('\033[1;1H') == '\033[2;41H'
    with patch.object(viewport, 'configured_padding', return_value=(1, 2, 1, 2)), patch.object(viewport, 'configured_max_width', return_value=0):
        assert viewport.padding_for(200, 40) == (1, 2, 1, 2)


def test_tiny_positive_width_remains_usable():
    with patch.object(viewport, 'configured_padding', return_value=(0, 0, 0, 0)), patch.object(viewport, 'configured_max_width', return_value=1):
        assert viewport.padding_for(80, 24) == (0, 20, 0, 20)
        assert viewport.padding_for(30, 24) == (0, 0, 0, 0)


def test_multiline_frames_keep_each_row_inside_viewport():
    with patch.object(viewport, 'configured_padding', return_value=(1, 2, 1, 2)), patch.object(viewport, 'configured_max_width', return_value=120), patch.object(viewport.os, 'get_terminal_size', return_value=os.terminal_size((200, 40))):
        frame = viewport.positioned_lines(['title', '', '\033[2Kdetails'])
        assert '\r' not in frame and '\n' not in frame
        assert viewport.offset_output(frame) == '\033[2;41Htitle\033[3;41H\033[4;41H\033[2Kdetails'
