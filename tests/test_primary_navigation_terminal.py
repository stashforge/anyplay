import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("query_input,query", ((False, "match"), (True, "match"), (True, "")))
def test_structured_arrows_edit_query_instead_of_leaving_screen(tmp_path, query_input, query):
    script = f"""
from media_cli.ui.view_model import Blank, Inherit, SelectionPolicy
from media_cli.ui.view_session import ExitSession
view = ViewSpec('root', 'QUERY-EDIT', (ViewItem('one', 'match title', details=Inherit),),
    details=DetailLines(Blank, Blank, Blank), selection_policy=SelectionPolicy(query_input={query_input!r}))
def handle(event):
    return ExitSession(event.value if event.kind == 'query' else session.position.query)
session = ViewSession(view, handle)
session.position.query = {query!r}
finish(run_view_session(flags, ui, session, poster_manager=manager))
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.send_after(b"\033[12;100H", b"\x1b[DX" + (b"\r" if query_input else b"\x1b"))
        assert terminal.result() == ("matcXh" if query else "X")
