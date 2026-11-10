from threading import Event
import json

import pytest

from media_cli.app import animethemes_series as flow
from media_cli.app.animethemes import _search_view
from media_cli.services import animethemes as service
from media_cli.ui.picker_state import PickerState
from media_cli.ui.view_session import UiEvent


def test_series_request_uses_graphql_identity_and_anime_relation(monkeypatch):
    def request(url, data=None, **kwargs):
        assert url == service.GRAPHQL_URL
        assert json.loads(data)["variables"] == {"slug": "a/b"}
        return {"data": {"series": {
            "slug": "a/b", "title": {"romaji": "Collection"},
            "anime": {"nodes": [{"slug": "first", "title": {"romaji": "First"},
                                 "images": {"nodes": [{"link": "https://example.test/cover.jpg"}]},
                                 "themes": []}]},
        }}}
    monkeypatch.setattr(service, "request_json", request)
    series = service.get_series("a/b")
    assert series["name"] == "Collection"
    assert series["anime"][0]["name"] == "First"
    assert series["anime"][0]["images"][0]["link"].endswith("cover.jpg")


def make_controller():
    raw = {"slug": "collection", "name": "Collection"}
    rows = [{"kind": "series", "label": "Collection", "raw": raw}]
    source = _search_view("collection", "series", ["series"], rows)
    played = []
    controller = flow.SeriesController(raw, played.append, source_view=source,
                                       source_position=PickerState(query="coll", selected_index=0))
    return controller, played


def send(controller, kind, item_id=None):
    session = controller.session
    session.dispatch(UiEvent(kind, session.current.id, session.generation, item_id))


def finish(controller, completed):
    assert completed.wait(2)
    # Workers post their immutable destination after the service call returns.
    from time import monotonic, sleep
    deadline = monotonic() + 2
    while controller.session.navigation is not None and monotonic() < deadline:
        controller.session.drain_updates()
        sleep(0.005)
    assert controller.session.navigation is None


def test_series_nested_navigation_restores_both_positions(monkeypatch):
    loaded = Event()
    def series(slug):
        loaded.set()
        return {"name": "Collection", "anime": [{"slug": "first", "name": "First"},
                                                   {"slug": "second", "name": "Second"}]}
    monkeypatch.setattr(service, "get_series", series, raising=False)
    videos = Event()
    def anime(show):
        videos.set()
        return {"slug": "second", "name": "Second", "animethemes": [{
            "slug": "OP1", "song": {"title": "Song"}, "animethemeentries": [{
                "id": 1, "videos": [{"link": "https://example.test/song.webm", "resolution": 720}]}]}]}
    monkeypatch.setattr(service, "get_anime_for_show", anime)
    controller, played = make_controller()
    try:
        controller.start()
        finish(controller, loaded)
        assert [item.label for item in controller.session.current.items] == ["First", "Second"]
        controller.session.position.query = "sec"
        send(controller, "select", "anime:second")
        finish(controller, videos)
        assert controller.session.current.prompt == "Select action"
        send(controller, "select", "action:play")
        assert played[0]["stream"]["link"].endswith("song.webm")
        send(controller, "back")
        assert controller.session.position.query == "sec"
        assert controller.session.position.selected_id == "anime:second"
        send(controller, "back")
        assert controller.session.position.query == "coll"
        assert controller.session.position.selected_id == "result:0"
    finally:
        controller.close()


@pytest.mark.parametrize("failure", [False, True])
def test_empty_or_failed_series_keeps_back_available(monkeypatch, failure):
    loaded = Event()
    def series(slug):
        loaded.set()
        if failure:
            raise RuntimeError("offline")
        return {"anime": []}
    monkeypatch.setattr(service, "get_series", series, raising=False)
    controller, _ = make_controller()
    try:
        controller.start()
        finish(controller, loaded)
        if failure:
            assert controller.session.current.id == controller.source_id
            assert controller.session.current.message == "Could not load this selection. Try again."
        else:
            assert controller.session.current.state == "empty"
            send(controller, "back")
            assert controller.session.current.id == controller.source_id
    finally:
        controller.close()


def test_cancelled_series_does_not_open_when_worker_finishes(monkeypatch):
    started, release, posted = Event(), Event(), Event()
    def series(slug):
        started.set()
        release.wait(2)
        return {"anime": [{"slug": "late", "name": "Late"}]}
    monkeypatch.setattr(service, "get_series", series, raising=False)
    controller, _ = make_controller()
    original_post = controller.session.post
    def post(command):
        original_post(command)
        posted.set()
    monkeypatch.setattr(controller.session, "post", post)
    try:
        controller.start()
        assert started.wait(2)
        send(controller, "back")
        release.set()
        assert posted.wait(2)
        controller.session.drain_updates()
        assert controller.session.current.id == controller.source_id
        assert controller.session.depth == 1
    finally:
        release.set()
        controller.close()


def test_source_filter_edit_does_not_exit_series_session():
    controller, _ = make_controller()
    try:
        session = controller.session
        session.dispatch(UiEvent("query", session.current.id, session.generation, value="changed"))
        assert not session.exited
    finally:
        controller.close()


