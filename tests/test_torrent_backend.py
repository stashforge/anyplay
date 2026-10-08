from media_cli.torrents import (
    TorrentSearchConfig,
    augment_episode_data_with_torrents,
    build_display_label,
    build_episode_torrent_queries,
    build_torrent_source,
    merge_results,
    parse_torrents_csv_rows,
    parse_nyaa_rss,
    parse_tsukihime_detail,
    parse_tsukihime_rows,
    search_anime_torrents,
)
from media_cli.media.resolver import resolve_source
from media_cli.torrents.models import torrent_search_config_from_app_config
from media_cli.torrents.search import rank_results
import threading
import time


def test_tsukihime_rows_keep_anime_metadata_without_seeders():
    rows = [
        {
            "id": 28361,
            "name": "[Ironclad] Slime - S04E23 (WEB 1080p AV1)",
            "btih": "14a97a8ea038faff42716b9685f4eea591c04073",
            "totalsize": 433841026,
            "audiolangs": ["ja"],
            "sublangs": ["en", "fr-FR"],
            "episode_no": 23,
            "filecount": 1,
            "anime": {"id": 24, "title": "Tensei Shitara Slime Datta Ken 4"},
            "group": {"name": "Ironclad"},
        }
    ]

    result = parse_tsukihime_rows(rows)[0]

    assert result.source == "tsukihime"
    assert result.tsukihime_id == 28361
    assert result.infohash == "14a97a8ea038faff42716b9685f4eea591c04073"
    assert result.group == "Ironclad"
    assert result.episode == "23"
    assert result.audio_langs == ("ja",)
    assert result.sub_langs == ("en", "fr-FR")
    assert result.file_count == 1
    assert result.seeders is None


def test_resolver_preserves_expanded_hls_audio_and_subtitle_tracks():
    streams = resolve_source(
        {
            "sourceName": "AniZone 1080p",
            "link": "https://cdn.example/1080.m3u8",
            "type": "hls",
            "resolution": "1080p",
            "headers": {"User-Agent": "UA"},
            "audio_tracks": [{"url": "https://cdn.example/audio-ja.m3u8", "language": "ja"}],
            "audio_url": "https://cdn.example/audio-ja.m3u8",
            "subtitles": [{"url": "https://cdn.example/en.vtt", "language": "en", "default": True}],
        },
        silent=True,
    )

    assert streams[0]["audio_tracks"][0]["language"] == "ja"
    assert streams[0]["audio_url"] == "https://cdn.example/audio-ja.m3u8"
    assert streams[0]["subtitles"][0]["language"] == "en"
    assert streams[0]["subtitle_url"] == "https://cdn.example/en.vtt"


def test_tsukihime_detail_hydrates_tracker_stats_and_trackers():
    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Group] Example",
                "btih": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "totalsize": 100,
            }
        ]
    )[0]
    detail = {
        "trackers": [
            {"url": "udp://tracker-a/announce", "seeders": 2, "leechers": 9, "complete": 1, "error": 0},
            {"url": "udp://tracker-b/announce", "seeders": 7, "leechers": 3, "complete": 4, "error": 0},
        ],
        "files": [
            {
                "audiolangs": ["ja"],
                "sublangs": ["en"],
            }
        ],
    }

    hydrated = parse_tsukihime_detail(result, detail)

    assert hydrated.seeders == 7
    assert hydrated.leechers == 9
    assert hydrated.completed == 4
    assert hydrated.tracker_urls == ("udp://tracker-a/announce", "udp://tracker-b/announce")
    assert hydrated.audio_langs == ("ja",)
    assert hydrated.sub_langs == ("en",)


def test_merge_prefers_tsukihime_metadata_and_csv_seed_stats():
    tsukihime = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E23",
                "btih": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "totalsize": 433841026,
                "audiolangs": ["ja"],
                "sublangs": ["en"],
                "episode_no": 23,
                "group": {"name": "Ironclad"},
            }
        ]
    )
    csv = parse_torrents_csv_rows(
        [
            {
                "name": "[Ironclad] Slime - S04E23",
                "infohash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "size_bytes": 433841026,
                "seeders": 42,
                "leechers": 5,
                "completed": 99,
            }
        ]
    )

    merged = merge_results(tsukihime + csv)[0]

    assert merged.source == "torrents-csv+tsukihime"
    assert merged.group == "Ironclad"
    assert merged.audio_langs == ("ja",)
    assert merged.sub_langs == ("en",)
    assert merged.seeders == 42
    assert merged.leechers == 5
    assert merged.completed == 99


