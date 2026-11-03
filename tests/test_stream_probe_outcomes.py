"""Uncertain media probes preserve cached mirrors without claiming validation."""

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from media_cli.media import sources
from media_cli.services import http


@pytest.fixture
def transport(monkeypatch):
    responses = []
    statuses = [200]
    def response(*args, **kwargs):
        item = SimpleNamespace(status_code=statuses.pop(0), close=Mock())
        responses.append(item)
        return item
    cffi = SimpleNamespace(get=response)
    requests = SimpleNamespace(head=response, get=response)
    monkeypatch.setitem(sys.modules, 'curl_cffi', SimpleNamespace(requests=cffi))
    monkeypatch.setitem(sys.modules, 'requests', requests)
    return statuses, responses, cffi


@pytest.mark.parametrize('use_cffi', [True, False])
@pytest.mark.parametrize('status,keep', [(200, True), (206, True), (302, True),
                                      (401, True), (403, True), (408, True), (429, True),
                                      (404, False), (410, False), (500, True), (503, True)])
def test_only_confirmed_missing_status_prunes_mirror(transport, use_cffi, status, keep):
    statuses, responses, cffi = transport
    statuses[:] = [status]
    if not use_cffi:
        cffi.get = Mock(side_effect=ImportError('unavailable'))
    assert sources.ping_stream_liveness({'link': 'https://example.test/media'}) is keep
    assert len(responses) == 1
    responses[0].close.assert_called_once_with()


@pytest.mark.parametrize('status,renew', [(200, True), (429, False), (403, False), (503, False)])
def test_stale_cache_retains_uncertain_mirror_without_renewing_validation(transport, status, renew):
    statuses, _, _ = transport
    statuses[:] = [status]
    stream = {'link': 'https://example.test/media', 'validated_at': 1.0, 'resolved_at': 1.0}
    assert sources.check_stream_health_and_refresh(stream, now=100000.0)
    assert stream['validated_at'] == (100000.0 if renew else 1.0)


def test_timeout_preserves_stale_mirror_and_hard_expiry_still_prunes(monkeypatch):
    client = SimpleNamespace(get=Mock(side_effect=TimeoutError), head=Mock(side_effect=TimeoutError))
    monkeypatch.setitem(sys.modules, 'curl_cffi', SimpleNamespace(requests=client))
    monkeypatch.setitem(sys.modules, 'requests', client)
    stream = {'link': 'https://example.test/media', 'validated_at': 1.0}
    assert sources.check_stream_health_and_refresh(stream, now=100000.0)
    assert stream['validated_at'] == 1.0
    stream['expires_at'] = 99999.0
    assert not sources.check_stream_health_and_refresh(stream, now=100000.0)


@pytest.mark.parametrize('status,keep', [(206, True), (429, True), (404, False)])
def test_head_not_supported_uses_get_and_closes_both_responses(transport, status, keep):
    statuses, responses, cffi = transport
    cffi.get = Mock(side_effect=ImportError('unavailable'))
    statuses[:] = [405, status]
    assert sources.ping_stream_liveness({'link': 'https://example.test/media'}) is keep
    assert len(responses) == 2
    for response in responses:
        response.close.assert_called_once_with()


def test_newly_extracted_url_still_requires_positive_availability(monkeypatch):
    class Response:
        status = 429
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(http.urllib.request, 'urlopen', lambda *a, **kw: Response())
    assert http.is_alive('https://example.test/media') is False
