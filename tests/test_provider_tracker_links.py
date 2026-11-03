"""Stored tracker links keep provider identity in both directions."""
from copy import deepcopy

from media_cli.state import preferences
from media_cli.core import storage


def show(provider, sid='same'):
    return {'_id': sid, '_provider': provider, 'name': provider}


def test_equal_ids_keep_independent_tracker_links():
    prefs = {}
    preferences.save_anilist_match(prefs, '111', show('anipm'))
    preferences.save_anilist_match(prefs, '222', show('senshi'))
    assert preferences.source_anilist_match(prefs, 'same', 'anipm')['_id'] == '111'
    assert preferences.source_anilist_match(prefs, 'same', 'senshi')['_id'] == '222'
    preferences.clear_anilist_match(prefs, '111')
    assert preferences.source_anilist_match(prefs, 'same', 'anipm') == {}
    assert preferences.source_anilist_match(prefs, 'same', 'senshi')['_id'] == '222'


def test_switching_same_anilist_match_across_equal_provider_ids_clears_old_reverse():
    prefs = {}
    preferences.save_anilist_match(prefs, '111', show('anipm'))
    preferences.save_anilist_match(prefs, '111', show('senshi'))
    assert preferences.source_anilist_match(prefs, 'same', 'anipm') == {}
    assert preferences.source_anilist_match(prefs, 'same', 'senshi')['_id'] == '111'
    assert preferences.anilist_match(prefs, '111')['_provider'] == 'senshi'


def test_rebinding_provider_title_removes_only_its_previous_forward_link():
    prefs = {}
    preferences.save_anilist_match(prefs, '111', show('anipm'))
    preferences.save_anilist_match(prefs, '222', show('anipm'))
    assert preferences.anilist_match(prefs, '111') == {}
    preferences.clear_anilist_match(prefs, '111')
    assert preferences.source_anilist_match(prefs, 'same', 'anipm')['_id'] == '222'


def test_legacy_reverse_is_read_only_with_matching_forward_owner():
    prefs = {'same': {'anilist_match': {'_id': '111'}},
             'al:111': {'al_match': show('anipm')}}
    original = deepcopy(prefs)
    assert preferences.source_anilist_match(prefs, 'same', 'anipm')['_id'] == '111'
    assert preferences.source_anilist_match(prefs, 'same', 'senshi') == {}
    assert prefs == original


def test_batch_lookup_keeps_mixed_providers_in_one_snapshot(monkeypatch):
    prefs = {}
    preferences.save_anilist_match(prefs, '111', show('anipm'))
    preferences.save_anilist_match(prefs, '222', show('senshi'))
    reads = []
    monkeypatch.setattr(storage, 'load_prefs', lambda: reads.append(True) or prefs)
    result = storage.get_source_anilist_matches([show('anipm'), show('senshi')])
    assert result[preferences._provider_title_key('same', 'anipm')]['_id'] == '111'
    assert result[preferences._provider_title_key('same', 'senshi')]['_id'] == '222'
    assert len(reads) == 1


def test_clearing_stale_forward_pointer_preserves_newer_reverse_and_other_fields():
    key = preferences._provider_title_key('same', 'anipm')
    prefs = {'al:111': {'al_match': show('anipm'), 'sync_enabled': True},
             key: {'anilist_match': {'_id': '222'}, 'resumes': {'1': 45}}}
    preferences.clear_anilist_match(prefs, '111')
    assert prefs[key]['anilist_match']['_id'] == '222'
    assert prefs[key]['resumes']['1'] == 45
    assert prefs['al:111'] == {'sync_enabled': True}


def test_fast_enrichment_does_not_mix_equal_ids_from_different_providers(monkeypatch):
    from media_cli.core import enrichment
    prefs = {}
    preferences.save_anilist_match(prefs, '111', show('anipm'))
    preferences.save_anilist_match(prefs, '222', show('senshi'))
    monkeypatch.setattr(storage, 'load_prefs', lambda: prefs)
    monkeypatch.setattr(storage, 'save_prefs', lambda value: None)
    monkeypatch.setattr(storage, 'is_incognito', lambda: False)
    results = [show('anipm'), show('senshi')]
    enrichment.enrich_provider_results_fast(results, [
        {'_id': '111', 'name': 'Alpha'}, {'_id': '222', 'name': 'Zeta'}])
    assert [row['aniListId'] for row in results] == ['111', '222']
    assert [row['_match_source'] for row in results] == ['stored', 'stored']
