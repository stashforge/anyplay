"""Preferred mirrors belong to a provider title, not a globally unique raw ID."""
from copy import deepcopy
from media_cli.state import preferences
from media_cli.core import storage


def test_same_title_id_keeps_provider_mirrors_independent():
    prefs = {}
    preferences.toggle_preferred_mirror(prefs, 'same', 'First', '1080p', provider_id='anipm')
    preferences.toggle_preferred_mirror(prefs, 'same', 'Second', '720p', provider_id='senshi')
    assert preferences.preferred_mirror(prefs, 'same', provider_id='anipm')['source_name'] == 'First'
    assert preferences.preferred_mirror(prefs, 'same', provider_id='senshi')['source_name'] == 'Second'
    preferences.toggle_preferred_mirror(prefs, 'same', 'First', '1080p', provider_id='anipm')
    assert preferences.preferred_mirror(prefs, 'same', provider_id='anipm') == {}
    assert preferences.preferred_mirror(prefs, 'same', provider_id='senshi')['source_name'] == 'Second'


def test_legacy_unowned_preferences_are_preserved_but_not_guessed():
    prefs = {'same': {'source_name': 'Legacy', 'resolution': '720p', 'resumes': {'1': 42}}}
    original = deepcopy(prefs)
    assert preferences.preferred_mirror(prefs, 'same', provider_id='anipm') == {}
    assert preferences.preferred_mirror(prefs, 'same', provider_id='senshi') == {}
    assert prefs == original
    preferences.toggle_preferred_mirror(prefs, 'same', 'New', '1080p', provider_id='anipm')
    assert prefs['same'] == original['same']
    assert preferences.preferred_mirror(prefs, 'same')['source_name'] == 'Legacy'


def test_provider_case_and_supported_alias_use_same_preference():
    prefs = {}
    preferences.toggle_preferred_mirror(prefs, 'same', 'Mirror', '720p', provider_id='AniDBApp')
    assert preferences.preferred_mirror(prefs, 'same', provider_id='anidb')['source_name'] == 'Mirror'


def test_storage_forwards_provider_identity(monkeypatch):
    prefs = {}
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    storage.toggle_preferred_mirror('same', 'Mirror', '720p', provider_id='anipm')
    assert storage.get_preferred_mirror('same', provider_id='anipm')['source_name'] == 'Mirror'
    assert storage.get_preferred_mirror('same', provider_id='senshi') == {}


def test_desktop_preference_callback_is_bound_to_original_provider(monkeypatch):
    from media_cli.playback import engine
    calls = []
    monkeypatch.setitem(engine._player_ui_state, 'show', {'_provider': 'anipm'})
    monkeypatch.setattr(engine, 'get_preferred_mirror', lambda sid, pid: calls.append((sid, pid)) or {})

    def player(*args, **kwargs):
        # UI navigation cannot change the ownership of this playback callback.
        engine._player_ui_state['show'] = {'_provider': 'senshi'}
        kwargs['get_preferred_mirror']('same')
        return 'played'

    monkeypatch.setattr(engine.desktop_playback, 'play_desktop', player)
    assert engine.play_desktop('Example', '1', {}, show_id='same') == 'played'
    assert calls == [('same', 'anipm')]


def test_foreground_resolution_uses_selected_providers_preference(monkeypatch):
    from media_cli.core import streams
    prefs = {}
    for provider, source in [('anipm', 'First'), ('senshi', 'Second')]:
        preferences.toggle_preferred_mirror(prefs, 'same', source, '720p', provider_id=provider)
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(streams, 'get_preferred_mirror', storage.get_preferred_mirror)
    monkeypatch.setattr(streams, 'expand_direct_sources', lambda sources: sources)
    monkeypatch.setattr(streams, 'resolve_source', lambda source: [
        {'source_name': source['sourceName'], 'link': 'https://example.test/' + source['sourceName'],
         'resolution': '720p', 'type': 'mp4'}])
    for provider, wanted in [('anipm', 'First'), ('senshi', 'Second')]:
        result = streams.fetch_episode_stream('same', '1', provider_id=provider, quality='720p', ep_data={
            'episode': {'sourceUrls': [{'sourceName': 'First'}, {'sourceName': 'Second'}]}})
        assert result[0]['source_name'] == wanted
