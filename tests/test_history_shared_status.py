"""Background refresh status does not replace History context or block tabs."""

from test_ui_view_terminal import Terminal


def test_shared_history_refresh_footer_keeps_tabs_responsive_and_restores(tmp_path):
    source = """
from media_cli.app.history_menu_view import build_history_view
from media_cli.app.history_menu_controller import run_history_menu
from media_cli.ui.background_loading import BackgroundLoadingStatus
from media_cli.ui.loading_model import LoadingTaskSpec, LoadingOwner, LoadingInteraction, LoadingPlacement
from media_cli.ui.actions import ActionBindings
from media_cli.ui.view_position_memory import SavedViewPosition
state = dict(mode='Active', busy=True)
views = []
loading = BackgroundLoadingStatus(LoadingTaskSpec('refresh', LoadingOwner('history', 0),
    'Refreshing 2 titles...', LoadingInteraction.BACKGROUND, LoadingPlacement.FOOTER,
    ActionBindings(()), delay=0), None, frame=lambda: '~')
entries = [dict(show=dict(_id='one', _provider='example', name='Same title'),
                translation_type='sub', timestamp=140)]
ui._view_positions = {('history',): SavedViewPosition('history:example:one:sub', 'Same', 0, 2, 'Active')}
def read():
    status = loading.read(state['busy'])
    view = build_history_view(entries, mode=state['mode'], total_count=1, now=200, show_cover=False,
                              footer_statuses=(status,) if status else ())
    views.append(view)
    return view
def switch(mode):
    state.update(mode=mode, busy=False)
result = run_history_menu(flags, ui, read_view=read, switch_mode=switch, select=lambda ref: True,
    refresh_title=lambda ref: None, refresh_view=lambda: None, delete=lambda ref: None,
    information=lambda ref: None)
saved = ui._view_positions[('history',)]
finish(dict(kind=result.kind, mode=state['mode'], statuses=len(views[-1].footer_statuses),
            query=saved.query, cursor=saved.query_cursor, selected=result.reference.show_id))
"""
    with Terminal(tmp_path, source, columns=200) as terminal:
        terminal.send_after(b"Refreshing 2 titles...", b"\t")
        output = terminal.until(b"Up to date")
        terminal.send_after(b"Updated 1m ago", b"\r")
        assert b"Watch History" in output
        assert terminal.result() == dict(kind="selected", mode="Up to date", statuses=0,
                                         query="Same", cursor=2, selected="one")