def test_merge_uses_highest_known_swarm_stats():
    tsukihime = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[DKB] Slime - S04E01",
                "btih": "1212121212121212121212121212121212121212",
                "totalsize": 401544742,
                "seeders": 2,
                "group": {"name": "DKB"},
            }
        ]
    )
    nyaa = parse_nyaa_rss(
        """<?xml version="1.0" encoding="utf-8"?>
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0">
          <channel>
            <item>
              <title>[DKB] Slime - S04E01</title>
              <link>https://nyaa.si/view/2093452</link>
              <nyaa:infoHash>1212121212121212121212121212121212121212</nyaa:infoHash>
              <nyaa:size>383.0 MiB</nyaa:size>
              <nyaa:seeders>23</nyaa:seeders>
              <nyaa:leechers>0</nyaa:leechers>
              <nyaa:downloads>500</nyaa:downloads>
            </item>
          </channel>
        </rss>"""
    )

    merged = merge_results(tsukihime + nyaa)[0]

    assert merged.source == "nyaa+tsukihime"
    assert merged.nyaa_id == 2093452
    assert merged.seeders == 23
    assert merged.completed == 500


def test_parse_nyaa_rss_extracts_torrent_stats_and_magnet_metadata():
    results = parse_nyaa_rss(
        """<?xml version="1.0" encoding="utf-8"?>
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0">
          <channel>
            <item>
              <title>[Judas] Tensei Shitara Slime Datta Ken - S04E01 [1080p]</title>
              <link>https://nyaa.si/view/2091000</link>
              <guid isPermaLink="true">https://nyaa.si/view/2091000</guid>
              <pubDate>Fri, 03 Apr 2026 00:03:00 +0000</pubDate>
              <nyaa:infoHash>aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa</nyaa:infoHash>
              <nyaa:size>270.2 MiB</nyaa:size>
              <nyaa:seeders>96</nyaa:seeders>
              <nyaa:leechers>0</nyaa:leechers>
              <nyaa:downloads>7163</nyaa:downloads>
            </item>
          </channel>
        </rss>"""
    )

    result = results[0]
    assert result.source == "nyaa"
    assert result.infohash == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert result.group == "Judas"
    assert result.size_bytes == int(270.2 * 1024**2)
    assert result.seeders == 96
    assert result.leechers == 0
    assert result.completed == 7163
    assert result.nyaa_id == 2091000
    assert result.added_unix > 0


def test_display_label_uses_unknown_for_important_missing_metadata():
    result = parse_torrents_csv_rows(
        [
            {
                "name": "Loose Torrent Name 1080p",
                "infohash": "cccccccccccccccccccccccccccccccccccccccc",
                "size_bytes": 433841026,
                "seeders": 12,
            }
        ]
    )[0]

    label = build_display_label(result)

    assert label == "[Torrent] Unknown group 1080p | audio: Unknown | subs: Unknown | 12 seeders | 413.7MiB"


def test_search_anime_torrents_merges_tsukihime_and_csv_without_network():
    def fake_tsukihime_search(query, limit, offset):
        assert query == "slime s04e23"
        return [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E23 (WEB 1080p AV1)",
                "btih": "dddddddddddddddddddddddddddddddddddddddd",
                "totalsize": 433841026,
                "audiolangs": ["ja"],
                "sublangs": ["en"],
                "episode_no": 23,
                "group": {"name": "Ironclad"},
            }
        ]

    def fake_detail(tsukihime_id):
        assert tsukihime_id == 1
        return {"trackers": []}

    def fake_csv(query, limit, after):
        assert query == "slime s04e23"
        return [
            {
                "name": "[Ironclad] Slime - S04E23 (WEB 1080p AV1)",
                "infohash": "dddddddddddddddddddddddddddddddddddddddd",
                "size_bytes": 433841026,
                "seeders": 55,
                "leechers": 2,
            }
        ]

    def fake_nyaa(query, limit, offset):
        assert query == "slime s04e23"
        return """<?xml version="1.0" encoding="utf-8"?>
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel></channel></rss>"""

    results = search_anime_torrents(
        "slime s04e23",
        config=TorrentSearchConfig(min_seeders=5, hydrate_top=5),
        fetch_tsukihime_search=fake_tsukihime_search,
        fetch_tsukihime_detail=fake_detail,
        fetch_torrents_csv=fake_csv,
        fetch_nyaa_rss=fake_nyaa,
    )

    assert len(results) == 1
    assert results[0].source == "torrents-csv+tsukihime"
    assert results[0].seeders == 55
    assert build_display_label(results[0]) == "[Torrent] Ironclad 1080p | JA audio | EN subs | 55 seeders | 413.7MiB"


