"""Automatic matching needs a clear winner even when a title is exact."""

import pytest

from media_cli.domain import matching


@pytest.mark.parametrize('reverse', [False, True])
def test_exact_title_tie_is_not_decided_by_result_order(reverse):
    reference = {'name': 'Example Anime'}
    candidates = [dict(reference, _id='one'), dict(reference, _id='two')]
    if reverse:
        candidates.reverse()
    assert matching.choose_confident_match(reference, candidates) is None


def test_small_metadata_advantage_does_not_resolve_exact_title_ambiguity():
    reference = {'name': 'Example Anime', 'season': {'year': 2023, 'quarter': 'Spring'}}
    first = {'name': 'Example Anime', 'year': 2023}
    second = dict(reference)
    assert matching.choose_confident_match(reference, [first, second]) is None


@pytest.mark.parametrize('title_field', ['englishName', 'romajiName', 'nativeName', 'altNames'])
def test_alternate_title_match_still_requires_a_clear_winner(title_field):
    value = ['Example Anime'] if title_field == 'altNames' else 'Example Anime'
    reference = {'name': 'Example Anime'}
    first = {'name': 'Other spelling', title_field: value, '_id': 'one'}
    second = dict(first, _id='two')
    assert matching.choose_confident_match(reference, [first, second]) is None
    assert matching.choose_confident_match(reference, [first]) is first


@pytest.mark.parametrize('metadata,reason', [
    ({'airedStart': {'year': 2020}}, 'year'),
    ({'season': 2}, 'season'),
    ({'part': 2}, 'part'),
    ({'format': 'MOVIE'}, 'type'),
    ({'episodeCount': 24}, 'episodes'),
])
def test_metadata_conflicts_still_reject_same_title_candidate(metadata, reason):
    reference = {'name': 'Example Anime', 'year': 2023, 'season': 1,
                 'part': 1, 'format': 'TV', 'episodeCount': 12}
    wrong = dict(reference, **metadata)
    right = dict(reference)
    assert matching.match_score_details(reference, wrong)['contradiction'] == reason
    assert matching.choose_confident_match(reference, [wrong, right]) is right


def test_release_year_can_distinguish_candidates_with_english_and_romaji_titles():
    reference = {'name': 'English Name', 'romajiName': 'Romaji no Namae',
                 'airedStart': {'year': 2023}}
    sparse = {'name': 'Romaji no Namae'}
    supported = {'name': 'Different spelling', 'englishName': 'English Name', 'year': 2023}
    assert matching.choose_confident_match(reference, [sparse, supported]) is supported


def test_format_support_can_resolve_exact_title_ambiguity():
    reference = {'name': 'Example Anime', 'format': 'TV'}
    sparse = {'name': 'Example Anime'}
    supported = dict(reference)
    assert matching.choose_confident_match(reference, [sparse, supported]) is supported


@pytest.mark.parametrize('ambiguous', [True, False])
def test_info_enrichment_respects_match_confidence(monkeypatch, ambiguous):
    from types import SimpleNamespace
    from media_cli.core import enrichment

    show = {'_id': 'local', '_provider': 'anipm', 'name': 'Example Anime'}
    first = {'_id': '111', 'name': 'Example Anime', 'description': 'First synopsis',
             'genres': ['Drama']}
    second = dict(first, _id='222', description='Other synopsis')
    candidates = [first, second] if ambiguous else [first]
    monkeypatch.setattr(enrichment, '_persist_enrichment_state', lambda *args: None)
    monkeypatch.setattr('media_cli.core.storage.patch_history_entry_show', lambda *args, **kwargs: None)
    monkeypatch.setattr('media_cli.core.storage.load_config', lambda: {})
    monkeypatch.setattr('media_cli.providers.get_provider', lambda *args: SimpleNamespace())
    monkeypatch.setattr('media_cli.core.anilist.search_anilist', lambda *args: candidates)

    assert enrichment.enrich_show_if_missing(show) is (not ambiguous)
    if ambiguous:
        assert not show.get('description')
        assert not show.get('aniListId')
    else:
        assert show['description'] == 'First synopsis'
