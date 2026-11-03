"""Detail health requires evidence beyond a possibly synthesized title."""

import pytest

from media_cli.health.details import assess_details


def test_animegg_empty_fetch_is_inconclusive_after_real_normalization(monkeypatch):
    from media_cli.providers.animegg import AnimeGG

    provider = AnimeGG()
    monkeypatch.setattr(provider, '_fetch_html', lambda url: '')
    payload = provider.get_title('death-note')

    assert payload['name'] == 'Death Note'
    assert payload['status'] == ''
    assert not payload['thumbnail'] and not payload['description']
    result = assess_details(payload, 1535)
    assert result['status'] == 'inconclusive'
    assert result['present_fields'] == ['title']
    assert 'title-only' in result['reason'].lower()
    assert 'placeholder' not in result['reason'].lower()


@pytest.mark.parametrize('extra', [
    {}, {'_id': 20}, {'_provider_id': 'death-note', 'url': 'https://example.com/death-note'},
    {'romajiName': 'Death Note', 'status': 'UNKNOWN', 'format': 'UNKNOWN'},
])
def test_unproven_title_only_payload_does_not_claim_retrieval(extra):
    result = assess_details({'name': 'Death Note', **extra}, 1535)
    assert result['status'] == 'inconclusive'
    assert result['present_fields'] == ['title']
    assert 'title-only' in result['reason'].lower()
    assert 'not verified' in result['reason'].lower()
    assert 'placeholder' not in result['reason'].lower()


@pytest.mark.parametrize('extra', [{}, {
    'thumbnail': 'https://example.com/cover.jpg', 'description': 'A synopsis', 'malId': 1535,
}])
def test_declared_wordpress_fallback_overrides_other_present_fields(extra):
    result = assess_details({
        'name': 'Death Note', '_provider_title_fallback': True, **extra,
    }, 1535)
    assert result['status'] == 'inconclusive'
    assert 'fallback' in result['reason'].lower()
    assert 'title' in result['present_fields']
    if extra:
        assert {'cover', 'description', 'mal_id'} <= set(result['present_fields'])


def test_real_wordpress_empty_fetch_declares_fallback():
    from media_cli.providers.shared.wordpress import WordPressAnimeProvider

    provider = WordPressAnimeProvider(fetch=lambda url: '')
    payload = provider.get_title('https://example.com/death-note/')
    assert payload['_provider_title_fallback'] is True
    result = assess_details(payload, 1535)
    assert result['status'] == 'inconclusive'
    assert 'fallback' in result['reason'].lower()


@pytest.mark.parametrize('extra,field', [
    ({'thumbnail': 'https://example.com/cover.jpg'}, 'cover'),
    ({'description': 'A synopsis'}, 'description'),
    ({'format': 'TV'}, 'format'),
    ({'status': 'FINISHED'}, 'status'),
    ({'season': {'year': 2006}}, 'year'),
    ({'malId': 1535}, 'mal_id'),
    ({'aniListId': 1535}, 'anilist_id'),
    ({'romajiName': 'Desu Noto'}, 'alternate_title'),
])
def test_title_with_substantive_evidence_needs_no_other_optional_fields(extra, field):
    result = assess_details({'name': 'Death Note', '_provider_title_fallback': False, **extra}, 1535)
    assert result['status'] == 'passed'
    assert set(result['present_fields']) == {'title', field}
    assert result['missing_fields']


@pytest.mark.parametrize('title', ['MAL ID: 1535', 'AniList ID: 1535'])
def test_placeholder_remains_distinct_even_with_cover(title):
    result = assess_details({'name': title, 'thumbnail': 'https://example.com/cover.jpg'}, 1535)
    assert result['status'] == 'inconclusive'
    assert 'placeholder' in result['reason'].lower()
    assert result['present_fields'] == ['cover']
    assert 'title' in result['missing_fields']


@pytest.mark.parametrize('title', ['Death Note', 'MAL ID: 1535', ''])
@pytest.mark.parametrize('ids,reason,field', [
    ({'malId': 20}, 'MAL', 'mal_id'),
    ({'aniListId': 20}, 'AniList', 'anilist_id'),
    ({'malId': 1535, 'aniListId': 20}, 'AniList', 'anilist_id'),
])
def test_explicit_id_conflicts_still_fail_even_with_declared_fallback(title, ids, reason, field):
    result = assess_details({'name': title, '_provider_title_fallback': True, **ids},
                            1535, expected_anilist_id=1535)
    assert result['status'] == 'failed'
    assert reason in result['reason'] and 'conflicts' in result['reason']
    assert field in result['present_fields']
