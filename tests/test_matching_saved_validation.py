"""Automatic cached links must not override current provider tracker identity."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from media_cli.domain import matching


@pytest.fixture
def lookup(monkeypatch):
    from media_cli.core import anilist, storage
    saved = Mock(return_value={})
    fetch = Mock(return_value=None)
    mal = Mock(return_value=[])
    search = Mock(return_value=[])
    save = Mock()
    monkeypatch.setattr(storage, 'get_source_anilist_match', saved)
    monkeypatch.setattr(storage, 'save_source_anilist_match', save)
    monkeypatch.setattr(anilist, 'fetch_anilist_media', fetch)
    monkeypatch.setattr(anilist, 'fetch_anilist_by_ids', mal)
    monkeypatch.setattr(anilist, 'search_anilist', search)
    return saved, fetch, mal, search, save


def media(al_id, mal_id):
    return {'id': al_id, 'idMal': mal_id, 'title': {'romaji': 'Example'}}


@pytest.mark.parametrize('source', ['fuzzy', 'id', 'auto', 'title', 'stored', 'unknown', None])
def test_stale_automatic_link_is_skipped_before_fetch(lookup, source):
    saved, fetch, _, search, save = lookup
    saved.return_value = {'_id': '111', 'match_source': source}
    fetch.side_effect = lambda token, sid: media(int(sid), 222)
    show = {'_id': 'local', '_provider': 'senshi', 'name': 'Example', 'aniListId': '333'}
    result = matching.match_provider_show_to_anilist(None, None, show, '')
    assert result['aniListId'] == '333'
    fetch.assert_called_once_with('', '333')
    search.assert_not_called()
    assert save.call_args.args[1]['_id'] == '333'


@pytest.mark.parametrize('al_field', ['aniListId', '_anilist_id'])
def test_rejected_saved_match_does_not_mutate_show_when_lookup_fails(lookup, al_field):
    saved, fetch, _, _, save = lookup
    saved.return_value = {'_id': '111', 'match_source': 'fuzzy'}
    fetch.return_value = media(111, 222)
    # Keep direct lookups unavailable so only a stale saved fetch could succeed.
    fetch.side_effect = lambda token, sid: media(111, 222) if sid == '111' else None
    show = {'_id': 'local', 'name': 'Unrelated', al_field: '333'}
    before = deepcopy(show)
    assert matching.match_provider_show_to_anilist(None, None, show, '') is None
    assert show == before
    save.assert_not_called()


@pytest.mark.parametrize('mal_field', ['malId', 'myanimelist_id'])
def test_mal_conflict_in_fetched_saved_entry_uses_direct_mal_lookup(lookup, mal_field):
    saved, fetch, mal, search, save = lookup
    saved.return_value = {'_id': '111', 'match_source': 'fuzzy'}
    fetch.return_value = media(111, 999)
    mal.return_value = [{'_id': '333', 'malId': 222, 'name': 'Example'}]
    show = {'_id': 'local', 'name': 'Example', mal_field: 222}
    result = matching.match_provider_show_to_anilist(None, None, show, '')
    assert result['aniListId'] == '333'
    mal.assert_called_once_with('', mal_ids=[222])
    search.assert_not_called()
    assert save.call_count == 1


def test_explicit_manual_match_is_retained_despite_provider_id_conflict(lookup):
    saved, fetch, mal, search, _ = lookup
    saved.return_value = {'_id': '111', 'match_source': 'manual'}
    fetch.return_value = media(111, 999)
    show = {'_id': 'local', 'name': 'Example', 'aniListId': '333', 'malId': 222}
    result = matching.match_provider_show_to_anilist(None, None, show, '')
    assert result['aniListId'] == '111'
    assert result['anilistMatch']['source'] == 'manual'
    fetch.assert_called_once_with('', '111')
    mal.assert_not_called()
    search.assert_not_called()


@pytest.mark.parametrize('extra', [{}, {'aniListId': '00111'}, {'malId': 222}])
def test_compatible_saved_link_is_reused_without_duplicate_lookup(lookup, extra):
    saved, fetch, mal, search, _ = lookup
    saved.return_value = {'_id': '111', 'match_source': 'id'}
    fetch.return_value = media(111, 222)
    show = {'_id': '987', '_provider': 'senshi', 'name': 'Example', **extra}
    result = matching.match_provider_show_to_anilist(None, None, show, '')
    assert result['aniListId'] == '111'
    assert result['_id'] == '987'
    assert result['anilistMatch']['source'] == 'id'
    fetch.assert_called_once_with('', '111')
    mal.assert_not_called()
    search.assert_not_called()
