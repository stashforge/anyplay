"""Queued arrow repeats must not starve all intermediate paints."""

import re

from test_ui_view_terminal import Terminal


def test_queued_arrows_paint_progress_and_preserve_every_key(tmp_path):
    script = '''
items = tuple(ViewItem(str(i), 'Title %03d' % i,
    details=DetailLines(Content('POSITION %03d' % i), Blank, Blank)) for i in range(1000))
session = ViewSession(ViewSpec('menu', 'Pick', items,
    details=DetailLines(Blank, Blank, Blank)), lambda event: ExitSession(event.item_id))
finish(run_view_session(flags, ui, session, poster_manager=manager))
'''
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'POSITION 000', b'\x1b[B' * 300)
        start = terminal.cursor
        terminal.send_after(b'POSITION 700', b'\r')
        positions = re.findall(rb'POSITION (\d{3})', terminal.output[start:])
        assert len(set(positions)) > 1, 'All 300 keys drained before any intermediate paint'
        assert terminal.result() == '700'
