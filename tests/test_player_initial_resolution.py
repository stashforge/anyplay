"""Cold playback must prepare once and use one extraction owner."""

from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui import player_screen


def test_cold_playback_fetches_and_augments_once_without_background_preflight(monkeypatch):
    core = playback_player.app_core
    show = {'_id': 'one', '_provider': 'example', 'name': 'Example'}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(current_ep='1')
    data = {'episode': {'sourceUrls': [{'sourceName': 'Mirror'}]}}
    counts = {'fetch': 0, 'lookup': 0, 'resolve': 0}
    monkeypatch.setattr(player_screen, '_player_ui_state', {})
    monkeypatch.setattr(player_screen, 'reset_loading_cancel', lambda: None)
    monkeypatch.setattr(player_screen, 'loading_cancel_requested', lambda: False)
    monkeypatch.setattr(player_screen, 'run_loading_work', lambda work, **kw: work())
    monkeypatch.setattr(core, 'with_loading', lambda message, work: work())
    monkeypatch.setattr(core, 'prepare_show_display_state', lambda *a: None)
    monkeypatch.setattr(core, 'ensure_episode_ids', lambda *a: ['1'])
    monkeypatch.setattr(core, 'get_show_anilist_id', lambda *a: None)
    monkeypatch.setattr(core, 'enter_alt_screen', lambda: None)
    monkeypatch.setattr(core, 'is_termux', lambda: False)
    monkeypatch.setattr(core, '_ipc_player', SimpleNamespace(prefetched_ep=None, prefetched_res=None))
    monkeypatch.setattr(core, '_get_cached_ep_data', lambda *a: None)
    monkeypatch.setattr(core, '_set_cached_ep_data', lambda *a: None)
    monkeypatch.setattr(core, '_stream_snapshot', lambda *a, **kw: [])
    monkeypatch.setattr(playback_player.streams_mod, '_bg_thread', None)
    monkeypatch.setattr(playback_player, '_local_stream_for_episode', lambda *a: None)

    def fetch(*args, **kwargs):
        counts['fetch'] += 1
        return data

    def lookup(*args, **kwargs):
        counts['lookup'] += 1
        return data

    def resolve(episode, key, **kwargs):
        counts['resolve'] += 1
        assert episode is data
        assert key[-1] == '1080p'
        return ({'source_name': 'Mirror', 'resolution': '1080p', 'link': 'stream'}, 'Mirror', data, [])

    class Selected(Exception):
        pass

    def selected(*args, **kwargs):
        raise Selected

    monkeypatch.setattr(core, 'get_episode_data', fetch)
    monkeypatch.setattr(playback_player, '_lookup_player_sources', lookup)
    monkeypatch.setattr(playback_player, '_fetch_player_fallback', resolve)
    monkeypatch.setattr(playback_player.streams_mod, 'start_bg_resolve',
                        lambda *a, **kw: pytest.fail('Cold playback started duplicate background extraction'))
    monkeypatch.setattr(core, '_extend_streams', selected)
    args = SimpleNamespace(aniskip=False, download=False, print_url=False, sources=False, quality='1080p')
    with pytest.raises(Selected):
        playback_player.handle_play_state(CliFlags(), ui, ms, {'quality': '720p'}, args, 'sub', '1080p', lambda *a: False)
    assert counts == {'fetch': 1, 'lookup': 1, 'resolve': 1}


@pytest.mark.parametrize('prefetched', [False, True])
def test_downloaded_next_episode_is_selected_before_network_preparation(monkeypatch, prefetched):
    core = playback_player.app_core
    show = {'_id': 'one', '_provider': 'example', 'name': 'Example'}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(current_ep='2')
    local = ({'link': '/downloads/episode2.mkv', 'is_local': True}, 'Local File', {'is_local': True}, [])
    online = ({'link': 'https://example.invalid/video'}, 'Online', {}, [])
    monkeypatch.setattr(player_screen, '_player_ui_state', {})
    monkeypatch.setattr(core, 'with_loading', lambda message, work: work())
    monkeypatch.setattr(core, 'prepare_show_display_state', lambda *a: None)
    monkeypatch.setattr(core, 'ensure_episode_ids', lambda *a: ['1', '2'])
    monkeypatch.setattr(core, 'get_show_anilist_id', lambda *a: None)
    monkeypatch.setattr(core, 'enter_alt_screen', lambda: None)
    monkeypatch.setattr(core, '_ipc_player', SimpleNamespace(
        prefetched_ep='2' if prefetched else None,
        prefetched_res=online if prefetched else None))
    monkeypatch.setattr(playback_player, '_local_stream_for_episode', lambda *a: local)
    monkeypatch.setattr(core, 'get_episode_data', lambda *a, **kw: pytest.fail('Network episode request before local playback'))
    monkeypatch.setattr(playback_player, '_lookup_player_sources', lambda *a, **kw: pytest.fail('Source lookup for a downloaded episode'))
    monkeypatch.setattr(playback_player, '_load_player_mal_id', lambda *a, **kw: pytest.fail('Online ID lookup before local playback'))

    class LocalSelected(Exception):
        pass

    def selected(*args, **kwargs):
        assert ms.selected_stream is local[0]
        raise LocalSelected

    monkeypatch.setattr(core, '_extend_streams', selected)
    args = SimpleNamespace(aniskip=True, download=False, print_url=False, sources=False, quality='1080p')
    with pytest.raises(LocalSelected):
        playback_player.handle_play_state(CliFlags(), ui, ms, {}, args, 'sub', '1080p', lambda *a: False)
