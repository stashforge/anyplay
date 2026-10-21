"""AniPM source sessions are fresh, structured and safe to report."""

import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from media_cli.providers.anipm import AniPMProvider


@pytest.fixture
def handshake(monkeypatch):
    calls = []
    gateway = {"token": "private & token", "expiresAt": 2000, "skip": {"op": {"start": 5, "end": 90}}}
    resolver = {"source": "https://media.settlar.io/token", "kind": "hls", "expiresAt": 1900,
                "subtitles": [{"label": "English", "srclang": "en", "url": "https://media.settlar.io/sub"}]}

    def html(url, **kwargs):
        calls.append((url, kwargs))
        return '<script id="other">bad</script><script id="anipm-embed-config" type="application/json">' + json.dumps({"nonce": "private + nonce"}) + '</script>'

    def request(url, **kwargs):
        calls.append((url, kwargs))
        return resolver if urlsplit(url).hostname == "embed.settlar.io" else gateway

    provider = AniPMProvider(request_json_fn=request, request_html_fn=html, clock_fn=lambda: 1000)
    # These tests isolate session handling; channel enumeration has its own suite.
    monkeypatch.setattr(provider, "_episode_mirrors", lambda identity, episode, language, payload: payload)
    return provider, calls, gateway, resolver


def test_three_stage_handshake_encodes_secrets_and_sets_host_headers(handshake):
    provider, calls, _, _ = handshake
    result = provider.episode_sources("20", "3.5", "dub")
    assert calls[0][0] == "https://ani.pm/embed/ani/20/3.5/dub"
    assert parse_qs(urlsplit(calls[1][0]).query) == {
        "n": ["private + nonce"], "id": ["20"], "ep": ["3.5"], "lang": ["dub"], "hardsub": ["0"],
    }
    assert parse_qs(urlsplit(calls[2][0]).query) == {"t": ["private & token"]}
    assert calls[1][1]["extra_hdrs"]["Origin"] == "https://ani.pm"
    assert calls[2][1]["extra_hdrs"]["Origin"] == "https://embed.settlar.io"
    assert result["_provider"] == "anipm"
    assert result["_provider_episode"] == "3.5"
    source = result["episode"]["sourceUrls"][0]
    assert source["streamUrl"] == "https://media.settlar.io/token"
    assert source["headers"]["Referer"] == "https://embed.settlar.io/"
    assert source["type"] == "hls"
    assert source["requires_proxy"] is True
    assert source["allow_ytdlp_expansion"] is False
    assert source["subtitles"][0]["url"] == "https://media.settlar.io/sub"
    assert source["subtitles"][0]["lang"] == "en"
    assert source["skip_intervals"] == [{"type": "op", "label": "Opening", "start": 5.0,
                                          "end": 90.0, "source": "provider"}]


def test_repeated_resolutions_do_not_reuse_token_or_mutable_payload(handshake):
    provider, calls, _, _ = handshake
    first = provider.episode_sources("20", "1")
    first["episode"]["sourceUrls"][0]["subtitles"].clear()
    second = provider.episode_sources("20", "1")
    assert len(calls) == 6
    assert second["episode"]["sourceUrls"][0]["subtitles"]


@pytest.mark.parametrize("stage", ["gateway", "resolver"])
def test_expired_sessions_are_not_playable(handshake, stage):
    provider, calls, gateway, resolver = handshake
    (gateway if stage == "gateway" else resolver)["expiresAt"] = 990
    assert provider.episode_sources("20", "1") is None
    assert len(calls) == (4 if stage == "gateway" else 6)


@pytest.mark.parametrize("expiry", [None, True, "2000", float("nan"), float("inf"), 1005])
def test_missing_invalid_or_imminent_expiry_is_rejected(handshake, expiry):
    provider, _, _, resolver = handshake
    resolver["expiresAt"] = expiry
    assert provider.episode_sources("20", "1") is None


def test_gateway_expiring_during_resolution_is_rejected(handshake):
    provider, _, _, _ = handshake
    times = iter([1000, 1000, 1996, 1000, 1000, 1996])
    provider._clock = lambda: next(times)
    assert provider.episode_sources("20", "1") is None