def test_search_anime_torrents_starts_source_fetches_in_parallel():
    starts = {}
    lock = threading.Lock()

    def mark(name):
        with lock:
            starts[name] = time.monotonic()
        time.sleep(0.05)

    def fake_tsukihime_search(query, limit, offset):
        mark("tsukihime")
        return []

    def fake_csv(query, limit, after):
        mark("csv")
        return []

    def fake_nyaa(query, limit, offset):
        mark("nyaa")
        return """<?xml version="1.0" encoding="utf-8"?>
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel></channel></rss>"""

    search_anime_torrents(
        "slime s04e01",
        config=TorrentSearchConfig(hydrate_top=0),
        fetch_tsukihime_search=fake_tsukihime_search,
        fetch_torrents_csv=fake_csv,
        fetch_nyaa_rss=fake_nyaa,
    )

    assert set(starts) == {"tsukihime", "csv", "nyaa"}
    assert max(starts.values()) - min(starts.values()) < 0.04


def test_search_anime_torrents_hydrates_tsukihime_details_in_parallel():
    starts = {}
    lock = threading.Lock()

    def fake_tsukihime_search(query, limit, offset):
        return [
            {
                "id": i,
                "name": f"[G{i}] Slime - S04E01",
                "btih": f"{i:040x}",
                "totalsize": 100,
                "group": {"name": f"G{i}"},
            }
            for i in range(1, 4)
        ]

    def fake_detail(tsukihime_id):
        with lock:
            starts[tsukihime_id] = time.monotonic()
        time.sleep(0.05)
        return {"trackers": []}

    def fake_csv(query, limit, after):
        return []

    def fake_nyaa(query, limit, offset):
        return """<?xml version="1.0" encoding="utf-8"?>
        <rss xmlns:nyaa="https://nyaa.si/xmlns/nyaa" version="2.0"><channel></channel></rss>"""

    search_anime_torrents(
        "slime s04e01",
        config=TorrentSearchConfig(hydrate_top=3),
        fetch_tsukihime_search=fake_tsukihime_search,
        fetch_tsukihime_detail=fake_detail,
        fetch_torrents_csv=fake_csv,
        fetch_nyaa_rss=fake_nyaa,
    )

    assert set(starts) == {1, 2, 3}
    assert max(starts.values()) - min(starts.values()) < 0.04


def test_search_anime_torrents_keeps_partial_results_when_one_source_fails():
    def fake_tsukihime_search(query, limit, offset):
        return [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E01 (1080p)",
                "btih": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "totalsize": 400 * 1024**2,
                "group": {"name": "Ironclad"},
            }
        ]

    def fake_csv(query, limit, after):
        return []

    def fake_nyaa(query, limit, offset):
        raise OSError("connection reset")

    results = search_anime_torrents(
        "slime s04e01",
        config=TorrentSearchConfig(hydrate_top=0),
        fetch_tsukihime_search=fake_tsukihime_search,
        fetch_torrents_csv=fake_csv,
        fetch_nyaa_rss=fake_nyaa,
    )

    assert [result.group for result in results] == ["Ironclad"]


def test_episode_torrent_fallback_stops_after_visible_result_cap():
    ep_data = {"episode": {"sourceUrls": []}}
    results = [
        parse_tsukihime_rows(
            [
                {
                    "id": i,
                    "name": f"[Group{i}] Slime - S04E01 (1080p)",
                    "btih": f"{i:040x}",
                    "totalsize": (300 + i) * 1024**2,
                    "filecount": 1,
                    "group": {"name": f"Group{i}"},
                }
            ]
        )[0]
        for i in range(1, 20)
    ]
    seen_queries = []

    def fake_search(query, *, config):
        seen_queries.append(query)
        return results

    augment_episode_data_with_torrents(
        ep_data,
        show={"englishName": "Slime", "name": "Slime Romaji", "_season": 4},
        episode_label="1",
        max_results=12,
        search_fn=fake_search,
    )

    assert seen_queries == ["Slime S04E01"]


