"""A MAL result cannot become an AniList record without a verified mapping."""
import json
from io import BytesIO
from urllib.error import URLError

from media_cli.core import anilist_fallback as fallback


def response(data):
    return BytesIO(json.dumps(data).encode())


def test_malsync_mapping_failure_never_substitutes_mal_id(monkeypatch):
    calls = []
    def request(req, **kwargs):
        calls.append(req.full_url)
        if 'tenrai.org' in req.full_url:
            return response({'data': [{'mal_id': 123, 'title': 'Example'}]})
        raise URLError('mapping unavailable')
    monkeypatch.setattr(fallback.urllib.request, 'urlopen', request)
    assert fallback.search_mal('Example')['data']['Page']['media'] == []
    assert len(calls) == 2


def test_only_mapped_mal_results_receive_anilist_ids(monkeypatch):
    def request(req, **kwargs):
        if 'tenrai.org' in req.full_url:
            return response({'data': [{'mal_id': 123, 'title': 'Example'}, {'mal_id': 456, 'title': 'Other'}]})
        if req.full_url.endswith('/123'):
            return response({'aniId': 789})
        return response({})
    monkeypatch.setattr(fallback.urllib.request, 'urlopen', request)
    media = fallback.search_mal('Example')['data']['Page']['media']
    assert [(row['id'], row['idMal']) for row in media] == [(789, 123)]


def test_successful_anilist_search_never_contacts_mal_or_malsync(monkeypatch):
    result = {'data': {'Page': {'media': [{'id': 789, 'idMal': 123}]}}}
    monkeypatch.setattr(fallback, 'search_anilist_public', lambda *args: result)
    monkeypatch.setattr(fallback, 'search_mal', lambda *args: (_ for _ in ()).throw(AssertionError('Unneeded fallback')))
    assert fallback.search_anilist_with_fallback('Example') is result


def test_authenticated_anilist_success_still_avoids_mapping_fallback(monkeypatch):
    result = {'data': {'Page': {'media': [{'id': 789, 'idMal': 123}]}}}
    def unavailable(*args):
        raise URLError('public search unavailable')
    monkeypatch.setattr(fallback, 'search_anilist_public', unavailable)
    monkeypatch.setattr(fallback, 'search_anilist_authenticated', lambda *args: result)
    monkeypatch.setattr(fallback, 'search_mal', lambda *args: (_ for _ in ()).throw(AssertionError('Unneeded fallback')))
    assert fallback.search_anilist_with_fallback('Example') is result


def test_mapping_fallback_is_used_only_after_both_anilist_attempts_fail(monkeypatch):
    calls = []
    def unavailable(*args):
        calls.append('anilist')
        raise URLError('unavailable')
    result = {'data': {'Page': {'media': [{'id': 789, 'idMal': 123}]}}}
    def last_resort(*args):
        calls.append('fallback')
        return result
    monkeypatch.setattr(fallback, 'search_anilist_public', unavailable)
    monkeypatch.setattr(fallback, 'search_anilist_authenticated', unavailable)
    monkeypatch.setattr(fallback, 'search_mal', last_resort)
    assert fallback.search_anilist_with_fallback('Example') is result
    assert calls == ['anilist', 'anilist', 'fallback']