@pytest.mark.parametrize("html", ["", '<script id="anipm-embed-config">invalid</script>',
                                  '<script id="anipm-embed-config">{}</script>'])
def test_missing_nonce_stops_before_token_request(handshake, html):
    provider, calls, _, _ = handshake
    provider._request_html = lambda *a, **kw: html
    assert provider.episode_sources("20", "1") is None
    assert not calls


@pytest.mark.parametrize("key,value", [("source", None), ("source", "javascript:bad"), ("kind", "unknown")])
def test_invalid_resolver_source_is_rejected(handshake, key, value):
    provider, _, _, resolver = handshake
    resolver[key] = value
    assert provider.episode_sources("20", "1") is None


def test_missing_token_and_failed_attempt_can_be_retried(handshake):
    provider, _, gateway, _ = handshake
    token = gateway.pop("token")
    assert provider.episode_sources("20", "1") is None
    gateway["token"] = token
    assert provider.episode_sources("20", "1")


def test_invalid_identity_or_language_makes_no_requests(handshake):
    provider, calls, _, _ = handshake
    assert provider.episode_sources("../20", "1") is None
    assert provider.episode_sources("20", "../1") is None
    assert provider.episode_sources("20", "1", "raw") is None
    assert not calls


def test_exception_does_not_log_token_url(handshake, caplog):
    provider, _, _, _ = handshake

    def failed(*a, **kw):
        raise OSError("https://embed.settlar.io/?t=private-secret")

    provider._request_json = failed
    assert provider.episode_sources("20", "1") is None
    assert "private-secret" not in caplog.text


@pytest.mark.parametrize("failure", ["nonce-json", "gateway-expiry", "resolver-expiry", "resolver-http"])
def test_stale_session_restarts_once_with_fresh_credentials(handshake, failure):
    provider, _, gateway, resolver = handshake
    calls = []
    attempt = 0

    def html(*a, **kw):
        nonlocal attempt
        attempt += 1
        return '<script id="anipm-embed-config">' + json.dumps({"nonce": f"nonce-{attempt}"}) + '</script>'

    def request(url, **kw):
        calls.append(url)
        is_resolver = urlsplit(url).hostname == "embed.settlar.io"
        if attempt == 1:
            if not is_resolver and failure == "nonce-json":
                return {"error": "invalid-nonce"}
            if not is_resolver and failure == "gateway-expiry":
                return {**gateway, "expiresAt": 999}
            if is_resolver and failure == "resolver-expiry":
                return {**resolver, "expiresAt": 999}
            if is_resolver and failure == "resolver-http":
                raise HTTPError(url, 403, "private token", {}, None)
        return resolver if is_resolver else {**gateway, "token": f"token-{attempt}"}

    provider._request_html = html
    provider._request_json = request
    assert provider.episode_sources("20", "1")
    assert attempt == 2
    gateway_queries = [parse_qs(urlsplit(url).query) for url in calls if urlsplit(url).hostname == "ani.pm"]
    assert [query["n"] for query in gateway_queries] == [["nonce-1"], ["nonce-2"]]
    assert parse_qs(urlsplit(calls[-1]).query)["t"] == ["token-2"]


def test_persistent_invalid_nonce_has_exactly_two_attempts(handshake, caplog):
    provider, calls, _, _ = handshake
    provider._request_json = lambda *a, **kw: {"error": "invalid-nonce"}
    assert provider.episode_sources("20", "1") is None
    assert len(calls) == 2  # HTML is fetched twice, not indefinitely.
    assert "private" not in caplog.text


@pytest.mark.parametrize("response", [{"error": "title-not-found"}, {"error": "unavailable"}, None, {}])
def test_unrelated_response_errors_do_not_restart_handshake(handshake, response):
    provider, calls, _, _ = handshake
    provider._request_json = lambda *a, **kw: response
    assert provider.episode_sources("20", "1") is None
    assert len(calls) == 1


@pytest.mark.parametrize("status", [404, 429, 500])
def test_non_auth_http_errors_are_not_retried(handshake, status):
    provider, calls, _, _ = handshake

    def failed(url, **kw):
        raise HTTPError(url, status, "private secret", {}, None)

    provider._request_json = failed
    assert provider.episode_sources("20", "1") is None
    assert len(calls) == 1


