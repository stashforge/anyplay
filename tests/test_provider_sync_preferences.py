"""Provider-local sync keys cannot enable another anime through an ID collision."""
from copy import deepcopy
from unittest.mock import Mock

import pytest
from media_cli.core import storage
from media_cli.state import preferences


def show(provider, tracker=None):
    title = {'_id': 'same', '_provider': provider, 'name': provider}
    if tracker:
        title['aniListId'] = tracker
    return title


def test_sync_toggle_isolates_provider_ids():
    prefs = {}
    preferences.set_title_sync(prefs, show('anipm', '111'), True)
    assert preferences.title_sync_preference(prefs, show('anipm', '111')) is True
    assert preferences.title_sync_preference(prefs, show('senshi', '222')) is None
    preferences.set_title_sync(prefs, show('senshi', '222'), False)
    assert preferences.title_sync_preference(prefs, show('anipm', '111')) is True


def test_same_anilist_title_still_shares_sync_fallback():
    prefs = {}
    preferences.set_title_sync(prefs, show('anipm', '111'), True)
    assert preferences.title_sync_preference(prefs, show('senshi', '111')) is True
    preferences.set_title_sync(prefs, show('senshi', '111'), False)
    assert preferences.title_sync_preference(prefs, show('senshi', '111')) is False
    assert preferences.title_sync_preference(prefs, show('anipm', '111')) is True


@pytest.mark.parametrize('enabled', [False, True])
def test_legacy_sync_migrates_only_to_unique_history_owner(monkeypatch, enabled):
    title = show('anipm')
    prefs = {'same': {'sync_enabled': enabled}}
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'load_history', lambda: [{'show': title}])
    assert storage.get_title_sync_preference(title) is enabled
    assert storage.get_title_sync_preference(show('senshi')) is None
    assert prefs['same']['sync_enabled'] is enabled


def test_batch_sync_migration_uses_one_history_snapshot_and_one_save(monkeypatch):
    from media_cli.context import FLAGS
    from media_cli.domain.metadata import batch_prepare_shows_display_state
    titles = [show('anipm'), {'_id': 'other', '_provider': 'senshi', 'name': 'Other'}]
    history = [{'show': title, 'episode': '0'} for title in deepcopy(titles)]
    prefs = {'same': {'sync_enabled': True}, 'other': {'sync_enabled': False}}
    read_history, read_prefs, save = Mock(return_value=history), Mock(return_value=prefs), Mock()
    monkeypatch.setattr(storage, 'load_history', read_history)
    monkeypatch.setattr(storage, 'load_prefs', read_prefs)
    monkeypatch.setattr(storage, 'save_prefs', save)
    for key in ('incognito_mode', 'sync_force_on', 'sync_force_off'):
        monkeypatch.setattr(FLAGS, key, False)
    batch_prepare_shows_display_state(titles)
    assert [title['_sync_enabled'] for title in titles] == [True, False]
    assert storage.get_title_sync_preference(titles[0]) is True
    # Exclude the explicit individual read above when counting batch work.
    assert read_history.call_count == 1
    assert read_prefs.call_count == 2
    assert save.call_count == 1
    batch_prepare_shows_display_state(titles)
    assert save.call_count == 1


def test_ambiguous_legacy_sync_is_preserved_without_assignment(monkeypatch):
    prefs = {'same': {'sync_enabled': True}}
    original = deepcopy(prefs)
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'load_history', lambda: [{'show': show(p)} for p in ('anipm', 'senshi')])
    assert storage.get_title_sync_preference(show('anipm')) is None
    assert storage.get_title_sync_preference(show('senshi')) is None
    assert prefs == original


def test_storage_toggle_and_batch_read_use_the_same_scoped_setting(monkeypatch):
    title = show('anipm')
    prefs = {'same': {'sync_enabled': False, 'resumes': {'1': 45}}}
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'load_history', lambda: [{'show': title}])
    monkeypatch.setattr(storage, 'is_incognito', lambda: False)
    storage.set_title_sync(title, True)
    assert storage.get_title_sync_preference(title) is True
    assert preferences.title_sync_preference(prefs, title) is True
    assert storage.get_title_sync_preference(show('senshi')) is None
    assert prefs['same']['sync_enabled'] is False
    assert prefs['same']['resumes'] == {'1': 45}
