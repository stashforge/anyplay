"""No matching selectable rows must not retain a hidden active item."""

import os

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("images", [False, True])
def test_no_matches_clear_details_cover_and_row_actions_then_recover(tmp_path, images):
    source = """
flags.show_image = IMAGES
row_actions, refreshes = [], []
def cover(show):
    show.update(_poster_raw='COVER-SENTINEL', _poster_status='ready')
    return 'COVER-SENTINEL'
manager.get = cover
view = ViewSpec('results', 'Choose', (
    ViewItem('alpha', 'Alpha', details=DetailLines(Content('ALPHA-TITLE'), Blank, Content('ALPHA-METADATA')),
             footer_context=('ALPHA-CONTEXT',), image=ImageSpec('alpha', 'available', 'https://images.test/a.png')),
    ViewItem('beta', 'Beta', details=DetailLines(Content('BETA-TITLE'), Blank, Content('BETA-METADATA'))),
), details=DetailLines(Blank, Blank, Blank), context=('COMMON-CONTEXT',),
   actions=(ViewAction('delete', 'Delete', ('DELETE',)),
            ViewAction('refresh_title', 'Refresh title', ('CTRL_R',)),
            ViewAction('information', 'Information', ('CTRL_O',)),
            ViewAction('refresh_view', 'Refresh view', ('F5',))))
def handle(event):
    if event.kind == 'action' and event.value == 'refresh_view':
        refreshes.append(dict(selected=session.position.selected_id, hovered=ui.hovered_show_obj))
        print('GLOBAL-REFRESH', flush=True)
    elif event.kind == 'action':
        item = session.item(event.item_id)
        if item is not None:
            row_actions.append(item.id)
    elif event.kind == 'select':
        return ExitSession(event.item_id)
session = ViewSession(view, handle)
selected = run_view_session(flags, ui, session, poster_manager=manager)
finish(dict(selected=selected, row_actions=row_actions, refreshes=refreshes, query=session.position.query))
""".replace("IMAGES", repr(images))
    with Terminal(tmp_path, source, columns=160) as terminal:
        terminal.send_after(b"ALPHA-METADATA", b"zzzz")
        terminal.until(b"No matching results")
        terminal.send_after(b"COMMON-CONTEXT", b"\x12\x1b[3~\x0f\r\x1b[15~")
        empty_output = terminal.until(b"GLOBAL-REFRESH")
        assert all(marker not in empty_output for marker in (b"ALPHA-TITLE", b"ALPHA-METADATA",
                                                             b"ALPHA-CONTEXT", b"COVER-SENTINEL"))
        os.write(terminal.master, b"\x15")
        terminal.send_after(b"ALPHA-METADATA", b"\r")
        assert terminal.result() == dict(selected="alpha", row_actions=[],
                                         refreshes=[dict(selected=None, hovered=None)], query="")