def test_keyboard_interrupt_is_not_swallowed_or_retried(handshake):
    provider, calls, _, _ = handshake

    def cancelled(*a, **kw):
        raise KeyboardInterrupt

    provider._request_json = cancelled
    with pytest.raises(KeyboardInterrupt):
        provider.episode_sources("20", "1")
    assert len(calls) == 1


@pytest.mark.parametrize("explicit", [False, True])
def test_subtitle_default_prefers_provider_choice_then_english(handshake, explicit):
    provider, _, _, resolver = handshake
    resolver["subtitles"] = [
        {"url": "https://media.settlar.io/fr", "label": "French", "srclang": "fr", "default": explicit},
        {"url": "https://media.settlar.io/en", "label": "English", "srclang": "en-US"},
    ]
    source = provider.episode_sources("20", "1")["episode"]["sourceUrls"][0]
    expected = "https://media.settlar.io/fr" if explicit else "https://media.settlar.io/en"
    assert source["subtitle_url"] == expected
    assert [row["url"] for row in source["subtitles"] if row["default"]] == [expected]
    assert source["subtitles"][1]["lang"] == "en"
    assert len(source["subtitles"]) == 2


def test_subtitle_fallback_ignores_invalid_rows_and_uses_language_label(handshake):
    provider, _, _, resolver = handshake
    resolver["subtitles"] = [None, {"url": "javascript:bad", "default": True},
                             {"url": "https://media.settlar.io/fr", "label": "French"},
                             {"url": "https://media.settlar.io/en", "label": "English"}]
    source = provider.episode_sources("20", "1")["episode"]["sourceUrls"][0]
    assert source["subtitle_url"] == "https://media.settlar.io/en"
    assert len(source["subtitles"]) == 2


@pytest.mark.parametrize("tracks", [[], None, [{"url": "https://media.settlar.io/fr", "srclang": "fr"}]])
def test_subtitle_default_handles_no_english_or_no_tracks(handshake, tracks):
    provider, _, _, resolver = handshake
    resolver["subtitles"] = tracks
    source = provider.episode_sources("20", "1")["episode"]["sourceUrls"][0]
    assert source.get("subtitle_url", "") == (tracks[0]["url"] if tracks else "")
    assert sum(bool(row.get("default")) for row in source["subtitles"]) == (1 if tracks else 0)


def test_chosen_subtitle_survives_shared_stream_resolution(handshake):
    from media_cli.media.resolver import resolve_source

    provider, _, _, _ = handshake
    source = provider.episode_sources("20", "1")["episode"]["sourceUrls"][0]
    stream = resolve_source(source, silent=True)[0]
    assert stream["subtitle_url"] == source["subtitle_url"]
    assert stream["subtitles"] == source["subtitles"]
    assert stream["requires_proxy"] is True


def test_anipm_sources_do_not_gain_synthetic_ytdlp_mirrors(handshake):
    from media_cli.core.streams import expand_direct_sources

    provider, _, _, _ = handshake
    sources = provider.episode_sources("20", "1")["episode"]["sourceUrls"]
    assert expand_direct_sources(sources) == sources


def test_expansion_opt_out_is_data_driven_and_keeps_other_provider_behavior():
    from media_cli.core.streams import expand_direct_sources

    native = {"sourceName": "Any native provider", "streamUrl": "https://example.org/master",
              "allow_ytdlp_expansion": False}
    other = {"sourceName": "Other provider", "streamUrl": "https://example.org/master"}
    assert expand_direct_sources([native]) == [native]
    assert len(expand_direct_sources([other])) == 2


def test_hardsub_uses_matching_embed_option_and_session_parameter(handshake):
    from media_cli.providers.shared.anipm_sources import resolve

    provider, calls, _, _ = handshake
    assert resolve("20", "1", "sub", hardsub=True, fetch_html=provider._request_html,
                   fetch_json=provider._request_json, clock=provider._clock)
    assert parse_qs(urlsplit(calls[0][0]).query) == {"hardsub": ["1"]}
    assert parse_qs(urlsplit(calls[1][0]).query)["hardsub"] == ["1"]
