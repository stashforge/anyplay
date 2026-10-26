"""Native downloader return must keep resumed menus out of the shell buffer."""

import re

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize('standalone', [True, False])
def test_downloader_back_reenters_alt_screen_before_resuming_menu(tmp_path, standalone):
    script = f'''
from media_cli.ui import display, download_manager
terminal_marker('SHELL BEFORE')
display.enter_alt_screen()
def native_screen(stdscr, worker, **kwargs):
    stdscr.addstr(0,0,'NATIVE DOWNLOADER')
    stdscr.refresh()
    return 'BACK'
download_manager._curses_download_manager=native_screen
result=download_manager.run_download_manager(SimpleNamespace(start=lambda:None),standalone={standalone!r})
picked=picker_module.tui_pick(flags,ui,'Resumed menu',['One','Two'],
    header='RESUMED DETAILS',return_on_quit=True)
display.restore_terminal()
terminal_marker('SHELL RETURNED')
finish(dict(manager=result,picked=picked))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'RESUMED DETAILS', b'\x1b')
        assert terminal.result() == {'manager': 'BACK', 'picked': -2}
        active = False
        seen = []
        for match in re.finditer(rb'\x1b\[\?1049[hl]|RESUMED DETAILS|SHELL RETURNED', terminal.output):
            token = match.group()
            if token == b'\x1b[?1049h':
                active = True
            elif token == b'\x1b[?1049l':
                active = False
            elif token == b'RESUMED DETAILS':
                assert active, 'Resumed TUI was drawn into the normal shell buffer'
                seen.append('menu')
            else:
                assert not active, 'Final exit did not restore the shell buffer'
                seen.append('shell')
        assert 'menu' in seen and seen[-1] == 'shell'
