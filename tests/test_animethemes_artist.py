import re
from types import SimpleNamespace

from media_cli.app import animethemes_artist as flow
from media_cli.app.animethemes import _row_show, _search_header
from media_cli.services import animethemes as service


def artist_fixture():
    return {
        "id": 155, "slug": "miku_itou", "name": "Miku Itou",
        "images": [{"link": "https://example.test/artist.jpg"}],
        "groups": [{"name": "Group", "slug": "group", "artistmember": {"as": "An Akagi"}}],
        "songs": [{"id": 1, "title": "Song", "artistsong": {"as": "An Akagi"},
                   "animethemes": [{"id": 11, "type": "ED", "sequence": 3,
                                    "anime": {"name": "Anime", "slug": "anime"}}]}],
        "memberPerformances": [{"song": {"id": 2}}],
    }


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_group_rows_only_include_artist_participation():
    owner = artist_fixture()
    group = {"name": "Group", "songs": [
        {"id": 2, "title": "Together", "animethemes": [{"id": 22, "type": "OP", "sequence": None}]},
        {"id": 3, "title": "Someone else", "animethemes": [{"id": 33, "type": "ED"}]},
    ]}
    rows = service.artist_theme_rows(group, owner)
    assert [r["label"] for r in rows] == ["OP  Together"]
    assert rows[0]["artist"] == "Miku Itou"


def test_sections_use_real_counts_and_skip_empty_groups(monkeypatch):
    artist = artist_fixture()
    group = {"songs": [{"id": 2, "title": "Together", "animethemes": [{"id": 22, "type": "OP"}]}]}
    monkeypatch.setattr(service, "get_artist", lambda slug: group)
    sections = service.artist_sections(artist)
    assert [s["label"] for s in sections] == ["Song Performances", "In Group"]
    assert sections[1]["credit"] == "An Akagi"
    assert len(sections[1]["rows"]) == 1


def test_artist_headers_preserve_four_lines_and_identity():
    artist = artist_fixture()
    section = {"label": "In Group", "group": "Group", "credit": "An Akagi"}
    header = plain(flow.artist_header(artist, section))
    assert len(header.splitlines()) == 4
    assert header.splitlines()[0] == "Artist: Miku Itou"
    assert header.splitlines()[1] == "In Group • As An Akagi"
    assert "1 direct theme • 1 group" in header
    row = service.artist_theme_rows(artist)[0]
    header = plain(flow.artist_header(artist, section, row))
    assert len(header.splitlines()) == 4
    assert header.splitlines()[0] == "Song"
    assert "Artist: Miku Itou • As An Akagi" in header
    assert header.splitlines()[2] == "ED3 • Anime"


def test_artist_search_uses_portrait_and_membership():
    row = {"kind": "artist", "bucket": "artists", "raw": artist_fixture()}
    show = _row_show(row)
    assert show["thumbnail"].endswith("artist.jpg")
    header = plain(_search_header("miku", "artists", [row]))
    assert "Artist: Miku Itou" in header
    assert "Member of Group" in header
    assert "No alternative title" not in header


def test_song_enrichment_keeps_exact_theme_and_fetches_credits(monkeypatch):
    row = service.artist_theme_rows(artist_fixture())[0]
    monkeypatch.setattr(service, "request_json", lambda *a, **kw: {
        "song": {"id": 1, "title": "Song", "artists": [{"name": "Miku Itou"}]}})
    monkeypatch.setattr(service, "_anime_detail_by_slug", lambda slug: {
        "name": "Anime", "images": [{"link": "https://example.test/cover.jpg"}],
        "animethemes": [{"id": 99}, {"id": 11, "type": "ED", "sequence": 3, "animethemeentries": [{"videos": []}]}]})
    enriched = service.enrich_artist_theme(row)
    assert enriched["raw"]["id"] == 11
    assert enriched["raw"]["song"]["artists"][0]["name"] == "Miku Itou"
    assert _row_show(enriched)["thumbnail"].endswith("cover.jpg")


def test_one_performance_section_opens_directly(monkeypatch):
    artist = artist_fixture()
    section = {"label": "Song Performances", "rows": service.artist_theme_rows(artist)}
    monkeypatch.setattr(service, "get_artist", lambda slug: artist)
    monkeypatch.setattr(service, "artist_sections", lambda a: [section])
    monkeypatch.setattr(flow.app_core, "with_loading", lambda msg, fn, *args: fn(*args))
    calls = []
    monkeypatch.setattr(flow, "_artist_songs", lambda *args: calls.append(args[-1]))
    flow.open_artist(None, None, None, {}, None, artist)
    assert calls == [section]


