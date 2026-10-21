"""AniPM participates in normal CLI discovery and shared playback contracts."""

import io
import time
from urllib.parse import urlsplit

import pytest

from media_cli.providers import available_providers, get_provider
from media_cli.providers.anipm import AniPMProvider


def test_cli_prefix_and_completion_use_registered_provider(monkeypatch):
    from media_cli import providers
    from media_cli.cli import completion
    from media_cli.cli.args import parse_cli_args

    monkeypatch.setattr(providers, "disabled_providers_from_config", lambda *a: set())
    assert isinstance(available_providers()["anipm"], AniPMProvider)
    args, _ = parse_cli_args(["search", "anipm:naruto", "--debug", "--incognito"])
    assert args.provider == "anipm"
    assert args.query == ["naruto"]
    assert "anipm:" in completion.PROVIDER_PREFIXES


@pytest.fixture
def api(monkeypatch):
    from media_cli import providers
    from media_cli.services import catalog

    monkeypatch.setattr(providers, "disabled_providers_from_config", lambda *a: set())
    title = {"anilistId": 20, "malId": 20, "title": "Naruto", "format": "TV",
             "episodes": {"total": 220, "sub": 1, "dub": 1}, "poster": "https://ani.pm/cover"}
    episode = {"number": 1, "title": "First", "available": {"sub": True, "dub": True}}
    calls = []

    def request(url, **kwargs):
        calls.append(url)
        path = urlsplit(url).path
        if path == "/api/anime/catalog":
            return {"items": [title]}
        if path == "/api/anime/ani/20":
            return {"data": {**title, "episodeList": [episode]}}
        if path == "/api/partner/v1/titles/20/episodes":
            return {"data": [episode]}
        if path == "/api/partner/v1/session":
            return {"token": "fake-token", "expiresAt": time.time() + 3600,
                    "skip": {"op": {"start": 10, "end": 90}}}
        if path == "/api/embed/session":
            return {"source": "https://media.settlar.io/test", "kind": "hls", "expiresAt": time.time() + 3600,
                    "subtitles": [{"url": "https://media.settlar.io/sub", "label": "English", "srclang": "en"}]}
        pytest.fail("Unexpected API path")

    monkeypatch.setattr(catalog, "_req", request)
    html = b'<script id="anipm-embed-config">{"nonce":"fake-nonce"}</script>'
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: io.BytesIO(html))
    return catalog, calls


def test_shared_search_details_catalogue_and_playback_dispatch(api, monkeypatch):
    from media_cli.media.resolver import resolve_source
    from media_cli.playback.desktop import play_desktop

    catalog, calls = api
    assert isinstance(get_provider("anipm", catalog._req), AniPMProvider)
    show = catalog.search_anime("Naruto", provider_id="anipm")[0]
    assert show["_provider"] == "anipm"
    details = catalog.get_show_by_id(show["_id"], "anipm")
    assert details["name"] == "Naruto"
    assert catalog.fetch_episode_catalog(show["_id"], "dub", "anipm")["ids"] == ["1"]
    data = catalog.get_episode_data(show["_id"], "1", "sub", "anipm")
    assert [row["channel"] for row in data["episode"]["sourceUrls"]] == ["sub"]
    stream = resolve_source(data["episode"]["sourceUrls"][0], silent=True)[0]
    loaded = {}
    proxy_calls = []
    monkeypatch.setattr("media_cli.media.local_proxy.replace_active_local_proxy", lambda server: None)
    monkeypatch.setattr("media_cli.media.local_proxy.cleanup_active_local_proxy", lambda: None)

    def proxy(url, referer, headers, **kwargs):
        proxy_calls.append((url, referer, headers))
        return "http://127.0.0.1/native.m3u8", object()

    class Ipc:
        props = {}

        def load(self, *args, **kwargs):
            loaded["args"] = args
            loaded.update(kwargs)

        def wait_for_playback(self, *args):
            return "QUIT", 0

    play_desktop(Ipc(), show["name"], "1", stream, get_resume_time=lambda *a: 0,
                 get_preferred_mirror=lambda *a: {}, update_stream_info=lambda *a: None,
                 prepare_subtitles_fn=lambda rows, **kw: rows, prepare_proxy_fn=proxy)
    assert loaded["args"][0] == "http://127.0.0.1/native.m3u8"
    assert proxy_calls[0][0] == "https://media.settlar.io/test"
    assert loaded["args"][2]["User-Agent"]
    assert loaded["args"][3] == "https://embed.settlar.io/"
    assert loaded["subtitles"][0]["default"]
    assert loaded["skip_intervals"][0]["source"] == "provider"
    assert loaded["skip_lookup"] == (None, 1)
    assert len(calls) == 6


def test_translation_switch_fetches_only_current_mode_mirrors(api):
    catalog, calls = api
    for language in ("sub", "dub", "sub"):
        start = len(calls)
        data = catalog.get_episode_data("20", "1", language, "anipm")
        assert {row["channel"] for row in data["episode"]["sourceUrls"]} == {language}
        session_calls = [url for url in calls[start:] if urlsplit(url).path == "/api/partner/v1/session"]
        assert len(session_calls) == 1
        assert f"lang={language}" in session_calls[0]
