"""Provider-scoped resume support preserves legacy data and episode aliases."""
from copy import deepcopy

from media_cli.core import storage
from media_cli.state import preferences


def test_resume_save_and_clear_are_isolated_by_provider():
    prefs = {}
    preferences.save_resume_time(prefs, 'same', '1', 45, provider_id='anipm')
    preferences.save_resume_time(prefs, 'same', '1', 90, provider_id='senshi')
    assert preferences.resume_time(prefs, 'same', '1', provider_id='anipm') == 45
    assert preferences.resume_time(prefs, 'same', '1', provider_id='senshi') == 90
    preferences.save_resume_time(prefs, 'same', '1', 0, provider_id='anipm')
    assert preferences.resume_time(prefs, 'same', '1', provider_id='anipm') == 0
    assert preferences.resume_time(prefs, 'same', '1', provider_id='senshi') == 90


def test_scoped_resume_never_adopts_or_mutates_unowned_legacy_data():
    prefs = {'same': {'resumes': {'1': 20}, 'sync_enabled': True}}
    legacy = deepcopy(prefs)
    assert preferences.resume_time(prefs, 'same', '1', provider_id='anipm') == 0
    assert prefs == legacy
    preferences.save_resume_time(prefs, 'same', '1', 45, provider_id='anipm')
    assert prefs['same'] == legacy['same']
    assert preferences.resume_time(prefs, 'same', '1') == 20
    preferences.save_resume_time(prefs, 'same', '1', 25)
    assert preferences.resume_time(prefs, 'same', '1') == 25
    assert preferences.resume_time(prefs, 'same', '1', provider_id='anipm') == 45


def test_resume_and_mirror_fields_share_scope_without_erasing_each_other():
    prefs = {}
    preferences.toggle_preferred_mirror(prefs, 'same', 'Mirror', '720p', provider_id='anidb')
    preferences.save_resume_time(prefs, 'same', 'Episode 31 [463]', 56, provider_id='AniDBApp')
    assert preferences.resume_time(prefs, 'same', '31', provider_id='anidb') == 56
    preferences.save_resume_time(prefs, 'same', 'Episode 31 [463]', 0, provider_id='anidb')
    assert preferences.resume_time(prefs, 'same', '31', provider_id='anidb') == 0
    assert preferences.preferred_mirror(prefs, 'same', provider_id='anidb')['source_name'] == 'Mirror'


def test_storage_resume_api_preserves_provider_context(monkeypatch):
    prefs = {}
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    storage.save_resume_time('same', '1', 45, provider_id='anipm')
    assert storage.get_resume_time('same', '1', provider_id='anipm') == 45
    assert storage.get_resume_time('same', '1', provider_id='senshi') == 0
