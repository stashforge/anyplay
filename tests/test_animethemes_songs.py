"""Song choices retain their complete set of playable video variants."""

from copy import deepcopy

from media_cli.services import animethemes as service


def anime_fixture():
    return {
        "id": 10, "slug": "anime", "name": "Anime", "year": 2024,
        "images": [{"link": "https://example.test/cover.jpg"}],
        "animethemes": [
            {"id": 11, "slug": "OP1", "type": "OP", "song": {
                "id": 101, "title": "Opening", "artists": [{"name": "Singer"}]},
             "animethemeentries": [
                 {"id": 21, "version": 1, "episodes": "1-6", "spoiler": True,
                  "videos": [
                      {"id": 31, "link": "https://example.test/op720.webm", "resolution": 720, "source": "WEB"},
                      {"id": 32, "link": "https://example.test/op1080.webm", "resolution": 1080, "source": "BD", "nc": True}]},
                 {"id": 22, "version": 2, "episodes": "7-12", "videos": [
                     {"id": 33, "link": "https://example.test/opv2.webm", "resolution": 720, "source": "WEB"}]},
             ]},
            {"id": 12, "slug": "ED1", "type": "ED", "song": {"id": 102, "title": "Ending"},
             "animethemeentries": [{"id": 23, "videos": [
                 {"id": 34, "link": "https://example.test/ed.webm", "resolution": 720}]}]},
        ],
    }


def test_song_choices_do_not_repeat_for_quality_or_animation_version():
    rows = service.anime_song_rows(anime_fixture())
    assert [row["label"] for row in rows] == ["Opening OP 1", "Ending ED 1"]
    assert [row["kind"] for row in rows] == ["theme", "theme"]
    assert [row["raw"]["id"] for row in rows] == [11, 12]
    assert [len(row["variants"]) for row in rows] == [3, 1]


def test_variants_preserve_existing_playback_rows_without_default_selection():
    anime = anime_fixture()
    rows = service.anime_song_rows(anime)
    assert isinstance(rows[0]["variants"], tuple)
    assert [variant for row in rows for variant in row["variants"]] == service.flatten_anime_videos(anime)
    assert [row["theme"] for row in rows[0]["variants"]] == ["OP1", "OP1", "OP1v2"]
    assert "stream" not in rows[0]


def test_equal_song_titles_and_ids_do_not_merge_distinct_theme_performances():
    anime = anime_fixture()
    anime["animethemes"][1]["song"] = deepcopy(anime["animethemes"][0]["song"])
    rows = service.anime_song_rows(anime)
    assert len(rows) == 2
    assert [row["raw"]["id"] for row in rows] == [11, 12]
    assert [row["label"] for row in rows] == ["Opening OP 1", "Opening ED 1"]


def test_song_row_labels_only_use_supplied_opening_or_ending_labels():
    assert service.song_row_label("Song", {"slug": "OP2"}) == "Song OP 2"
    assert service.song_row_label("Song", {"type": "ED", "sequence": 3}) == "Song ED 3"
    assert service.song_row_label("Song", {"slug": "OP1v2"}) == "Song OP 1"
    assert service.song_row_label("Song", {"slug": "IN1"}) == "Song"
    assert service.song_row_label("Song", {}) == "Song"


def test_song_projection_keeps_anime_context_without_mutating_source():
    anime = anime_fixture()
    before = deepcopy(anime)
    row = service.anime_song_rows(anime)[0]
    assert anime == before
    assert row["bucket"] == "animethemes"
    assert row["anime"] == "Anime"
    assert row["song"] == "Opening"
    assert row["artists"] == "Singer"
    assert row["raw"]["anime"]["slug"] == "anime"
    assert row["raw"]["anime"]["images"] == anime["images"]


def test_empty_and_unplayable_themes_do_not_create_song_choices():
    assert service.anime_song_rows({}) == []
    anime = anime_fixture()
    anime["animethemes"][0]["animethemeentries"] = [{"videos": [{"resolution": 720}]}]
    assert [row["raw"]["id"] for row in service.anime_song_rows(anime)] == [12]


def test_missing_theme_ids_do_not_collapse_separate_choices():
    anime = anime_fixture()
    for theme in anime["animethemes"]:
        del theme["id"]
    assert len(service.anime_song_rows(anime)) == 2
