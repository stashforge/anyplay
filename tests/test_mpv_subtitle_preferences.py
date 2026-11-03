"""Configured mpv subtitle preferences also apply to late external tracks."""
import json

import pytest

from media_cli.playback.mpv import MpvIpc


def player_with_preferences(monkeypatch, languages):
    player = MpvIpc()
    commands = []
    monkeypatch.setattr(player, 'send_cmd', lambda *args: commands.append(args))
    player._track_subtitle_preferences({
        'event': 'client-message',
        'args': ['anyplay_subtitle_preferences', json.dumps(languages)],
    })
    return player, commands


@pytest.mark.parametrize('languages,expected', [
    (['eng'], ['auto', 'select', 'auto']),
    (['jpn', 'eng'], ['auto', 'auto', 'select']),
    (['deu'], ['auto', 'auto', 'auto']),
])
def test_external_selection_uses_preference_order_not_provider_default(monkeypatch, languages, expected):
    player, commands = player_with_preferences(monkeypatch, languages)
    tracks = [
        {'url': 'spanish', 'language': 'es', 'default': True},
        {'url': 'english', 'language': 'en'},
        {'url': 'japanese', 'lang': 'ja'},
    ]
    player.queue_subtitles(tracks, load_serial=0)
    player._attach_pending_external_tracks()
    assert [c[2] for c in commands if c[0] == 'sub-add'] == expected
    assert [c[4] for c in commands if c[0] == 'sub-add'] == ['es', 'en', 'ja']
    assert tracks[0]['default'] is True


def test_unlabelled_external_subtitle_is_not_forced_when_preference_exists(monkeypatch):
    player, commands = player_with_preferences(monkeypatch, ['eng'])
    player._pending_subtitle_url = 'unknown'
    player._attach_pending_external_tracks()
    assert commands == [('sub-add', 'unknown', 'auto')]


def test_no_preference_keeps_provider_default_and_language_metadata(monkeypatch):
    player, commands = player_with_preferences(monkeypatch, [])
    player.queue_subtitles([{'url': 'spanish', 'language': 'es', 'default': True}], load_serial=0)
    player._attach_pending_external_tracks()
    assert commands == [('sub-add', 'spanish', 'select', 'Subtitle', 'es')]


def test_english_label_can_supply_missing_language(monkeypatch):
    player, commands = player_with_preferences(monkeypatch, ['eng'])
    player.queue_subtitles([{'url': 'english', 'label': 'English (CC)'}], load_serial=0)
    player._attach_pending_external_tracks()
    assert commands == [('sub-add', 'english', 'select', 'English (CC)', 'eng')]


def test_late_attachment_and_stale_generation(monkeypatch):
    player, commands = player_with_preferences(monkeypatch, ['eng'])
    player.file_loaded = True
    player._load_serial = 2
    player.queue_subtitles([{'url': 'stale', 'language': 'en'}], load_serial=1)
    assert commands == []
    player.queue_subtitles([{'url': 'spanish', 'language': 'es'}], load_serial=2)
    assert commands == [('sub-add', 'spanish', 'auto', 'Subtitle', 'es')]


def test_unrelated_messages_do_not_change_preferences(monkeypatch):
    player, commands = player_with_preferences(monkeypatch, ['eng'])
    player._track_subtitle_preferences({'event': 'client-message', 'args': ['next_ep']})
    player.queue_subtitles([{'url': 'spanish', 'language': 'es'}], load_serial=0)
    player._attach_pending_external_tracks()
    assert commands[0][2] == 'auto'


def test_preferences_survive_preload_event_drain(monkeypatch):
    from types import SimpleNamespace
    player = MpvIpc()
    messages = [b'{"event":"client-message","args":["anyplay_subtitle_preferences","[\\"eng\\"]"]}\n']

    def recv(size):
        if messages:
            return messages.pop(0)
        raise BlockingIOError

    player.client = SimpleNamespace(recv=recv)
    monkeypatch.setattr(player, 'start', lambda: None)
    monkeypatch.setattr(player, 'send_cmd', lambda *args: None)
    player.load('https://example.test/video', 'Test', {}, '')
    assert player._subtitle_languages == ['eng']


@pytest.mark.parametrize('languages,sets_fallback', [('{}', False), ('{"eng"}', True)])
def test_runtime_only_disables_fallback_with_configured_languages(languages, sets_fallback):
    import shutil
    import subprocess
    from media_cli.playback.mpv_runtime import ANISKIP_LUA_SCRIPT

    lua = shutil.which('lua')
    if not lua:
        pytest.skip('Lua interpreter required')
    prelude = '''
local hooks = {}
local languages = LANGUAGES
package.preload['mp.utils'] = function() return {format_json = function() return 'prefs' end} end
mp = {
 add_hook = function(name, priority, fn) hooks[name] = fn end,
 observe_property = function() end, add_key_binding = function() end,
 register_event = function() end, register_script_message = function() end,
 get_property_native = function(name) assert(name == 'slang'); return languages end,
 set_property = function(name, value) print(name .. '=' .. value) end,
 commandv = function(...) print(table.concat({...}, '|')) end,
}
'''.replace('LANGUAGES', languages)
    result = subprocess.run([lua, '-'], input=prelude + ANISKIP_LUA_SCRIPT + '\nhooks.on_load()\n',
                            text=True, capture_output=True, check=True, timeout=3)
    assert ('file-local-options/subs-fallback=no' in result.stdout) == sets_fallback
    assert 'script-message|anyplay_subtitle_preferences|prefs' in result.stdout
