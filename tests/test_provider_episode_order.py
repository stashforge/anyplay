"""Episode order shares provider/title scope without touching playback data."""
from copy import deepcopy
from media_cli.core import storage
from media_cli.state import preferences


def test_provider_order_toggles_independently():
    prefs = {}
    assert preferences.toggle_episode_order(prefs, 'same', 'asc', 'anipm') == 'desc'
    assert preferences.episode_order(prefs, 'same', 'asc', 'senshi') == 'asc'
    assert preferences.toggle_episode_order(prefs, 'same', 'asc', 'senshi') == 'desc'
    assert preferences.toggle_episode_order(prefs, 'same', 'asc', 'anipm') == 'asc'
    assert preferences.episode_order(prefs, 'same', 'asc', 'senshi') == 'desc'


def test_order_preserves_resume_and_mirror_in_same_scope():
    prefs = {}
    preferences.save_resume_time(prefs, 'same', '1', 45, 'anidb')
    preferences.toggle_preferred_mirror(prefs, 'same', 'Mirror', '720p', 'anidb')
    preferences.toggle_episode_order(prefs, 'same', 'asc', 'AniDBApp')
    assert preferences.episode_order(prefs, 'same', 'asc', 'anidb') == 'desc'
    assert preferences.resume_time(prefs, 'same', '1', 'anidb') == 45
    assert preferences.preferred_mirror(prefs, 'same', 'anidb')['source_name'] == 'Mirror'


def test_storage_keeps_uniquely_owned_legacy_order(monkeypatch):
    prefs = {'same': {'episode_order': 'desc', 'resumes': {'1': 45}}}
    history = [{'show': {'_id': 'same', '_provider': 'anipm'}}]
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'load_history', lambda: history)
    assert storage.get_episode_order('same', 'asc', 'anipm') == 'desc'
    assert storage.toggle_episode_order('same', 'asc', 'anipm') == 'asc'
    assert storage.get_episode_order('same', 'asc', 'senshi') == 'asc'
    assert prefs['same']['episode_order'] == 'desc'
    assert storage.get_resume_time('same', '1', 'anipm') == 45
    history[:] = [{'show': {'_id': 'same', '_provider': 'senshi'}}]
    assert storage.get_episode_order('same', 'asc', 'senshi') == 'asc'


def test_ambiguous_legacy_order_is_not_assigned(monkeypatch):
    prefs = {'same': {'episode_order': 'desc'}}
    history = [{'show': {'_id': 'same', '_provider': p}} for p in ('anipm', 'senshi')]
    original = deepcopy(prefs)
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'load_history', lambda: history)
    assert storage.get_episode_order('same', 'asc', 'anipm') == 'asc'
    assert storage.get_episode_order('same', 'asc', 'senshi') == 'asc'
    assert prefs == original
    assert storage.get_episode_order('same', 'asc') == 'desc'


def test_order_does_not_reassign_a_previously_established_resume_owner(monkeypatch):
    prefs = {'same': {'episode_order': 'desc', 'resumes': {'1': 45}}}
    history = [{'show': {'_id': 'same', '_provider': 'anipm'}}]
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'load_history', lambda: history)
    assert storage.get_resume_time('same', '1', 'anipm') == 45
    history[:] = [{'show': {'_id': 'same', '_provider': 'senshi'}}]
    assert storage.get_episode_order('same', 'asc', 'senshi') == 'asc'
    assert prefs['same']['episode_order'] == 'desc'
