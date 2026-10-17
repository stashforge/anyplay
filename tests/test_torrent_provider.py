from media_cli.providers.torrent import TorrentProvider
from media_cli.torrents.models import TorrentResult
from media_cli.domain.titles import get_display_titles


def _sample_result(**updates):
    data = {
        "source": "nyaa+tsukihime",
        "name": "[Ironclad] Example Show - S01E01 [WEB 1080p AV1]",
        "infohash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "size_bytes": 433_000_000,
        "seeders": 42,
        "leechers": 7,
        "completed": 1234,
        "group": "Ironclad",
        "episode": "1",
        "anime_title": "Example Show",
        "anime_english_title": "Example Show EN",
        "audio_langs": ("ja",),
        "sub_langs": ("en", "fr-FR"),
        "file_count": 1,
    }
    data.update(updates)
    return TorrentResult(**data)


def test_torrent_provider_search_returns_normal_titles():
    provider = TorrentProvider(search_fn=lambda query: [_sample_result()])

    results = provider.search("example")

    assert len(results) == 1
    item = results[0]
    assert item["_provider"] == "torrent"
    assert item["_provider_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert item["name"] == "[Ironclad] Example Show - S01E01 [WEB 1080p AV1]"
    assert item["englishName"] == "Example Show EN"
    assert item["availableEpisodes"]["sub"] == 1
    assert item["availableEpisodes"]["dub"] == 1
    assert "Seeders: 42" in item["description"]
    assert "Leechers: 7" in item["description"]
    assert "Completed: 1234" in item["description"]
    assert "Size: 412.9MiB" in item["description"]
    assert item["_torrent"]["leechers"] == 7
    assert item["_torrent"]["completed"] == 1234


def test_torrent_provider_summary_is_visible_as_alternate_title():
    provider = TorrentProvider(search_fn=lambda query: [_sample_result()])

    item = provider.search("example")[0]
    alternate = get_display_titles(item, item["name"])

    assert item["_torrent_summary"] == alternate
    assert "42 Seeders" in alternate
    assert "7 Leechers" in alternate
    assert "412.9MiB" in alternate


def test_torrent_provider_episode_catalog_exposes_single_episode_for_each_mode():
    provider = TorrentProvider(search_fn=lambda query: [_sample_result()])
    result = provider.search("example")[0]

    catalog = provider.episode_catalog(result["_provider_id"], "dub")

    assert catalog["ids"] == ["1"]
    assert catalog["episodes_map"]["1"]["label"] == "File"
    assert catalog["episodes_map_by_ttype"]["sub"]["1"]["translation_type"] == "sub"
    assert catalog["episodes_map_by_ttype"]["dub"]["1"]["translation_type"] == "dub"


def test_torrent_provider_episode_sources_returns_torrent_mirror():
    provider = TorrentProvider(search_fn=lambda query: [_sample_result()])
    result = provider.search("example")[0]

    sources = provider.episode_sources(result["_provider_id"], "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert len(urls) == 1
    assert urls[0]["type"] == "torrent"
    assert urls[0]["sourceUrl"].startswith("magnet:?xt=urn%3Abtih%3Aaaaaaaaa")
    assert urls[0]["torrent"]["seeders"] == 42


def test_torrent_provider_can_build_basic_source_from_uncached_infohash():
    provider = TorrentProvider(search_fn=lambda query: [])

    sources = provider.episode_sources("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "1", "sub")

    urls = sources["episode"]["sourceUrls"]
    assert len(urls) == 1
    assert urls[0]["type"] == "torrent"
    assert "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" in urls[0]["torrent"]["infohash"]


def test_torrent_provider_is_available_to_cli():
    from media_cli.cli.args import parse_cli_args
    from media_cli.providers import available_providers

    assert "torrent" in available_providers()

    args, _ = parse_cli_args(["search", "torrent:slime"])

    assert args.command == "search"
    assert args.provider == "torrent"
    assert args.query == ["slime"]


def test_torrent_provider_ignores_registry_request_json_injection(monkeypatch):
    from media_cli.providers import get_provider

    monkeypatch.setattr(
        "media_cli.providers.torrent.search_general_torrents",
        lambda query: [_sample_result()],
    )

    def fail_if_used(*args, **kwargs):
        raise AssertionError("torrent provider should not use request_json as search backend")

    provider = get_provider("torrent", fail_if_used)

    results = provider.search("example")

    assert len(results) == 1
    assert results[0]["_provider"] == "torrent"
