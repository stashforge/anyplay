"""Reuse pure text comparisons without caching mutable matching decisions."""

from unittest.mock import Mock

import pytest

from media_cli.domain import matching


@pytest.fixture(autouse=True)
def isolated_score_cache():
    matching._cached_title_match_score.cache_clear()
    yield
    matching._cached_title_match_score.cache_clear()


def test_repeated_title_scores_reuse_similarity_work(monkeypatch):
    compare = Mock(wraps=matching.difflib.SequenceMatcher)
    monkeypatch.setattr(matching.difflib, 'SequenceMatcher', compare)
    left, right = ['cache regression title alpha'], ['cache regression title beta']
    expected = matching.title_match_score(left, right)
    calls = compare.call_count
    assert calls > 0
    assert matching.title_match_score(tuple(left), tuple(right)) == expected
    assert compare.call_count == calls
    right[:] = left
    assert matching.title_match_score(left, right) == (30, True)


@pytest.mark.parametrize('field,value,contradiction', [
    ('year', 2010, 'year'), ('season', 2, 'season'), ('part', 2, 'part'),
])
def test_metadata_changes_are_checked_after_title_score_is_cached(field, value, contradiction):
    first = dict(name='Cache metadata title', year=2023, season=1, part=1)
    second = first.copy()
    before = matching.match_score_details(first, second)
    assert not before['contradiction']
    second[field] = value
    assert matching.match_score_details(first, second)['contradiction'] == contradiction


def test_title_score_cache_has_a_fixed_size_limit():
    matching._cached_title_match_score.cache_clear()
    for i in range(520):
        matching.title_match_score([f'bounded score {i}'], [f'bounded score {i}'])
    assert matching._cached_title_match_score.cache_info().currsize == 512
