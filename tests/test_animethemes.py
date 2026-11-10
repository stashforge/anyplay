from media_cli.cli.args import parse_cli_args
from media_cli.app.animethemes import (
    _active_tabs,
    _row_show,
    _search_header,
    _search_prompt,
)
from media_cli.core.terminal import strip_ansi
from media_cli.services.animethemes import (
    ANIMETHEMES_SEARCH_TABS,
    flatten_anime_videos,
    flatten_search_bucket,
    theme_video_rows,
)


def test_animethemes_anime_lookup_prefers_external_ids(monkeypatch):
    from media_cli.services import animethemes as service

    calls = []
    monkeypatch.setattr(service, "_graphql", lambda query, variables: calls.append(variables) or {"filterAnimeByExternalSite": []})
    show = {"aniListId": "150672", "malId": "52034", "name": "[Oshi no Ko]"}
    assert service.get_anime_for_show(show) is None
    show.pop("aniListId")
    assert service.get_anime_for_show(show) is None
    assert calls == [{"site": "ANILIST", "ids": [150672]}, {"site": "MAL", "ids": [52034]}]


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


def test_animethemes_search_tabs_match_visible_site_tabs():
    assert ANIMETHEMES_SEARCH_TABS == (
        "anime",
        "animethemes",
        "artists",
        "series",
        "studios",
        "playlists",
    )
    data = {
        "anime": [{
            "name": "[Oshi no Ko]",
            "year": 2023,
            "media_format": "TV",
            "animethemes": [{}, {}],
        }],
        "animethemes": [{
            "slug": "OP1",
            "song": {"title": "Idol", "artists": [{"name": "YOASOBI"}]},
            "anime": {"name": "[Oshi no Ko]"},
        }],
        "artists": [{"name": "YOASOBI"}],
        "series": [{"name": "[Oshi no Ko]"}],
        "studios": [{"name": "Doga Kobo"}],
        "playlists": [{"name": "Oshi no Ko OPs", "visibility": "Public"}],
    }

    assert flatten_search_bucket("anime", data["anime"])[0]["label"] == "[Oshi no Ko]"
    assert flatten_search_bucket("animethemes", data["animethemes"])[0]["label"] == "Idol OP 1"
    assert flatten_search_bucket("artists", data["artists"])[0]["label"] == "YOASOBI"
    assert flatten_search_bucket("series", data["series"])[0]["label"] == "[Oshi no Ko]"
    assert flatten_search_bucket("studios", data["studios"])[0]["label"] == "Doga Kobo"
    assert flatten_search_bucket("playlists", data["playlists"])[0]["label"] == "Oshi no Ko OPs"


def test_animethemes_search_uses_only_non_empty_tabs_and_prompt_names_active_tab():
    data = {
        "anime": [{"name": "[Oshi no Ko]"}],
        "animethemes": [{"slug": "OP1", "song": {"title": "Idol"}}],
        "artists": [],
        "series": [],
        "studios": [],
        "playlists": [],
    }

    assert _active_tabs(data) == ["anime", "animethemes"]
    assert _search_prompt(["anime", "animethemes"], 1) == "Search AnimeThemes · Theme"


def test_animethemes_command_parses_query():
    args, _ = parse_cli_args(["search", "animethemes:idol"])

    assert args.command == "search"
    assert args.animethemes == "search"
    assert args.query == ["idol"]


def test_animethemes_search_header_keeps_standard_four_line_shape():
    rows = flatten_search_bucket(
        "anime",
        [{
            "name": "[Oshi no Ko]",
            "year": 2023,
            "season": "Spring",
            "media_format": "TV",
            "animethemes": [{}, {}],
        }],
    )
    header = _search_header(
        "oshi no ko",
        "anime",
        rows,
        0,
    )

    lines = [strip_ansi(line) for line in header.splitlines()]
    assert len(lines) == 4
    assert lines[0] == "[Oshi no Ko]"
    assert lines[1] == "No alternative title"
    assert lines[2] == "TV • Spring 2023 • 2 themes"
    assert lines[3] == 'AnimeThemes • "oshi no ko" • Tab=switch • Enter=open • Esc=search'