def test_group_picker_uses_standard_hints_and_back(monkeypatch):
    artist = artist_fixture()
    sections = [{"label": name, "rows": service.artist_theme_rows(artist)} for name in ["Song Performances", "In Group"]]
    monkeypatch.setattr(service, "get_artist", lambda slug: artist)
    monkeypatch.setattr(service, "artist_sections", lambda a: sections)
    monkeypatch.setattr(flow.app_core, "with_loading", lambda msg, fn, *args: fn(*args))
    monkeypatch.setattr(flow.app_core, "make_single_show_info_fn", lambda *args: None)
    picks = iter([1, -1])
    def pick(*args, **kwargs):
        assert kwargs["hints"]["In Group"] == "1 theme"
        assert "Artist: Miku Itou" in kwargs["header_fn"](1)
        return next(picks)
    monkeypatch.setattr(flow, "tui_pick", pick)
    calls = []
    monkeypatch.setattr(flow, "_artist_songs", lambda *args: calls.append(args[-1]))
    flow.open_artist(None, SimpleNamespace(), None, {}, None, artist)
    assert calls == [sections[1]]


def test_single_artist_auto_opens_once_and_back_stays_in_results(monkeypatch):
    from media_cli.app import animethemes as search_flow
    artist = artist_fixture()
    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(search_flow.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(service, "search", lambda query: {"artists": [artist]})
    monkeypatch.setattr(search_flow.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(search_flow.app_core, "make_info_fn", lambda *args: None)
    opened = []
    monkeypatch.setattr(flow, "open_artist", lambda *args: opened.append(args[-1]))
    count = 0
    def pick(*args, **kwargs):
        nonlocal count
        kwargs["live_fn"]()
        count += 1
        assert kwargs["auto_select_single_when_done"]() == (count == 1)
        return 0 if count == 1 else -1
    monkeypatch.setattr(search_flow, "tui_pick", pick)
    result = search_flow.handle_animethemes_search_state(
        None, SimpleNamespace(), SimpleNamespace(query_str="miku"), {}, SimpleNamespace(query=["miku"]), "sub")
    assert result == "QUIT"
    assert opened == [artist]


def test_song_picker_updates_poster_selection_before_rendering(monkeypatch):
    artist = artist_fixture()
    row = service.artist_theme_rows(artist)[0]
    row["raw"]["anime"]["images"] = [{"link": "https://example.test/cover.jpg"}]
    ui = SimpleNamespace(hovered_show_id=155, hovered_show_obj={"_id": 155})
    monkeypatch.setattr(flow.app_core, "make_info_fn", lambda *args: None)
    def poster(show):
        assert ui.hovered_show_id == show["_id"] == 11
        assert flow.app_core._hovered_show_id == 11
        assert ui.hovered_show_obj is show
        assert show["thumbnail"].endswith("cover.jpg")
        show["_poster_status"] = "loading"
        return "COVER"
    monkeypatch.setattr(flow.app_core, "_get_poster", poster)
    def pick(*args, **kwargs):
        assert kwargs["top_header_fn"](0) == "COVER"
        assert ui.hovered_show_obj["_poster_status"] == "loading"
        return -1
    monkeypatch.setattr(flow, "tui_pick", pick)
    flow._artist_songs(None, ui, None, {}, None, artist, {"rows": [row]})


def test_video_picker_updates_poster_selection(monkeypatch):
    from media_cli.app import animethemes as search_flow
    show = {"_id": 11, "thumbnail": "https://example.test/cover.jpg"}
    ui = SimpleNamespace(hovered_show_id=155)
    def poster(selected):
        assert ui.hovered_show_id == 11
        assert ui.hovered_show_obj is selected
        assert search_flow.app_core._hovered_show_id == 11
        return "COVER"
    monkeypatch.setattr(search_flow.app_core, "_get_poster", poster)
    def pick(*args, **kwargs):
        assert kwargs["top_header_fn"](0) == "COVER"
        return -1
    monkeypatch.setattr(search_flow, "tui_pick", pick)
    search_flow._pick_video_rows(None, ui, None, {}, None, show, [{"label": "720p"}])


def test_background_cover_update_requests_redraw_even_when_metadata_is_unchanged(monkeypatch):
    artist = artist_fixture()
    row = service.artist_theme_rows(artist)[0]
    ui = SimpleNamespace()
    ticks = iter([0.0, 5.0])
    monkeypatch.setattr(flow.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(flow.app_core, "make_info_fn", lambda *args: None)
    redrawn = []
    monkeypatch.setattr(flow.app_core, "_request_poster_redraw", lambda: redrawn.append(True))
    def enrich(old):
        return {**old, "raw": {**old["raw"], "anime": {
            **old["raw"]["anime"], "images": [{"link": "https://example.test/cover.jpg"}]}}}
    monkeypatch.setattr(service, "enrich_artist_theme", enrich)
    class ImmediateThread:
        def __init__(self, target, args, **kwargs):
            self.target, self.args = target, args
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(flow.threading, "Thread", ImmediateThread)
    def pick(*args, **kwargs):
        before = kwargs["header_fn"](0)
        kwargs["live_fn"]()
        assert kwargs["header_fn"](0) == before
        assert redrawn == [True]
        return -1
    monkeypatch.setattr(flow, "tui_pick", pick)
    flow._artist_songs(None, ui, None, {}, None, artist, {"rows": [row]})