def test_episode_torrent_fallback_honors_cancel_after_search_returns():
    ep_data = {"episode": {"sourceUrls": [{"sourceName": "Direct", "sourceUrl": "https://example.test/v.m3u8"}]}}
    calls = 0

    def fake_search(query, *, config):
        nonlocal calls
        calls += 1
        return parse_tsukihime_rows(
            [
                {
                    "id": 1,
                    "name": "[Ironclad] Slime - S04E01 (1080p)",
                    "btih": "a" * 40,
                    "totalsize": 400 * 1024**2,
                    "filecount": 1,
                    "group": {"name": "Ironclad"},
                }
            ]
        )

    cancelled = False

    def cancel_check():
        return cancelled

    def cancelling_search(query, *, config):
        nonlocal cancelled
        result = fake_search(query, config=config)
        cancelled = True
        return result

    augmented = augment_episode_data_with_torrents(
        ep_data,
        show={"englishName": "Slime", "name": "Slime Romaji", "_season": 4},
        episode_label="1",
        max_results=12,
        search_fn=cancelling_search,
        cancel_check=cancel_check,
    )

    assert calls == 1
    assert augmented is ep_data
    assert augmented["episode"]["sourceUrls"] == ep_data["episode"]["sourceUrls"]


def test_build_torrent_source_preserves_metadata_for_tui_and_resolver():
    result = merge_results(
        parse_tsukihime_rows(
            [
                {
                    "id": 1,
                    "name": "[Ironclad] Slime - S04E23 (WEB 1080p AV1)",
                    "btih": "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
                    "totalsize": 433841026,
                    "audiolangs": ["ja"],
                    "sublangs": ["en"],
                    "episode_no": 23,
                    "group": {"name": "Ironclad"},
                }
            ]
        )
    )[0]

    source = build_torrent_source(result)

    assert source["sourceName"] == "[Torrent] Ironclad 1080p | JA audio | EN subs | seeders: Unknown | 413.7MiB"
    assert source["type"] == "torrent"
    assert source["_source_kind"] == "torrent"
    assert source["sourceUrl"].startswith("magnet:?xt=urn%3Abtih%3Aeeee")
    assert source["torrent"]["infohash"] == "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    assert source["torrent"]["group"] == "Ironclad"
    assert source["torrent"]["audio_langs"] == ["ja"]
    assert source["torrent"]["sub_langs"] == ["en"]


def test_resolver_turns_torrent_source_into_player_stream():
    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E23 (WEB 1080p AV1)",
                "btih": "ffffffffffffffffffffffffffffffffffffffff",
                "totalsize": 433841026,
                "group": {"name": "Ironclad"},
            }
        ]
    )[0]

    streams = resolve_source(build_torrent_source(result), silent=True)

    assert len(streams) == 1
    stream = streams[0]
    assert stream["type"] == "torrent"
    assert stream["link"].startswith("magnet:?xt=urn%3Abtih%3Affff")
    assert stream["torrent"]["infohash"] == "ffffffffffffffffffffffffffffffffffffffff"
    assert stream["android_safe"] is False
    assert stream["source_name"].startswith("[Torrent] Ironclad")


def test_episode_torrent_queries_use_show_title_and_episode_label():
    one_piece = {"englishName": "One Piece", "name": "One Piece"}
    slime = {"name": "That Time I Got Reincarnated as a Slime Season 4", "_season": 4}

    assert build_episode_torrent_queries(one_piece, "1145") == ["One Piece 1145"]
    assert build_episode_torrent_queries(slime, "23") == [
        "That Time I Got Reincarnated as a Slime S04E23",
        "That Time I Got Reincarnated as a Slime Season 4 S04E23",
        "That Time I Got Reincarnated as a Slime 23",
        "That Time I Got Reincarnated as a Slime Season 4 23",
    ]


def test_episode_torrent_queries_include_franchise_episode_from_episode_map():
    show = {
        "englishName": "That Time I Got Reincarnated as a Slime Season 4",
        "name": "Tensei Shitara Slime Datta Ken 4th Season Part 1 & 2",
        "_season": 4,
        "_episodes_map": {
            "episode-1": {
                "display_number": "1",
                "franchise_number": "23",
                "label": "Episode 1 [23]",
            }
        },
    }

    queries = build_episode_torrent_queries(show, "Episode 1", episode_id="episode-1")

    assert queries[0] == "That Time I Got Reincarnated as a Slime S04E23"
    assert "That Time I Got Reincarnated as a Slime S04E01" in queries
    assert "That Time I Got Reincarnated as a Slime Season 4 S04E23" in queries
    assert "That Time I Got Reincarnated as a Slime Season 4 23" in queries