def test_animethemes_theme_header_uses_theme_metadata_only():
    rows = flatten_search_bucket(
        "animethemes",
        [{
            "slug": "OP1",
            "type": "OP",
            "song": {"title": "Idol", "artists": [{"name": "YOASOBI"}]},
            "anime": {"name": "[Oshi no Ko]"},
        }],
    )
    header = _search_header("oshi no ko", "animethemes", rows, 0)

    lines = [strip_ansi(line) for line in header.splitlines()]
    assert lines[0] == "Idol"
    assert lines[1] == "No alternative title"
    assert lines[2] == "by YOASOBI · [Oshi no Ko] · OP 1"
    assert "Enter=play" in lines[3]
    assert "Anime 1" not in lines[3]


def test_animethemes_loading_header_matches_search_shape():
    header = _search_header(
        "oshi no ko",
        "anime",
        [],
        0,
        loading_msg=":: Searching...",
    )

    lines = [strip_ansi(line) for line in header.splitlines()]
    assert len(lines) == 4
    assert lines[1] == "Use Up/Down to browse previous searches."
    assert lines[2] == "Provider: AnimeThemes"
    assert lines[3] == ":: Searching..."


def test_animethemes_row_show_maps_images_to_existing_cover_fields():
    row = flatten_search_bucket(
        "anime",
        [{
            "name": "[Oshi no Ko]",
            "images": [{"link": "https://img.example/oshi.jpg"}],
        }],
    )[0]

    show = _row_show(row)

    assert show["thumbnail"] == "https://img.example/oshi.jpg"


def test_animethemes_theme_row_uses_nested_anime_image():
    row = flatten_search_bucket(
        "animethemes",
        [{
            "slug": "ED",
            "song": {"title": "Mephisto", "artists": [{"name": "Ziyouu-vachi"}]},
            "anime": {
                "name": "[Oshi no Ko]",
                "images": [{"link": "https://img.example/oshi-theme.jpg"}],
            },
        }],
    )[0]

    show = _row_show(row)

    assert show["name"] == "Mephisto ED"
    assert show["englishName"] == "Ziyouu-vachi"
    assert show["thumbnail"] == "https://img.example/oshi-theme.jpg"


def test_animethemes_theme_result_can_open_its_videos():
    rows = theme_video_rows({
        "slug": "ED",
        "song": {"title": "Mephisto", "artists": [{"name": "Ziyouu-vachi"}]},
        "anime": {"name": "[Oshi no Ko]"},
        "animethemeentries": [{
            "episodes": "2-10",
            "version": 1,
            "videos": [{
                "link": "https://v.animethemes.moe/OshiNoKo-ED.webm",
                "resolution": 720,
                "source": "WEB",
            }],
        }],
    })

    assert rows[0]["label"] == "ED  Mephisto  Ziyouu-vachi  720 WEB"
    assert rows[0]["anime"] == "[Oshi no Ko]"
    assert rows[0]["stream"]["link"] == "https://v.animethemes.moe/OshiNoKo-ED.webm"


def test_search_cover_status_uses_same_projection_for_probe_and_footer(monkeypatch):
    from media_cli.app import animethemes as flow

    probed = []
    monkeypatch.setattr(flow.app_core, "_get_poster", lambda show: probed.append(show) or "")
    def footer(show, text, width):
        assert probed and probed[-1] is show
        return "Cover unavailable"
    monkeypatch.setattr(flow.app_core, "_poster_footer_line", footer)
    for kind in ("theme", "artist", "studio", "playlist"):
        row = {"kind": kind, "label": "Example", "raw": {"id": 1, "name": "Example"}}
        assert "Cover unavailable" in flow._search_header("query", kind, [row])
