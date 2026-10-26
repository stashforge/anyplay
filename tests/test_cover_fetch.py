import urllib.error

import pytest

from media_cli.services.browser_access import integration
from media_cli.ui import covers


@pytest.mark.parametrize("status", [403, 429, 503])
def test_blocked_cover_uses_cached_image_transport(monkeypatch, status):
    url = "https://i.animepahe.pw/uploads/posters/test.webp"
    image = b"RIFFxxxxWEBPbinary"
    def blocked(*a, **kw):
        raise urllib.error.HTTPError(url, status, "Blocked", {}, None)
    monkeypatch.setattr(covers.urllib.request, "urlopen", blocked)
    calls = []
    def fetch(target, **kw):
        calls.append((target, kw))
        return image
    monkeypatch.setattr(integration, "fetch_cached_image", fetch)
    assert covers.fetch_cover_bytes(url, max_bytes=64) == image
    assert calls == [(url, {"max_bytes": 64})]


def test_existing_normal_cover_path_is_unchanged(monkeypatch):
    import io
    data = b"RIFFxxxxWEBPnormal"
    response = io.BytesIO(data)
    response.headers = {"Content-Length": str(len(data))}
    response.geturl = lambda: "https://images.test/cover.webp"
    monkeypatch.setattr(covers.urllib.request, "urlopen", lambda *a, **kw: response)
    monkeypatch.setattr(integration, "fetch_cached_image", lambda *a, **kw: pytest.fail("Unnecessary replay"))
    assert covers.fetch_cover_bytes("https://images.test/cover.webp") == data


def test_failed_cached_cover_preserves_original_error(monkeypatch):
    url = "https://images.test/cover.webp"
    failure = urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
    def fail(*a, **kw):
        raise failure
    monkeypatch.setattr(covers.urllib.request, "urlopen", fail)
    monkeypatch.setattr(integration, "fetch_cached_image", lambda *a, **kw: None)
    with pytest.raises(urllib.error.HTTPError) as error:
        covers.fetch_cover_bytes(url)
    assert error.value is failure


def test_wpcom_direct_host_fallback_is_unchanged(monkeypatch):
    import io
    image = b"RIFFxxxxWEBPnormal"
    response = io.BytesIO(image)
    response.headers = {}
    response.geturl = lambda: "https://images.test/poster.webp"
    calls = []
    def open(request, **kwargs):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise TimeoutError
        return response
    monkeypatch.setattr(covers.urllib.request, "urlopen", open)
    monkeypatch.setattr(integration, "fetch_cached_image", lambda *a, **kw: pytest.fail("Unnecessary replay"))
    assert covers.fetch_cover_bytes("https://i0.wp.com/images.test/poster.webp") == image
    assert calls == ["https://i0.wp.com/images.test/poster.webp", "https://images.test/poster.webp"]
