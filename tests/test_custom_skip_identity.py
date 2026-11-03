"""Custom skips share verified tracker identity, never unrelated raw keys."""
from copy import deepcopy

from media_cli.core import storage
from media_cli.media import custom_skips
from media_cli.state import preferences

ENTRY = {'providers': ['all'], 'exclude_providers': [],
         'intervals': [{'type': 'op', 'label': 'Opening', 'start': 0.0, 'end': 90.0}]}


def show(provider, **extra):
    return {'_id': 'same', '_provider': provider, 'name': 'Same name', **extra}


def test_save_read_and_clear_do_not_cross_unrelated_provider_ids():
    prefs = {}
    first, other = show('anipm', aniListId='111'), show('senshi', aniListId='222')
    preferences.set_custom_skips(prefs, first, 'same', ENTRY)
    assert preferences.custom_skips(prefs, first, 'same') == [ENTRY]
    assert preferences.custom_skips(prefs, other, 'same') == []
    preferences.clear_custom_skips(prefs, other, 'same')
    assert preferences.custom_skips(prefs, first, 'same') == [ENTRY]


def test_tracker_namespaces_cannot_collide_with_provider_or_other_tracker_ids():
    prefs = {}
    first = show('anipm', aniListId='123')
    preferences.set_custom_skips(prefs, first, 'same', ENTRY)
    assert preferences.custom_skips(prefs, {'_id': '123', '_provider': 'senshi'}, '123') == []
    assert preferences.custom_skips(prefs, {'malId': '123'}, None) == []
    assert preferences.custom_skips(prefs, {'aniListId': '123'}, None) == [ENTRY]


def test_session_and_disable_keys_are_isolated_but_verified_tracker_can_share():
    first, other = show('anipm'), show('senshi')
    keys = custom_skips.stable_title_keys(first, 'same')
    sessions = {key: [ENTRY] for key in keys}
    assert custom_skips.resolve_custom_skips(show=other, fallback_show_id='same', provider_id='senshi',
                                           session_store=sessions, saved_entries=[]) is None
    first['aniListId'] = other['aniListId'] = '123'
    sessions = {key: [ENTRY] for key in custom_skips.stable_title_keys(first, 'same')}
    assert custom_skips.resolve_custom_skips(show=other, fallback_show_id='same', provider_id='senshi',
                                           session_store=sessions, saved_entries=[])['mode'] == 'session'


def test_legacy_custom_skips_migrate_only_with_unique_history_owner(monkeypatch):
    prefs = {'same': {'custom_skips': [deepcopy(ENTRY)]}}
    first = show('anipm')
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'load_history', lambda: [{'show': first}])
    assert storage.get_custom_skips(first, 'same') == [ENTRY]
    assert storage.get_custom_skips(show('senshi'), 'same') == []
    storage.clear_custom_skips(first, 'same')
    assert storage.get_custom_skips(first, 'same') == []
    assert prefs['same']['custom_skips'] == [ENTRY]


def test_identity_helper_failure_cannot_fall_back_to_bare_ids(monkeypatch):
    prefs = {'same': {'custom_skips': [deepcopy(ENTRY)]}}
    original = deepcopy(prefs)
    def unavailable(*args):
        raise RuntimeError('unavailable')
    monkeypatch.setattr(custom_skips, 'stable_title_keys', unavailable)
    assert preferences.custom_skips(prefs, show('senshi'), 'same') == []
    preferences.clear_custom_skips(prefs, show('senshi'), 'same')
    assert prefs == original


def test_same_name_is_not_identity_but_matching_mal_link_can_share():
    prefs = {}
    first = show('anipm', malId='123')
    other = {'_id': 'other', '_provider': 'anipm', 'name': first['name']}
    preferences.set_custom_skips(prefs, first, 'same', ENTRY)
    assert preferences.custom_skips(prefs, other, 'other') == []
    other['malId'] = '123'
    assert preferences.custom_skips(prefs, other, 'other') == [ENTRY]


def test_ambiguous_legacy_custom_skips_are_not_adopted(monkeypatch):
    prefs = {'same': {'custom_skips': [deepcopy(ENTRY)]}}
    original = deepcopy(prefs)
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'load_history', lambda: [{'show': show(p)} for p in ('anipm', 'senshi')])
    assert storage.get_custom_skips(show('anipm'), 'same') == []
    assert storage.get_custom_skips(show('senshi'), 'same') == []
    assert prefs == original