def test_nested_anime_metadata_matches_search_and_video_menu(monkeypatch):
    from media_cli.app.animethemes import _row_detail_values
    from media_cli.ui.view_model import resolve_details

    anime = {"slug": "second", "name": "Second", "media_format": "TV", "season": "summer",
             "year": 2024, "animethemes": [{"slug": "OP1"}, {"slug": "ED1"}],
             "images": [{"link": "https://example.test/second.jpg"}]}
    monkeypatch.setattr(service, "get_series", lambda slug: {"anime": [anime]})
    monkeypatch.setattr(service, "get_anime_for_show", lambda show: anime)
    controller, _ = make_controller()
    try:
        expected = _row_detail_values({"kind": "anime", "bucket": "anime", "raw": anime})[2]
        assert expected == "TV • Summer 2024 • 2 themes"
        titles = controller._titles()
        assert resolve_details(titles.details, titles.items[0].details).metadata == expected
        assert titles.items[0].image.url == "https://example.test/second.jpg"
        videos = controller._videos(anime)
        assert resolve_details(videos.details).metadata == expected
        assert videos.image.url == titles.items[0].image.url
    finally:
        controller.close()


def test_search_opens_series_through_shared_flow(monkeypatch):
    from types import SimpleNamespace

    from media_cli.app import animethemes as search_flow
    raw = {"slug": "collection", "name": "Collection"}
    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target
        def start(self):
            self.target()
    monkeypatch.setattr(search_flow.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(service, "search", lambda query: {"series": [raw]})
    monkeypatch.setattr(search_flow.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(search_flow.app_core, "make_info_fn", lambda *args: None)
    calls = []
    def open_series(*args, **kwargs):
        calls.append((args[-1], kwargs["source_view"].active_tab_id))
        return "quit"
    monkeypatch.setattr(flow, "open_series", open_series)
    def pick(*args, **kwargs):
        kwargs["live_fn"]()
        return 0
    monkeypatch.setattr(search_flow, "tui_pick", pick)
    result = search_flow.handle_animethemes_search_state(
        None, SimpleNamespace(), SimpleNamespace(query_str="collection"), {}, SimpleNamespace(), "sub")
    assert result == "QUIT"
    assert calls == [(raw, "series")]


def test_real_terminal_series_browsing_and_back(tmp_path):
    from test_ui_view_terminal import Terminal
    source = '''
from media_cli.app.animethemes_series import open_series
from media_cli.app.animethemes import _search_view
from media_cli.services import animethemes as service
from media_cli.ui.picker_state import PickerState
raw = dict(slug='collection', name='Collection')
service.get_series = lambda slug: dict(name='Collection', anime=[dict(slug='first', name='First')])
service.get_anime_for_show = lambda show: dict(name='First', slug='first', animethemes=[
    dict(slug='OP1', song=dict(title='Song'), animethemeentries=[dict(id=1, videos=[
        dict(link='https://example.test/song.webm', resolution=720)])])])
position = PickerState(query='coll', selected_index=0)
view = _search_view('collection', 'series', ['series'], [dict(kind='series', label='Collection', raw=raw)])
result = open_series(flags, ui, None, {}, None, raw, source_view=view, source_position=position)
finish(dict(kind=result.kind, query=position.query, selected=position.selected_index))
'''
    with Terminal(tmp_path, source) as terminal:
        terminal.send_after(b"Select anime", b"\r")
        terminal.send_after(b"Select action", b"\x1b[D")
        terminal.send_after(b"Select anime", b"\x1b[D")
        terminal.send_after(b"Search AnimeThemes", b"\x1b")
        assert terminal.result() == {"kind": "back", "query": "coll", "selected": 0}


def test_studio_reuses_collection_navigation_with_studio_context(monkeypatch):
    raw = {"slug": "studio", "name": "Studio"}
    source = _search_view("studio", "studios", ["studios"], [{"kind": "studio", "raw": raw, "label": "Studio"}])
    monkeypatch.setattr(service, "get_studio", lambda slug: {"name": "Studio", "anime": [
        {"slug": "anime", "name": "Anime", "images": []}]})
    controller = flow.SeriesController(raw, lambda row: None, source_view=source,
                                      source_position=PickerState(), collection_kind="studio")
    view = controller._titles()
    assert view.context == ("AnimeThemes", "Studio")
    assert view.id == "studio:studio"
    assert len(view.items) == 1
    controller.close()


def test_complete_collection_anime_does_not_fetch_again(monkeypatch):
    anime = {"slug": "second", "name": "Second", "animethemes": [{
        "slug": "OP1", "song": {"title": "Song"}, "animethemeentries": [{
            "videos": [{"link": "https://example.test/song.webm", "resolution": 720}]}]}]}
    monkeypatch.setattr(service, "get_anime_for_show", lambda *a: pytest.fail("Redundant anime request"))
    controller, _ = make_controller()
    try:
        assert controller._videos(anime).prompt == "Select action"
        assert controller._cache[("anime", "second")] is anime
    finally:
        controller.close()
