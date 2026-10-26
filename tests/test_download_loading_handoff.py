"""Interactive downloads retain terminal ownership until the manager opens."""

from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui import player_screen
from media_cli.ui.transition import PreparationCancelled


@pytest.fixture
def attempt(monkeypatch, tmp_path):
    core = playback_player.app_core
    events = []
    show = dict(_id='one', _provider='example', name='Example')
    data = {'episode': {'sourceUrls': []}}
    stream = dict(link='https://example.test/video.m3u8', source_name='Example')
    state = MachineState(current_ep='1')
    ui = UiState(ui_show_ctx=show)
    args = SimpleNamespace(download=True, no_tui=False, print_url=False, sources=False, aniskip=False)
    terminal = {'stdout': True}
    monkeypatch.setattr(playback_player.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(playback_player.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(core, 'with_loading', lambda message, work: work())
    monkeypatch.setattr(core, 'prepare_show_display_state', lambda *a: None)
    monkeypatch.setattr(core, 'ensure_episode_ids', lambda *a: ['1'])
    monkeypatch.setattr(core, 'get_show_anilist_id', lambda *a: None)
    monkeypatch.setattr(core, 'enter_alt_screen', lambda: events.append('enter'))
    monkeypatch.setattr(core, 'restore_terminal', lambda: events.append('restore'))
    monkeypatch.setattr(core, '_exit_player_screen', lambda **kw: events.append(('exit', kw.get('close_alt', False))))
    monkeypatch.setattr(core, '_set_cached_ep_data', lambda *a: None)
    monkeypatch.setattr(core, '_clear_streams', lambda: None)
    monkeypatch.setattr(core, 'is_termux', lambda: False)
    monkeypatch.setattr(core, 'set_action_feedback', lambda *a: events.append('feedback'))
    monkeypatch.setattr(core, 'download_episode', lambda *a, **kw: pytest.fail('Unexpected console downloader'))
    monkeypatch.setattr(core, 'fetch_episode_stream', lambda *a, **kw: pytest.fail('Unexpected provider request'))
    monkeypatch.setattr(player_screen, '_player_ui_state', {'active': False})
    monkeypatch.setattr(player_screen, 'render', lambda **kw: None)
    player_screen.reset_loading_cancel()
    monkeypatch.setattr(core, '_ipc_player', SimpleNamespace(
        prefetched_ep='1', prefetched_res=(stream, 'Example', data, [stream])))

    def run(flags=None):
        # Pytest replaces capture streams between fixture setup and test calls.
        monkeypatch.setattr(playback_player.sys.stdin, 'isatty', lambda: True)
        monkeypatch.setattr(playback_player.sys.stdout, 'isatty', lambda: terminal['stdout'])
        return playback_player.handle_play_state(flags or CliFlags(), ui, state,
            {'download_dir': str(tmp_path)}, args, 'sub', 'best', lambda *a: False)

    return SimpleNamespace(core=core, events=events, data=data, args=args, state=state, run=run, terminal=terminal)


@pytest.mark.parametrize('batch_end,expected', [(None, ['1']), ('2', ['1', '2'])])
def test_interactive_download_enters_manager_without_playback_loading_or_shell_output(
        monkeypatch, attempt, capsys, batch_end, expected):
    from media_cli.media import download_worker
    from media_cli.ui import download_manager

    queued = []
    attempt.state.download_batch_end = batch_end
    monkeypatch.setattr(attempt.core, 'ensure_episode_ids', lambda *a: ['1', '2', '3'])
    monkeypatch.setattr(download_worker, 'get_global_worker', lambda *a: SimpleNamespace(add_item=queued.append))

    def manager(worker, **kwargs):
        assert [item.ep_id for item in queued] == expected
        assert 'restore' not in attempt.events
        assert 'enter' not in attempt.events
        assert not player_screen._player_ui_state['active']
        assert ('exit', True) not in attempt.events
        attempt.events.append('manager')
        return 'QUIT'

    monkeypatch.setattr(download_manager, 'run_download_manager', manager)
    assert attempt.run() == 'QUIT'
    assert attempt.events[-1] == 'manager'
    assert not capsys.readouterr().out


@pytest.mark.parametrize('explicit_episode', [False, True])
def test_download_resolution_runs_from_queued_item_after_manager_opens(monkeypatch, attempt, capsys, explicit_episode):
    from concurrent.futures import ThreadPoolExecutor

    from media_cli.media import download_worker
    from media_cli.ui import download_manager

    attempt.core._ipc_player.prefetched_ep = None
    attempt.core._ipc_player.prefetched_res = None
    attempt.args._cli_episode_passed = explicit_episode
    monkeypatch.setattr(attempt.core, 'get_episode_data', lambda *a, **kw: pytest.fail('Blocking metadata fetch'))
    monkeypatch.setattr(player_screen, 'run_loading_work', lambda *a, **kw: pytest.fail('Playback loading screen'))
    queued = []
    monkeypatch.setattr(download_worker, 'get_global_worker', lambda *a: SimpleNamespace(add_item=queued.append))

    def resolve(*args, **kwargs):
        assert attempt.events[-1] == 'manager'
        assert args[:3] == ('one', '1', 'sub')
        return {'link': 'https://example.test/video.m3u8'}, 'Example', attempt.data, []

    def manager(worker, **kwargs):
        attempt.events.append('manager')
        assert queued and kwargs['standalone']
        with ThreadPoolExecutor(max_workers=1) as pool:
            url, headers, proxy = pool.submit(queued[0].resolve_fn, queued[0]).result(timeout=1)
        assert url == 'https://example.test/video.m3u8' and proxy is None
        assert queued[0].current_mirror_name == 'Example'
        assert any('Testing mirror' in line for line in queued[0].logs)
        return 'BACK'

    monkeypatch.setattr(attempt.core, 'fetch_episode_stream', resolve)
    monkeypatch.setattr(download_manager, 'run_download_manager', manager)
    assert attempt.run() == ('QUIT' if explicit_episode else 'DETAILS')
    assert 'restore' not in attempt.events
    assert attempt.args.download is False
    assert not capsys.readouterr().out


def test_download_cancel_during_entry_preparation_consumes_request(monkeypatch, attempt):
    def cancel(*args, **kwargs):
        raise PreparationCancelled

    monkeypatch.setattr(attempt.core, 'with_loading', cancel)
    assert attempt.run() == 'DETAILS'
    assert attempt.args.download is False
    assert not attempt.events


@pytest.mark.parametrize('mode', ['plain', 'no_tui', 'redirected'])
def test_console_download_keeps_existing_terminal_output(monkeypatch, attempt, capsys, mode):
    flags = CliFlags(plain_mode=mode == 'plain')
    attempt.args.no_tui = mode == 'no_tui'
    if mode == 'redirected':
        attempt.terminal['stdout'] = False

    def console_download(*args, **kwargs):
        assert 'restore' in attempt.events
        assert 'Resolving stream' in capsys.readouterr().out
        raise RuntimeError('Reached console downloader')

    monkeypatch.setattr(attempt.core, 'download_episode', console_download)
    with pytest.raises(RuntimeError, match='Reached console downloader'):
        attempt.run(flags)
