"""Search enrichment respects saved manual links and rejects stale automatic ones."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from media_cli.core import enrichment
from media_cli.state.preferences import _provider_title_key


@pytest.fixture
def saved_links(monkeypatch):
    links = {}
    read = Mock(side_effect=lambda sources: links)
    save = Mock()
    monkeypatch.setattr(enrichment, 'get_source_anilist_matches', read)
    monkeypatch.setattr(enrichment, 'save_source_anilist_matches', save)
    return links, read, save


def show(**extra):
    return {'_id': 'local', '_provider': 'senshi', 'name': 'Example', **extra}


def entry(al_id, mal_id):
    return {'_id': str(al_id), 'malId': mal_id, 'name': 'Example', 'description': 'Synopsis'}


@pytest.mark.parametrize('source', ['fuzzy', 'id', 'stored', 'unknown', None])
@pytest.mark.parametrize('identity', [{'aniListId': '333'}, {'malId': 222}])
def test_stale_automatic_link_cannot_be_restored_through_fuzzy_fallback(saved_links, source, identity):
    links, _, save = saved_links
    links[_provider_title_key('local', 'senshi')] = {'_id': '111', 'match_source': source}
    title = show(**identity)
    before = deepcopy(title)
    _, unmatched = enrichment.enrich_provider_results_fast([title], [entry(111, 999)])
    assert title == before
    assert unmatched == [title]
    save.assert_not_called()


@pytest.mark.parametrize('identity', [{'aniListId': '333'}, {'malId': 222}])
def test_manual_saved_link_wins_over_current_direct_id_match(saved_links, identity):
    links, read, save = saved_links
    links[_provider_title_key('local', 'senshi')] = {'_id': '111', 'match_source': 'manual'}
    title = show(**identity)
    _, unmatched = enrichment.enrich_provider_results_fast([title], [entry(111, 999), entry(333, 222)])
    assert title['aniListId'] == '111'
    assert title['anilistMatch']['source'] == 'manual'
    assert title['_id'] == 'local'
    assert unmatched == []
    assert read.call_count == 1
    assert save.call_args.args[0][0][0]['_match_source'] == 'manual'


def test_missing_manual_candidate_is_not_replaced_by_an_automatic_match(saved_links):
    links, _, save = saved_links
    links[_provider_title_key('local', 'senshi')] = {'_id': '111', 'match_source': 'manual'}
    title = show(aniListId='333')
    before = deepcopy(title)
    enrichment.enrich_provider_results_fast([title], [entry(333, 222)])
    assert title == before
    save.assert_not_called()


def test_matching_one_tracker_id_cannot_override_another_conflicting_id(saved_links):
    _, _, save = saved_links
    title = show(aniListId='111', malId=222)
    before = deepcopy(title)
    enrichment.enrich_provider_results_fast([title], [entry(111, 999)])
    assert title == before
    save.assert_not_called()


def test_compatible_stored_link_retains_existing_reuse(saved_links):
    links, read, save = saved_links
    links[_provider_title_key('local', 'senshi')] = {'_id': '111', 'match_source': 'fuzzy'}
    title = show()
    _, unmatched = enrichment.enrich_provider_results_fast([title], [entry(111, 222)])
    assert title['aniListId'] == '111'
    assert title['_match_source'] == 'stored'
    assert unmatched == []
    assert read.call_count == save.call_count == 1


def test_mixed_batch_reads_and_saves_once(saved_links):
    links, read, save = saved_links
    links[_provider_title_key('local', 'senshi')] = {'_id': '111', 'match_source': 'manual'}
    first = show(aniListId='333')
    second = show(_id='other', aniListId='333')
    enrichment.enrich_provider_results_fast([first, second], [entry(111, 999), entry(333, 222)])
    assert [first['aniListId'], second['aniListId']] == ['111', '333']
    assert read.call_count == save.call_count == 1
