"""Plain History retains numbered selection and does not enter the shared TUI."""

import pytest
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("answer,expected,provider", [(b"1\n", "DETAILS", "first"),
                                                     (b"2\n", "DETAILS", "second"),
                                                     (b"b\n", "SEARCH", None), (b"q\n", "QUIT", None)])
def test_plain_history_compatibility_after_adapter_extraction(tmp_path, answer, expected, provider):
    source = """
from copy import deepcopy
from media_cli.app import search, history_menu_controller
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.context import CliFlags, UiState, MachineState
entries = [dict(show=dict(_id='one', _provider=provider, name='Same title', availableEpisodes={'sub': 1}),
                episode='1', translation_type='sub', progress=1) for provider in ('first', 'second')]
search.app_core.load_history = lambda: deepcopy(entries)
search.app_core.filter_history_entries = lambda entries, mode: list(entries)
search.app_core.batch_prepare_shows_display_state = lambda *a: None
search.app_core.prepare_show_display_state = lambda *a: None
search.app_core._poster_manager = SimpleNamespace()
search.app_core._get_poster = lambda *a: ''
search.app_core.build_info_panel = lambda show, tt, width, parts, **kw: parts.append('PLAIN-DETAILS')
search.app_core._poster_footer_line = lambda *a, **kw: ''
search.app_core.ensure_episode_ids = lambda *a: ['1']
search.app_core.playback_ep_from_history_entry = lambda *a, **kw: '1'
search.app_core.patch_history_entry_state = lambda *a: True
search.app_core.with_loading = lambda message, work: work()
search.history_domain.history_entry_refresh_is_stale = lambda *a, **kw: False
def shared_renderer(*args, **kwargs):
    raise AssertionError('Plain History must retain its separate adapter')
history_menu_controller.run_history_menu = shared_renderer
flags, ui = CliFlags(plain_mode=True, show_image=False, incognito_mode=True), UiState(history_filter='All')
try:
    result = run_screen_controller(search.handle_history_state, flags, ui, MachineState(), {}, SimpleNamespace(),
                                   'sub', None, fallback='HISTORY')
except SystemExit as exc:
    assert exc.code == 0
    result = 'QUIT'
finish(dict(state=result, provider=ui.ui_show_ctx.get('_provider')))
"""
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Enter choice:", answer)
        assert terminal.result() == dict(state=expected, provider=provider)
        assert b"\x1b[?1049h" not in terminal.output