def test_augment_episode_data_appends_low_priority_torrent_sources_without_network():
    ep_data = {"episode": {"sourceUrls": [{"sourceName": "Provider", "sourceUrl": "https://example.test/v.m3u8"}]}}
    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Ironclad] One Piece - 1145 (1080p)",
                "btih": "abababababababababababababababababababab",
                "totalsize": 500,
                "audiolangs": ["ja"],
                "sublangs": ["en"],
                "group": {"name": "Ironclad"},
            }
        ]
    )[0]
    seen = {}

    def fake_search(query, *, config):
        seen["query"] = query
        seen["config"] = config
        return [result]

    augmented = augment_episode_data_with_torrents(
        ep_data,
        show={"englishName": "One Piece"},
        episode_label="1145",
        max_results=2,
        search_fn=fake_search,
    )

    sources = augmented["episode"]["sourceUrls"]
    assert seen["query"] == "One Piece 1145"
    assert seen["config"].min_seeders == 2
    assert sources[0]["sourceName"] == "Provider"
    assert sources[1]["type"] == "torrent"
    assert sources[1]["priority"] == 80
    assert sources[1]["torrent"]["infohash"] == "abababababababababababababababababababab"


def test_episode_torrent_fallback_ranks_results_for_current_translation_mode():
    ep_data = {"episode": {"sourceUrls": []}}
    rows = [
        {
            "id": 1,
            "name": "[SubGroup] Slime - S04E01 (1080p Multi-Subs)",
            "btih": "1111111111111111111111111111111111111111",
            "totalsize": 400 * 1024**2,
            "filecount": 1,
            "audiolangs": ["ja"],
            "sublangs": ["en"],
            "group": {"name": "SubGroup"},
        },
        {
            "id": 2,
            "name": "[Yameii] Slime - S04E01 [English Dub] (1080p)",
            "btih": "2222222222222222222222222222222222222222",
            "totalsize": 1400 * 1024**2,
            "filecount": 1,
            "audiolangs": ["en"],
            "sublangs": ["en"],
            "group": {"name": "Yameii"},
        },
        {
            "id": 3,
            "name": "[Dual] Slime - S04E01 [Dual-Audio] (1080p)",
            "btih": "3333333333333333333333333333333333333333",
            "totalsize": 1200 * 1024**2,
            "filecount": 1,
            "audiolangs": ["ja", "en"],
            "sublangs": ["en"],
            "group": {"name": "Dual"},
        },
        {
            "id": 4,
            "name": "[Unknown] Slime - S04E01 (1080p)",
            "btih": "4444444444444444444444444444444444444444",
            "totalsize": 300 * 1024**2,
            "filecount": 1,
            "group": {"name": "Unknown"},
        },
    ]
    results = parse_tsukihime_rows(rows)
    calls = {"count": 0}

    def fake_search(query, *, config):
        calls["count"] += 1
        return results

    sub_augmented = augment_episode_data_with_torrents(
        ep_data,
        show={"englishName": "Slime", "_season": 4},
        episode_label="1",
        ttype="sub",
        search_fn=fake_search,
    )
    calls_after_sub = calls["count"]
    dub_augmented = augment_episode_data_with_torrents(
        ep_data,
        show={"englishName": "Slime", "_season": 4},
        episode_label="1",
        ttype="dub",
        search_fn=fake_search,
    )

    sub_groups = [source["torrent"]["group"] for source in sub_augmented["episode"]["sourceUrls"]]
    dub_groups = [source["torrent"]["group"] for source in dub_augmented["episode"]["sourceUrls"]]

    assert sub_groups[:2] == ["SubGroup", "Dual"]
    assert dub_groups[:2] == ["Yameii", "Dual"]
    assert calls["count"] == calls_after_sub


def test_episode_torrent_fallback_keeps_more_tsukihime_mirrors_before_capping():
    ep_data = {"episode": {"sourceUrls": []}}
    results = [
        parse_tsukihime_rows(
            [
                {
                    "id": i,
                    "name": f"[Group{i}] Slime - S04E01 (1080p)",
                    "btih": f"{i:040x}",
                    "totalsize": (300 + i) * 1024**2,
                    "filecount": 1,
                    "audiolangs": ["ja"],
                    "sublangs": ["en"],
                    "group": {"name": f"Group{i}"},
                }
            ]
        )[0]
        for i in range(1, 15)
    ]

    def fake_search(query, *, config):
        return results

    augmented = augment_episode_data_with_torrents(
        ep_data,
        show={"englishName": "That Time I Got Reincarnated as a Slime", "_season": 4},
        episode_label="1",
        search_fn=fake_search,
    )

    sources = augmented["episode"]["sourceUrls"]
    assert len(sources) == 12
    assert all(source["type"] == "torrent" for source in sources)


