from media_cli.app.animethemes_artist_controller import ArtistController
from media_cli.ui.view_model import Blank, DetailLines, resolve_details
from media_cli.ui.view_session import UiEvent


ARTIST = {"id": 155, "slug": "miku_itou", "name": "Miku Itou", "groups": [],
          "images": [{"link": "https://example.test/artist.jpg"}]}
ROW = {"kind": "theme", "bucket": "animethemes", "label": "ED3  Song", "credit": {"as": "An Akagi"},
       "raw": {"id": 11, "type": "ED", "sequence": 3, "song": {"id": 1, "title": "Song"},
               "anime": {"name": "Anime", "slug": "anime"}}}
SECTION = {"id": "direct", "label": "Song Performances", "rows": [ROW]}


def select(session, item_id):
    session.dispatch(UiEvent("select", session.current.id, session.generation, item_id))


def test_artist_sections_and_song_layout_use_shared_view_contract():
    controller = ArtistController(ARTIST, lambda row: None)
    controller.artist = ARTIST
    root = controller.sections_view([SECTION])
    assert resolve_details(root.details, root.items[0].details).title == "Artist: Miku Itou"
    assert root.items[0].hint == "1 theme"
    songs = controller.songs_view(SECTION)
    song = songs.items[0]
    details = resolve_details(songs.details, song.details)
    assert details.title == "Song"
    assert details.secondary == "Artist: Miku Itou • As An Akagi"
    assert details.metadata == "ED3 • Anime"
    assert song.image.state == "resolving"
    assert songs.context == ("AnimeThemes", "Artist")
    controller.close()


def test_artist_navigation_preserves_parent_position_and_uses_video_boundary(monkeypatch):
    played = []
    controller = ArtistController(ARTIST, played.append)
    controller.artist = ARTIST
    sections = [SECTION, {**SECTION, "id": "group", "label": "In Group"}]
    controller.install_sections(sections)
    session = controller.session
    root = session.current.id
    session.position.query = "Performances"
    select(session, "section:direct")
    assert session.current.id != root
    assert session.item().id == "theme:11"
    session.dispatch(UiEvent("back", session.current.id, session.generation))
    assert session.current.id == root
    assert session.position.query == "Performances"
    assert session.position.selected_id == "section:direct"
    controller.close()


def test_cover_failure_is_explicit_and_does_not_erase_song_metadata(monkeypatch):
    controller = ArtistController(ARTIST, lambda row: None)
    controller.artist = ARTIST
    controller.install_sections([SECTION])
    select(controller.session, "section:direct")
    session = controller.session
    original = session.item()
    from media_cli.services import animethemes as service
    monkeypatch.setattr(service, "enrich_artist_theme", lambda row: (_ for _ in ()).throw(RuntimeError("offline")))
    controller.enrich_item(session.current.id, session.generation, original)
    session.drain_updates()
    assert session.item().image.state == "failed"
    assert session.item().details == original.details
    controller.close()


def test_shared_video_url_in_distinct_entries_keeps_distinct_items(monkeypatch):
    from media_cli.services import animethemes as service
    from media_cli.ui.view_model import ViewSpec
    from media_cli.ui.view_session import ViewSession
    controller = ArtistController(ARTIST, lambda row: None)
    item = controller.song_item(ROW, SECTION)
    controller.session = ViewSession(ViewSpec("videos", "Select video", state="loading", details=DetailLines(Blank, Blank, Blank)))
    monkeypatch.setattr(controller, "_enriched", lambda row: row)
    monkeypatch.setattr(service, "theme_video_rows", lambda raw: [
        {"label": "Version 1", "stream": {"link": "https://example.test/video"}, "raw": {"entry": {"id": 1}}},
        {"label": "Version 2", "stream": {"link": "https://example.test/video"}, "raw": {"entry": {"id": 2}}},
    ])
    controller._load_videos("videos", controller.session.generation, item)
    controller.session.drain_updates()
    assert controller.session.current.state == "ready"
    assert len({i.id for i in controller.session.current.items}) == 2
    controller.close()


