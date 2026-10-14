from media_cli.cli.args import parse_cli_args
from media_cli.app.animethemes import _search_header
from media_cli.core.terminal import strip_ansi
from urllib.parse import parse_qs, urlparse
from media_cli.services.animethemes import (
    ANIMETHEMES_SEARCH_TABS,
    anime_lookup_url,
    flatten_anime_videos,
    flatten_search_bucket,
)


def test_animethemes_anime_lookup_prefers_external_ids():
    show = {"aniListId": "150672", "malId": "52034", "name": "[Oshi no Ko]"}

    params = parse_qs(urlparse(anime_lookup_url(show)).query)
    assert params["filter[site]"] == ["AniList"]
    assert params["filter[external_id]"] == ["150672"]

    show.pop("aniListId")
    params = parse_qs(urlparse(anime_lookup_url(show)).query)
    assert params["filter[site]"] == ["MyAnimeList"]
    assert params["filter[external_id]"] == ["52034"]


def test_animethemes_flattens_anime_theme_videos():
    anime = {
        "name": "[Oshi no Ko]",
        "slug": "oshi_no_ko",
        "animethemes": [
            {
                "slug": "OP1",
                "type": "OP",
                "song": {
                    "title": "Idol",
                    "artists": [{"name": "YOASOBI"}],
                },
                "animethemeentries": [
                    {
                        "episodes": "1-11",
                        "version": 1,
                        "spoiler": False,
                        "nsfw": False,
                        "videos": [
                            {
                                "link": "https://v.animethemes.moe/OshiNoKo-OP1.webm",
                                "resolution": 720,
                                "source": "WEB",
                                "nc": False,
                                "subbed": False,
                            },
                            {
                                "link": "https://v.animethemes.moe/OshiNoKo-OP1-NCBD1080.webm",
                                "resolution": 1080,
                                "source": "BD",
                                "nc": True,
                                "tags": "NCBD1080",
                            },
                        ],
                    }
                ],
            }
        ],
    }

    rows = flatten_anime_videos(anime)

    assert [row["label"] for row in rows] == [
        "OP1  Idol  YOASOBI  720 WEB",
        "OP1  Idol  YOASOBI  1080 BD NC",
    ]
    assert rows[0]["stream"]["link"] == "https://v.animethemes.moe/OshiNoKo-OP1.webm"
    assert rows[1]["kind"] == "video"


def test_animethemes_search_tabs_and_rows_are_limited_to_useful_buckets():
    assert ANIMETHEMES_SEARCH_TABS == ("anime", "songs", "videos", "artists")
    data = {
        "anime": [{"name": "[Oshi no Ko]", "year": 2023, "media_format": "TV"}],
        "songs": [{"title": "Idol", "artists": [{"name": "YOASOBI"}]}],
        "videos": [{"basename": "OshiNoKo-OP1.webm", "resolution": 720, "source": "WEB"}],
        "artists": [{"name": "YOASOBI"}],
        "studios": [{"name": "Ignored"}],
    }

    assert flatten_search_bucket("anime", data["anime"])[0]["label"] == "[Oshi no Ko]  2023 TV"
    assert flatten_search_bucket("songs", data["songs"])[0]["label"] == "Idol  YOASOBI"
    assert flatten_search_bucket("videos", data["videos"])[0]["label"] == "OshiNoKo-OP1.webm  720 WEB"
    assert flatten_search_bucket("artists", data["artists"])[0]["label"] == "YOASOBI"


def test_animethemes_command_parses_query():
    args, _ = parse_cli_args(["animethemes", "search", "idol"])

    assert args.command == "animethemes"
    assert args.animethemes == "search"
    assert args.query == ["idol"]


def test_animethemes_search_header_keeps_standard_four_line_shape():
    rows = flatten_search_bucket(
        "anime",
        [{"name": "[Oshi no Ko]", "year": 2023, "media_format": "TV"}],
    )
    header = _search_header(
        "oshi no ko",
        "anime",
        {"anime": 1, "songs": 2, "videos": 3, "artists": 4},
        rows,
        0,
    )

    lines = [strip_ansi(line) for line in header.splitlines()]
    assert len(lines) == 4
    assert lines[0] == "[Oshi no Ko]"
    assert lines[1] == "No alternative title"
    assert "TV" in lines[2]
    assert "Anime 1" in lines[3]
    assert "AnimeThemes" in lines[3]


def test_animethemes_loading_header_matches_search_shape():
    header = _search_header(
        "oshi no ko",
        "anime",
        {"anime": 0, "songs": 0, "videos": 0, "artists": 0},
        [],
        0,
        loading_msg=":: Searching...",
    )

    lines = [strip_ansi(line) for line in header.splitlines()]
    assert len(lines) == 4
    assert lines[1] == "Use Up/Down to browse previous searches."
    assert lines[2] == "Provider: AnimeThemes"
    assert lines[3] == ":: Searching..."
