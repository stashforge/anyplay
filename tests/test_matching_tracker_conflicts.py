"""Explicit tracker contradictions must not be rescued by title similarity."""

import pytest

from media_cli.domain import matching


@pytest.mark.parametrize('left_key,right_key,reason', [
    ('aniListId', 'aniListId', 'anilist_id'),
    ('aniListId', '_anilist_id', 'anilist_id'),
    ('_anilist_id', 'aniListId', 'anilist_id'),
    ('malId', 'malId', 'mal_id'),
    ('malId', 'myanimelist_id', 'mal_id'),
    ('myanimelist_id', 'malId', 'mal_id'),
])
def test_same_title_with_conflicting_tracker_ids_is_not_chosen(left_key, right_key, reason):
    reference = {'name': 'Example Anime', left_key: '111'}
    candidate = {'name': 'Example Anime', right_key: 222}
    assert matching.match_score_details(reference, candidate)['contradiction'] == reason
    assert matching.choose_confident_match(reference, [candidate]) is None


def test_matching_anilist_id_does_not_cancel_conflicting_mal_id():
    reference = {'name': 'Example Anime', 'aniListId': '111', 'malId': 222}
    candidate = {'name': 'Example Anime', 'aniListId': 111, 'malId': '333'}
    assert matching.choose_confident_match(reference, [candidate]) is None


def test_conflicting_candidate_cannot_hide_compatible_candidate():
    reference = {'name': 'Example Anime', 'aniListId': 111}
    wrong = {'name': 'Example Anime', 'aniListId': 222, 'year': 2023}
    compatible = {'name': 'Example Anime', 'aniListId': '111'}
    assert matching.choose_confident_match(reference, [wrong, compatible]) is compatible


@pytest.mark.parametrize('candidate', [
    {'aniListId': 111, 'malId': '222'},
    {'aniListId': '00111', 'malId': 222},
    {'malId': 222},
    {},
])
def test_matching_or_missing_tracker_ids_preserve_title_matching(candidate):
    reference = {'name': 'Example Anime', 'aniListId': '111', 'malId': '222'}
    candidate = {'name': 'Example Anime', **candidate}
    assert matching.choose_confident_match(reference, [candidate]) is candidate


def test_provider_local_ids_are_not_compared_as_tracker_ids():
    reference = {'name': 'Example Anime', '_id': '111', '_provider': 'anipm'}
    candidate = {'name': 'Example Anime', '_id': '222', '_provider': 'senshi'}
    assert matching.choose_confident_match(reference, [candidate]) is candidate
