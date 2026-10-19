from threading import Event
import time
from types import SimpleNamespace

from media_cli.app.animethemes import _row_detail_values, _search_view
from media_cli.app.animethemes_artist_controller import ArtistController
from media_cli.services import animethemes as service
from media_cli.ui.view_model import resolve_details
from media_cli.ui.view_session import UiEvent


ARTIST = {
    "id": 155, "slug": "miku_itou", "name": "Miku Itou",
    "groups": ({"name": "Group", "slug": "group"},),
    "images": ({"link": "https://example.test/portrait.jpg"},),
}
ROW = {"label": "OP Song", "raw": {"id": 11, "type": "OP", "song": {"title": "Song"},
                                     "anime": {"name": "Anime", "slug": "anime"}}}
SECTION = {"id": "direct", "label": "Song Performances", "rows": (ROW,)}


def test_search_to_artist_preserves_actual_result_and_all_details_until_ready(monkeypatch):
    rows = ({"kind": "artist", "label": "Miku Itou", "raw": ARTIST},)
    source = _search_view("oshi no ko", "artists", ("anime", "artists"), rows)
    position = SimpleNamespace(query="", scroll=0, selected_index=0)
    controller = ArtistController(ARTIST, lambda row: None, source_view=source, source_position=position)
    jobs = []
    monkeypatch.setattr(controller, "_submit", lambda fn, *args: jobs.append((fn, args)))
    monkeypatch.setattr(service, "get_artist", lambda slug: ARTIST)
    monkeypatch.setattr(service, "artist_sections", lambda artist: (SECTION,))
    try:
        controller.start()
        session = controller.session
        assert session.current is source
        assert session.item().label == "Miku Itou"
        details = resolve_details(source.details, session.item().details)
        assert (details.title, details.secondary, details.metadata) == _row_detail_values(rows[0])
        assert details.secondary == "Member of Group"
        assert details.metadata == "ARTIST"
        assert session.item().image.url == ARTIST["images"][0]["link"]
        assert session.navigation.message == "Fetching artist performances"
        assert session.current.prompt == "Search AnimeThemes"
        function, arguments = jobs.pop()
        function(*arguments)
        assert session.current is source
        session.drain_updates()
        assert session.current.id == controller.root_id
        assert session.current.prompt == "Select group"
        assert session.item().label == "Song Performances"
        assert session.navigation is None
        assert session.depth == 1
    finally:
        controller.close()


def test_artist_loading_failure_retains_complete_source_and_can_retry(monkeypatch):
    controller = ArtistController(ARTIST, lambda row: None)
    jobs = []
    monkeypatch.setattr(controller, "_submit", lambda fn, *args: jobs.append((fn, args)))
    monkeypatch.setattr(service, "get_artist", lambda slug: (_ for _ in ()).throw(RuntimeError("offline")))
    try:
        source = controller.session.current
        controller.start()
        function, arguments = jobs.pop()
        function(*arguments)
        controller.session.drain_updates()
        current = controller.session.current
        assert current.items == source.items
        assert current.details == source.details
        assert current.prompt == source.prompt
        assert "Could not load" in current.message
        controller.session.dispatch(UiEvent("select", current.id, controller.session.generation, "artist"))
        assert controller.session.navigation is not None
        assert jobs
    finally:
        controller.close()


def test_song_to_video_preserves_source_and_cancel_ignores_result(monkeypatch):
    controller = ArtistController(ARTIST, lambda row: None)
    jobs = []
    monkeypatch.setattr(controller, "_submit", lambda fn, *args, **kwargs: jobs.append((fn, args)))
    monkeypatch.setattr(controller, "_enriched", lambda row: row)
    monkeypatch.setattr(service, "theme_video_rows", lambda raw: ({
        "label": "1080p", "raw": {"entry": {"id": 1}}, "stream": {"link": "https://example.test/video"},
    },))
    try:
        controller.install_sections((SECTION,))
        session = controller.session
        session.dispatch(UiEvent("select", session.current.id, session.generation, "section:direct"))
        source = session.current
        session.dispatch(UiEvent("select", source.id, session.generation, "theme:11"))
        assert session.current is source
        assert session.item().label == "OP Song"
        assert session.navigation.message == "Fetching song videos"
        session.dispatch(UiEvent("back", source.id, session.generation))
        function, arguments = jobs.pop()
        function(*arguments)
        session.drain_updates()
        assert session.current is source
        assert session.depth == 2
    finally:
        controller.close()