def test_episode_torrent_fallback_merges_multiple_title_queries():
    ep_data = {"episode": {"sourceUrls": []}}
    first = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Wide] Slime Season 4 - S04E01 (1080p)",
                "btih": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "totalsize": 1400 * 1024**2,
                "filecount": 1,
                "group": {"name": "Wide"},
            }
        ]
    )[0]
    second = parse_tsukihime_rows(
        [
            {
                "id": 2,
                "name": "[DKB] Tensei shitara Slime Datta Ken - S04E01 [1080p]",
                "btih": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                "totalsize": 383 * 1024**2,
                "filecount": 1,
                "group": {"name": "DKB"},
            }
        ]
    )[0]
    seen_queries = []

    def fake_search(query, *, config):
        seen_queries.append(query)
        if query == "That Time I Got Reincarnated as a Slime S04E01":
            return [first]
        if query == "Tensei Shitara Slime Datta Ken S04E01":
            return [second]
        return []

    augmented = augment_episode_data_with_torrents(
        ep_data,
        show={
            "englishName": "That Time I Got Reincarnated as a Slime Season 4",
            "name": "Tensei Shitara Slime Datta Ken 4th Season Part 1 & 2",
            "_season": 4,
        },
        episode_label="1",
        search_fn=fake_search,
    )

    groups = [source["torrent"]["group"] for source in augmented["episode"]["sourceUrls"]]
    assert "That Time I Got Reincarnated as a Slime S04E01" in seen_queries
    assert "Tensei Shitara Slime Datta Ken S04E01" in seen_queries
    assert groups == ["DKB", "Wide"]


def test_rank_results_prefers_single_episode_torrents_over_large_batches():
    rows = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[BatchGroup] Slime Season 4 Batch (1080p)",
                "btih": "1111111111111111111111111111111111111111",
                "totalsize": 10 * 1024**3,
                "seeders": 150,
                "filecount": 24,
                "audiolangs": ["ja"],
                "sublangs": ["en"],
                "group": {"name": "BatchGroup"},
            },
            {
                "id": 2,
                "name": "[DKB] Tensei shitara Slime Datta Ken - S04E01 [1080p]",
                "btih": "2222222222222222222222222222222222222222",
                "totalsize": 383 * 1024**2,
                "filecount": 1,
                "audiolangs": ["ja"],
                "sublangs": ["en"],
                "group": {"name": "DKB"},
            },
        ]
    )

    ranked = rank_results(
        rows,
        TorrentSearchConfig(
            min_seeders=2,
            allow_unknown_seeders=True,
            prefer_size=(150 * 1024**2, 2500 * 1024**2),
        ),
    )

    assert ranked[0].group == "DKB"


def test_torrent_search_config_reads_user_config_values():
    cfg = torrent_search_config_from_app_config(
        {
            "torrent_search_limit": "25",
            "torrent_min_seeders": "5",
            "torrent_hydrate_top": "3",
            "torrent_prefer_size": "400MB:1600MB",
            "torrent_allow_unknown_seeders": "false",
        }
    )

    assert cfg.limit == 25
    assert cfg.min_seeders == 5
    assert cfg.hydrate_top == 3
    assert cfg.prefer_size == (400 * 1024**2, 1600 * 1024**2)
    assert cfg.allow_unknown_seeders is False


def test_stream_sorting_keeps_torrents_after_provider_sources(monkeypatch):
    from media_cli.core import streams

    calls = []

    def fake_resolve(src, silent=False):
        calls.append(src["sourceName"])
        return [
            {
                "source_name": src["sourceName"],
                "resolution": src.get("resolution", "?"),
                "link": src.get("link", ""),
                "type": src.get("type", "hls"),
                "torrent": src.get("torrent", {}),
            }
        ]

    monkeypatch.setattr(streams, "resolve_source", fake_resolve)
    streams.clear_cache()
    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[TorrentGroup] Show - S01E01 (1080p)",
                "btih": "a" * 40,
                "totalsize": 400 * 1024**2,
                "group": {"name": "TorrentGroup"},
            }
        ]
    )[0]
    ep_data = {
        "episode": {
            "sourceUrls": [
                build_torrent_source(result),
                {
                    "sourceName": "Provider 720p",
                    "sourceUrl": "https://example.test/video.m3u8",
                    "type": "hls",
                    "resolution": "720p",
                },
            ]
        }
    }

    selected = streams.fetch_episode_stream("show", "1", "sub", "1080p", "mkissa", ep_data=ep_data)

    assert selected[0]["source_name"] == "Provider 720p"
    assert calls == ["Provider 720p"]


