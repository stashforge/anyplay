"""Provider session expiry survives resolution and constrains cached streams."""

import pytest

from media_cli.media.resolver import resolve_source
from media_cli.media.sources import calculate_stream_expiry


def test_provider_expiry_is_retained_through_direct_resolution():
    source = {"sourceName": "AniPM", "streamUrl": "https://media.settlar.io/opaque",
              "type": "hls", "expiresAt": 1800000000}
    stream = list(resolve_source(source, silent=True))[0]
    assert stream["expiresAt"] == 1800000000
    assert calculate_stream_expiry(stream, 1700000000) == 1800000000


@pytest.mark.parametrize("provider,token,expected", [
    (1800000000, 1900000000, 1800000000),
    (1900000000, 1800000000, 1800000000),
    (1600000001, 1800000000, 1600000001),
])
def test_earliest_explicit_limit_wins_even_if_already_expired(provider, token, expected):
    stream = {"streamUrl": f"https://example.test/video?exp={token}", "expiresAt": provider}
    assert calculate_stream_expiry(stream, 1700000000) == expected


@pytest.mark.parametrize("value", [None, True, False, "bad", "", float("inf"), float("nan"), [], {}])
def test_invalid_provider_expiry_does_not_break_existing_policy(value):
    stream = {"link": "https://example.test/video?exp=1800000000", "expiresAt": value}
    assert calculate_stream_expiry(stream, 1700000000) == 1800000000


def test_numeric_provider_expiry_string_is_supported():
    assert calculate_stream_expiry({"expiresAt": "1800000000"}, 1700000000) == 1800000000


def test_no_explicit_expiry_keeps_existing_soft_ttl():
    assert calculate_stream_expiry({}, 1700000000) == calculate_stream_expiry({"expiresAt": None}, 1700000000)


def test_provider_skips_survive_direct_resolution():
    intervals = [{"type": "op", "label": "Opening", "start": 5, "end": 90, "source": "provider"}]
    source = {"sourceName": "Provider", "streamUrl": "https://example.org/video.m3u8",
              "type": "hls", "skip_intervals": intervals}
    assert resolve_source(source, silent=True)[0]["skip_intervals"] == intervals


def test_provider_expiry_reaches_cache_publication(monkeypatch):
    from media_cli.core import streams

    monkeypatch.setattr(streams, "all_streams", [])
    monkeypatch.setattr(streams, "_streams_cache", {})
    stream = {"source_name": "AniPM", "resolution": "auto", "link": "https://example.test/opaque",
              "expiresAt": 1800000000}
    assert streams._publish_stream(stream, streams._streams_generation, key=("anipm-test",))
    assert stream["expires_at"] == 1800000000
