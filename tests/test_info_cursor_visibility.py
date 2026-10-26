import pytest

from media_cli.ui import display, info_screen, terminal_images, terminal_output


@pytest.mark.parametrize('in_tui,expected', [(True,'\033[?25l'),(False,'\033[?25h')])
def test_info_close_keeps_cursor_hidden_in_parent_tui(monkeypatch,in_tui,expected):
    frames=[]
    monkeypatch.setattr(display,'is_alt_screen_active',lambda:in_tui)
    monkeypatch.setattr(info_screen,'_build_content',lambda *args:[])
    monkeypatch.setattr(info_screen,'_read_key',lambda fd:'ESC')
    monkeypatch.setattr(terminal_images,'replace_for_frame',lambda **kw:'')
    monkeypatch.setattr(terminal_images,'clear_now',lambda:'')
    monkeypatch.setattr(terminal_output,'write_terminal',frames.append)
    info_screen._show_info_screen({'name':'Title'},0,poster_manager=None,ui=None)
    assert frames[-1] == expected
