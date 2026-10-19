import os
import re

import pytest

from test_footer_rendering import foreground_at
from test_ui_view_terminal import Terminal


def footer_write(output, marker=b'Enter=select'):
    rows = re.findall(rb'\x1b\[\d+;1H\x1b\[2K(.*?)(?=\x1b\[\d+;\d+H|\Z)', output, re.S)
    return next(row.decode() for row in reversed(rows) if marker in row)


@pytest.mark.parametrize("outcome", ("complete", "fail", "cancel"))
def test_legacy_search_footer_colors_in_actual_terminal(tmp_path, outcome):
    script = f'''
from media_cli.ui.panels import render_search_header
show = {{'_id': 'example', 'name': 'Example', 'status': 'FINISHED', '_local_progress': {{}},
        '_poster_raw': 'cached', '_poster_status': 'ready'}}
active = [True]
def key(key, index):
    if key == 'F5':
        active[0] = False
    return False
def header(index):
    return render_search_header('Anikoto', 'example', 'sub', lambda: [show],
        lambda: '\\033[36m* Enriching metadata...\\033[0m' if active[0] else '',
        get_error_fn=lambda: 'Request failed' if not active[0] and {outcome!r} == 'fail' else '')
finish(picker_module.tui_pick(flags, ui, 'Search Anime', ['Example'], header_fn=header,
                             key_fn=key, structured=True, return_on_quit=True))
'''
    with Terminal(tmp_path, script, columns=160) as terminal:
        terminal.until(b'Enriching metadata...')
        terminal.until(b'\033[35;160H')
        active = footer_write(bytes(terminal.output))
        assert set(foreground_at(active, 'Enriching metadata...')) == {(36,)}
        for token in ('Anikoto', 'Enter=select', 'Esc=quit'):
            assert set(foreground_at(active, token)) == {(38, 5, 244)}
        offset = len(terminal.output)
        terminal.cursor = offset
        os.write(terminal.master, b'\x1b[15~')
        terminal.until(b'Enter=select')
        terminal.until(b'\033[35;160H')
        idle = footer_write(bytes(terminal.output[offset:]))
        assert 'Enriching metadata...' not in idle
        for token in ('Enter=select', 'Esc=quit'):
            assert set(foreground_at(idle, token)) == {(38, 5, 244)}
        os.write(terminal.master, b'\x03')
        assert terminal.result() == -5


@pytest.mark.parametrize("outcome", ("complete", "fail", "cancel"))
def test_shared_footer_colors_in_actual_terminal(tmp_path, outcome):
    gate = tmp_path / 'finish-loading'
    script = f'''
from pathlib import Path
from media_cli.ui.view_model import Blank, Content, Inherit
from media_cli.ui.view_session import BeginNavigation, CompleteNavigation, FailNavigation
source = ViewSpec('source', 'Artist', (ViewItem('artist', 'Example', details=Inherit),),
    details=DetailLines(Content('Artist: Example'), Blank, Content('ARTIST')), context=('Anikoto',),
    actions=(ViewAction('select', 'select', ('ENTER',)), ViewAction('finish', 'finish', ('CTRL_R',))))
def complete():
    while not Path({str(gate)!r}).exists():
        time.sleep(0.01)
    if {outcome!r} != 'cancel':
        if {outcome!r} == 'complete':
            session.post(CompleteNavigation('source', session.generation, 'request', replace(source, id='destination')))
        elif {outcome!r} == 'fail':
            session.post(FailNavigation('source', session.generation, 'request', 'Request failed'))
session = ViewSession(source)
session.apply(BeginNavigation('source', session.generation, 'request', 'Fetching artist'))
threading.Thread(target=complete, daemon=True).start()
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script, columns=160) as terminal:
        terminal.until(b'Fetching artist')
        terminal.until(b'\033[35;160H')
        active = footer_write(bytes(terminal.output), marker=b'Esc=back')
        assert set(foreground_at(active, 'Fetching artist')) == {(36,)}
        assert set(foreground_at(active, 'Anikoto')) == {(38, 5, 244)}
        assert 'Enter=select' not in active
        assert set(foreground_at(active, 'Esc=back')) == {(38, 5, 244)}
        offset = len(terminal.output)
        terminal.cursor = offset
        if outcome == 'cancel':
            os.write(terminal.master, b'\x1b')
        gate.touch()
        terminal.until(b'Enter=select')
        terminal.until(b'\033[35;160H')
        idle = footer_write(bytes(terminal.output[offset:]))
        assert 'Fetching artist' not in idle
        if outcome == 'fail':
            assert set(foreground_at(idle, 'Request failed')) == {(38, 5, 203)}
        assert set(foreground_at(idle, 'Anikoto')) == {(38, 5, 244)}
        assert set(foreground_at(idle, 'Enter=select')) == {(38, 5, 244)}
        os.write(terminal.master, b'\x03')
        assert terminal.result() is None