def test_playback_episode_augment_helper_respects_disabled_torrent_fallback(monkeypatch):
    from media_cli.app import playback_episodes

    monkeypatch.setattr("media_cli.core.storage.load_config", lambda: {"torrent_fallback": False})
    monkeypatch.setattr("media_cli.providers.is_provider_active", lambda provider_id: True)

    def fail_augment(*args, **kwargs):
        raise AssertionError("torrent augmentation should be skipped")

    monkeypatch.setattr("media_cli.torrents.augment_episode_data_with_torrents", fail_augment)
    original = {"episode": {"sourceUrls": []}}

    assert playback_episodes._augment_episode_sources_with_torrents(
        original,
        {"name": "Show"},
        "1",
        "sub",
    ) is original


def test_playback_episode_augment_helper_uses_display_episode_label(monkeypatch):
    from media_cli.app import playback_episodes

    seen = {}

    def fake_augment(ep_data, *, show, episode_label, episode_id=None, ttype="sub"):
        seen["episode_label"] = episode_label
        seen["episode_id"] = episode_id
        seen["ttype"] = ttype
        return {"episode": {"sourceUrls": [{"sourceName": "Torrent"}]}}

    monkeypatch.setattr("media_cli.torrents.augment_episode_data_with_torrents", fake_augment)

    result = playback_episodes._augment_episode_sources_with_torrents(
        {"episode": {"sourceUrls": []}},
        {"name": "Show", "_episode_labels": {"ep-57": "Episode 27 [57]"}},
        "ep-57",
        "sub",
    )

    assert seen["episode_label"] == "27 [57]"
    assert seen["episode_id"] == "ep-57"
    assert seen["ttype"] == "sub"
    assert result["episode"]["sourceUrls"][0]["sourceName"] == "Torrent"


def test_mirror_torrent_augmentation_runs_without_blocking_picker(monkeypatch):
    from media_cli.app import playback_menu

    cur_key = ("show", "ep1", "sub", "mkissa", "best")
    ep_data = {"episode": {"sourceUrls": [{"sourceName": "Provider"}]}}
    augmented = {"_torrent_fallback_checked": True, "episode": {"sourceUrls": [{"sourceName": "Torrent"}]}}
    seen = {}

    monkeypatch.setattr(playback_menu.app_core, "is_termux", lambda: False)

    def fake_augment(data, show, episode_id, ttype):
        seen["augment_args"] = (data, show, episode_id, ttype)
        time.sleep(0.02)
        return augmented

    def fake_set_cached(data, key):
        seen["cached"] = (data, key)

    def fake_start_bg_resolve(data, exclude, show_id, ep, ttype, provider_id, quality):
        seen["resolved"] = (data, exclude, show_id, ep, ttype, provider_id, quality)

    monkeypatch.setattr(playback_menu.playback_mod, "_augment_episode_sources_with_torrents", fake_augment)
    monkeypatch.setattr(playback_menu.app_core, "_set_cached_ep_data", fake_set_cached)
    monkeypatch.setattr(playback_menu.streams, "start_bg_resolve", fake_start_bg_resolve)
    playback_menu._torrent_mirror_augmenting.clear()

    thread = playback_menu._start_torrent_mirror_augmentation(
        cur_key,
        ep_data,
        {"name": "Show"},
        "ep1",
        "sub",
        "mkissa",
        "best",
    )

    assert thread is not None
    assert thread.is_alive() or "cached" in seen
    thread.join(timeout=1)
    assert seen["augment_args"] == (ep_data, {"name": "Show"}, "ep1", "sub")
    assert seen["cached"] == (augmented, cur_key)
    assert seen["resolved"] == (augmented, set(), "show", "ep1", "sub", "mkissa", "best")


def test_empty_provider_episode_data_still_starts_torrent_mirror_augmentation(monkeypatch):
    from media_cli.app import playback_menu

    seen = {}
    monkeypatch.setattr(playback_menu.app_core, "is_termux", lambda: False)
    monkeypatch.setattr(playback_menu.playback_mod, "_augment_episode_sources_with_torrents", lambda *args: None)

    def fake_set_cached(data, key):
        seen["cached"] = (data, key)

    monkeypatch.setattr(playback_menu.app_core, "_set_cached_ep_data", fake_set_cached)
    playback_menu._torrent_mirror_augmenting.clear()

    thread = playback_menu._start_torrent_mirror_augmentation(
        ("show", "ep1", "sub", "mkissa", "best"),
        {"episode": {"sourceUrls": []}},
        {"name": "Show"},
        "ep1",
        "sub",
        "mkissa",
        "best",
    )

    assert thread is not None
    thread.join(timeout=1)
    assert "cached" not in seen