def test_navigation_transitions_are_logged(caplog):
    from media_cli.ui.view_session import BeginNavigation, CompleteNavigation, FailNavigation
    with caplog.at_level("DEBUG", logger="media_cli.ui.view_session"):
        controller = ArtistController(ARTIST, lambda row: None)
        try:
            session = controller.session
            view_id, generation = session.current.id, session.generation
            session.apply(BeginNavigation(view_id, generation, "first", "Fetching"))
            session.cancel_navigation()
            assert not session.apply(CompleteNavigation(view_id, generation, "first", controller.sections_view((SECTION,))))
            session.apply(BeginNavigation(view_id, generation, "retry", "Fetching"))
            session.apply(FailNavigation(view_id, generation, "retry", "offline"))
            session.apply(BeginNavigation(view_id, generation, "third", "Fetching"))
            session.apply(CompleteNavigation(view_id, generation, "third", controller.sections_view((SECTION,))))
        finally:
            controller.close()
    assert all(message in caplog.text for message in (
        "Navigation started", "Navigation cancelled", "stale source", "Navigation failed", "Navigation completed",
    ))


def test_cancelled_artist_request_does_not_swallow_immediate_retry(monkeypatch):
    controller = ArtistController(ARTIST, lambda row: None)
    first_started, release_first, retried = Event(), Event(), Event()
    calls = []

    def get_artist(slug):
        calls.append(slug)
        if len(calls) == 1:
            first_started.set()
            assert release_first.wait(2)
        else:
            retried.set()
        return ARTIST

    monkeypatch.setattr(service, "get_artist", get_artist)
    monkeypatch.setattr(service, "artist_sections", lambda artist: (SECTION,))
    try:
        controller.start()
        assert first_started.wait(1)
        session = controller.session
        session.dispatch(UiEvent("back", session.current.id, session.generation))
        session.dispatch(UiEvent("select", session.current.id, session.generation, "artist"))
        retry_request = session.navigation.request_id
        release_first.set()
        assert retried.wait(1), "Retry was dropped while cancelled request was still active"
        deadline = time.monotonic() + 1
        while session.current.id != controller.root_id and time.monotonic() < deadline:
            session.drain_updates()
            time.sleep(0.01)
        assert session.current.id == controller.root_id
        assert session.navigation is None
        assert retry_request == "artist:2"
    finally:
        release_first.set()
        controller.close()


def test_search_handoff_retains_filter_scroll_and_new_selection(monkeypatch):
    from media_cli.app import animethemes_artist as flow
    from media_cli.ui import view_picker
    from media_cli.ui.picker_state import PickerState

    other = {**ARTIST, "id": 156, "slug": "other_itou", "name": "Other Itou"}
    rows = ({"kind": "artist", "label": "Miku Itou", "raw": ARTIST},
            {"kind": "artist", "label": "Other Itou", "raw": other})
    source = _search_view("itou", "artists", ("artists",), rows)
    position = PickerState(query="Itou", scroll=1)
    monkeypatch.setattr(ArtistController, "_submit", lambda *args, **kwargs: None)

    def choose_another(flags, ui, session):
        session.cancel_navigation()
        session.position.query = "Itou"
        session.position.scroll = 2
        session.dispatch(UiEvent("select", session.current.id, session.generation, "result:1"))
        return session.result

    monkeypatch.setattr(view_picker, "run_view_session", choose_another)
    result = flow.open_artist(None, None, None, None, None, ARTIST,
                              source_view=source, source_position=position)
    assert result.item_id == "result:1"
    assert (position.query, position.scroll, position.selected_index) == ("Itou", 2, 1)
    controller = ArtistController(other, lambda row: None, source_view=source, source_position=position)
    try:
        assert controller.session.item().label == "Other Itou"
        assert controller.session.position.query == "Itou"
        assert controller.session.position.scroll == 2
    finally:
        controller.close()
