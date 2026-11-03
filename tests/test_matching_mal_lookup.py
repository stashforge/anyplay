"""Use exact MAL identity before the title-search fallback when linking a show."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from media_cli.domain import matching


@pytest.fixture
def lookups(monkeypatch):
    from media_cli.core import anilist, storage

    stored = Mock(return_value={})
    direct_al = Mock(return_value=None)
    direct_mal = Mock(return_value=[])
    search = Mock(return_value=[])
    save = Mock()
    monkeypatch.setattr(storage, 'get_source_anilist_match', stored)
    monkeypatch.setattr(storage, 'save_source_anilist_match', save)
    monkeypatch.setattr(anilist, 'fetch_anilist_media', direct_al)
    monkeypatch.setattr(anilist, 'fetch_anilist_by_ids', direct_mal)
    monkeypatch.setattr(anilist, 'search_anilist', search)
    return direct_al, direct_mal, search, save


@pytest.mark.parametrize('field,value', [('malId', 222), ('malId', '222'), ('myanimelist_id', '222')])
def test_exact_mal_lookup_links_different_anilist_id_without_title_search(lookups, field, value):
    direct_al, direct_mal, search, save = lookups
    show = {'_id': 'provider-local', '_provider': 'anipm', 'name': 'Provider title', field: value}
    media = {'_id': '111', 'malId': 222, 'name': 'Different tracker title'}
    direct_mal.return_value = [media]

    result = matching.match_provider_show_to_anilist(None, None, show, 'token')

    direct_mal.assert_called_once_with('token', mal_ids=[222])
    direct_al.assert_not_called()
    search.assert_not_called()
    assert result['aniListId'] == '111'
    assert result['malId'] == 222
    assert result['_id'] == 'provider-local'
    assert result['_provider'] == 'anipm'
    assert result['anilistMatch']['source'] == 'id'
    assert save.call_count == 1


@pytest.mark.parametrize('response', [[], [{'_id': '111', 'malId': 333, 'name': 'Other'}]])
def test_absent_or_wrong_mal_response_falls_back_without_saving(lookups, response):
    _, direct_mal, search, save = lookups
    direct_mal.return_value = response
    show = {'_id': 'local', '_provider': 'anipm', 'name': 'Example', 'malId': 222}
    before = deepcopy(show)
    assert matching.match_provider_show_to_anilist(None, None, show, '') is None
    search.assert_called_once_with('', 'Example')
    save.assert_not_called()
    assert show == before


def test_mal_lookup_error_retains_title_search_fallback(lookups):
    _, direct_mal, search, save = lookups
    direct_mal.side_effect = RuntimeError('offline')
    show = {'name': 'Example', 'malId': 222}
    assert matching.match_provider_show_to_anilist(None, None, show, '') is None
    search.assert_called_once_with('', 'Example')
    save.assert_not_called()


@pytest.mark.parametrize('value', [None, '', 0, -1, True, 'bad', '222.5'])
def test_invalid_mal_id_does_not_trigger_id_request(lookups, value):
    _, direct_mal, search, _ = lookups
    show = {'name': 'Example', 'malId': value}
    assert matching.match_provider_show_to_anilist(None, None, show, '') is None
    direct_mal.assert_not_called()
    search.assert_called_once_with('', 'Example')


def test_successful_anilist_id_lookup_does_not_also_request_mal(lookups):
    direct_al, direct_mal, search, _ = lookups
    direct_al.return_value = {'id': 111, 'idMal': 222, 'title': {'romaji': 'Example'}}
    show = {'name': 'Example', 'aniListId': '111', 'malId': 222}
    assert matching.match_provider_show_to_anilist(None, None, show, '')['aniListId'] == '111'
    direct_mal.assert_not_called()
    search.assert_not_called()


def test_mal_response_cannot_override_known_conflicting_anilist_id(lookups):
    _, direct_mal, _, save = lookups
    direct_mal.return_value = [{'_id': '999', 'malId': 222, 'name': 'Other'}]
    show = {'name': 'Example', 'aniListId': '111', 'malId': 222}
    assert matching.match_provider_show_to_anilist(None, None, show, '') is None
    assert show['aniListId'] == '111'
    save.assert_not_called()