def test_stream_cache_becomes_stale_when_episode_sources_change():
    from media_cli.core import streams

    streams.clear_cache()
    key = streams.make_stream_key("show", "1", "sub", "mkissa", "best")

    streams._set_cached_ep_data({"episode": {"sourceUrls": []}}, key)
    streams._streams_cache[key]["state"] = "FAILED"

    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E01 (1080p)",
                "btih": "1212121212121212121212121212121212121212",
                "totalsize": 100,
                "group": {"name": "Ironclad"},
            }
        ]
    )[0]
    changed = {"episode": {"sourceUrls": [build_torrent_source(result)]}}

    streams._set_cached_ep_data(changed, key)

    assert streams._streams_cache[key]["state"] == "STALE"
    assert streams.is_stream_cache_ready("show", "1", "sub", "mkissa", "best") is False


def test_cached_episode_data_with_unresolved_sources_is_not_ready():
    from media_cli.core import streams

    streams.clear_cache()
    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E01 (1080p)",
                "btih": "3434343434343434343434343434343434343434",
                "totalsize": 100,
                "group": {"name": "Ironclad"},
            }
        ]
    )[0]

    streams._set_cached_ep_data(
        {"episode": {"sourceUrls": [build_torrent_source(result)]}},
        show_id="show",
        ep="1",
        ttype="sub",
        provider_id="mkissa",
        quality="best",
    )

    assert streams.is_stream_cache_ready("show", "1", "sub", "mkissa", "best") is False


def test_torrent_sources_publish_as_selectable_mirrors_after_background_pass():
    from media_cli.core import streams

    streams.clear_cache()
    result = parse_tsukihime_rows(
        [
            {
                "id": 1,
                "name": "[Ironclad] Slime - S04E01 (1080p)",
                "btih": "5656565656565656565656565656565656565656",
                "totalsize": 100,
                "group": {"name": "Ironclad"},
            }
        ]
    )[0]
    ep_data = {"episode": {"sourceUrls": [build_torrent_source(result)]}}

    streams.start_bg_resolve(ep_data, set(), "show", "1", "sub", "mkissa", "best")
    deadline = time.time() + 1
    snapshot = []
    while time.time() < deadline:
        snapshot = streams._stream_snapshot("show", "1", "sub", "mkissa", "best", ep_data)
        if snapshot:
            break
        time.sleep(0.01)

    assert len(snapshot) == 1
    assert snapshot[0]["type"] == "torrent"
    assert snapshot[0]["link"].startswith("magnet:?xt=urn%3Abtih%3A5656")


def test_torrent_mirrors_publish_in_smallest_size_order():
    from media_cli.core import streams

    streams.clear_cache()
    rows = [
        {
            "id": 1,
            "name": "[Big] Slime - S04E01 (1080p)",
            "btih": "7878787878787878787878787878787878787878",
            "totalsize": 10 * 1024**3,
            "group": {"name": "Big"},
        },
        {
            "id": 2,
            "name": "[Small] Slime - S04E01 (1080p)",
            "btih": "9090909090909090909090909090909090909090",
            "totalsize": 1400 * 1024**2,
            "group": {"name": "Small"},
        },
    ]
    ep_data = {"episode": {"sourceUrls": [build_torrent_source(result) for result in parse_tsukihime_rows(rows)]}}

    streams.start_bg_resolve(ep_data, set(), "show", "1", "sub", "mkissa", "best")
    deadline = time.time() + 1
    snapshot = []
    while time.time() < deadline:
        snapshot = streams._stream_snapshot("show", "1", "sub", "mkissa", "best", ep_data)
        if len(snapshot) >= 2:
            break
        time.sleep(0.01)

    assert [stream["torrent"]["group"] for stream in snapshot] == ["Small", "Big"]


def test_foreground_resolution_tests_smaller_torrents_first(monkeypatch):
    from media_cli.core import streams

    rows = [
        {
            "id": 1,
            "name": "[Big] Slime - S04E01 (1080p)",
            "btih": "abababababababababababababababababababab",
            "totalsize": 10 * 1024**3,
            "group": {"name": "Big"},
        },
        {
            "id": 2,
            "name": "[Small] Slime - S04E01 (1080p)",
            "btih": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
            "totalsize": 1400 * 1024**2,
            "group": {"name": "Small"},
        },
    ]
    ep_data = {"episode": {"sourceUrls": [build_torrent_source(result) for result in parse_tsukihime_rows(rows)]}}
    seen = []

    def fake_resolve_source(source):
        seen.append(source["torrent"]["group"])
        return []

    monkeypatch.setattr(streams, "resolve_source", fake_resolve_source)

    assert streams.fetch_episode_stream("show", "1", "sub", "best", "mkissa", ep_data=ep_data) is None
    assert seen[:2] == ["Small", "Big"]
