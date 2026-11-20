from media_cli.domain.matching import _cached_title_match_score, title_match_score


def test_large_list_text_comparisons_survive_a_second_pass():
    _cached_title_match_score.cache_clear()
    pairs=[((f'anime {i}',),('library reference',)) for i in range(604)]
    for left,right in pairs:
        title_match_score(left,right)
    before=_cached_title_match_score.cache_info()
    for left,right in pairs:
        title_match_score(left,right)
    after=_cached_title_match_score.cache_info()
    assert after.hits-before.hits==604
    assert after.misses==before.misses
    title_match_score(('changed title',),('library reference',))
    assert _cached_title_match_score.cache_info().misses==after.misses+1
    _cached_title_match_score.cache_clear()