def test_artist_quit_is_distinct_from_back():
    controller = ArtistController(ARTIST, lambda row: None)
    controller.session.dispatch(UiEvent("quit", controller.session.current.id, controller.session.generation))
    assert controller.session.result == "quit"
    controller.close()
    controller = ArtistController(ARTIST, lambda row: None)
    controller.session.dispatch(UiEvent("back", controller.session.current.id, controller.session.generation))
    assert controller.session.result == "back"
    controller.close()


def test_hover_queue_is_bounded_and_cannot_delay_explicit_selection(monkeypatch):
    from threading import Event, Lock
    controller = ArtistController(ARTIST, lambda row: None)
    rows = [{**ROW, "label": f"Song {i}", "raw": {**ROW["raw"], "id": i}} for i in range(5)]
    controller.install_sections([{**SECTION, "rows": rows}])
    select(controller.session, "section:direct")
    release, first_started, both_started, selected = Event(), Event(), Event(), Event()
    started = set()
    lock = Lock()
    def slow_hover(view_id, generation, item):
        with lock:
            started.add(item.id)
            first_started.set()
            if len(started) == 2:
                both_started.set()
        release.wait(2)
    monkeypatch.setattr(controller, "enrich_item", slow_hover)
    monkeypatch.setattr(controller, "_load_videos", lambda *args: selected.set())
    session = controller.session
    try:
        for i in range(2):
            session.dispatch(UiEvent("focus", session.current.id, session.generation, f"theme:{i}"))
            if i == 0:
                assert first_started.wait(1)
        assert both_started.wait(1)
        for i in range(2, 5):
            session.dispatch(UiEvent("focus", session.current.id, session.generation, f"theme:{i}"))
        assert controller._jobs.pending_count <= 1
        select(session, "theme:4")
        assert selected.wait(0.5), "selection was queued behind hover work"
        assert all(thread.daemon for thread in controller._jobs.threads)
    finally:
        controller.close()
        release.set()


def test_quit_does_not_wait_for_in_flight_group_requests():
    import subprocess
    import sys
    script = '''
from threading import Event
from media_cli.app.animethemes_artist_controller import ArtistController
from media_cli.services import animethemes as service
started = Event()
blocked = Event()
def artist(slug, **kwargs):
    if slug == "group":
        started.set()
        blocked.wait(20)
    return {"id": 1, "slug": "artist", "name": "Artist", "groups": [{"slug": "group", "name": "Group"}]}
service.get_artist = artist
controller = ArtistController({"slug": "artist", "name": "Artist"}, lambda row: None)
controller.start()
assert started.wait(2)
controller.close()
print("closed", flush=True)
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=4)
    assert result.returncode == 0
    assert "closed" in result.stdout


def test_failed_artist_lookup_does_not_leave_a_fetching_cover_spinner(monkeypatch):
    from media_cli.services import animethemes as service
    from media_cli.ui.view_presentation import ViewPresentation
    from types import SimpleNamespace

    controller = ArtistController({**ARTIST, "slug": "missing", "name": "Missing"}, lambda row: None)
    source = controller.session.current
    portrait = controller.session.item().image
    monkeypatch.setattr(service, "get_artist", lambda slug: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(controller, "_submit", lambda callback, *args: callback(*args))
    controller.start()
    assert controller.session.navigation is not None
    controller.session.drain_updates()
    assert controller.session.current.id == source.id
    assert controller.session.current.items == source.items
    assert controller.session.current.details == source.details
    assert controller.session.current.state == "ready"
    assert controller.session.navigation is None
    assert controller.session.current.message == "Could not load artist performances"
    assert controller.session.item().image == portrait
    assert portrait.state == "available"
    presentation = ViewPresentation(controller.session, SimpleNamespace(enabled=lambda: True, needs_tick=lambda show: False))
    assert presentation.status_text() == "Could not load artist performances"
    assert not presentation.needs_tick()
    controller.close()
