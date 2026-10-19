from dataclasses import replace

import pytest

from media_cli.ui.view_model import Blank, Content, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_session import (
    BeginNavigation, CompleteNavigation, FailNavigation, PushView, UiEvent, ViewSession,
)


def source():
    return ViewSpec("artists", "Search AnimeThemes", (
        ViewItem("miku", "Miku Itou", details=Inherit),
        ViewItem("other", "Other artist", details=Inherit),
    ), details=DetailLines(Content("Artist: Miku Itou"), Content("Member of Group"), Content("ARTIST")))


def destination():
    return ViewSpec("groups", "Select group", (ViewItem("direct", "Song Performances", details=Inherit),),
                    details=DetailLines(Content("Artist: Miku Itou"), Blank, Content("48 direct themes")))


def begin(session, request="request"):
    session.apply(BeginNavigation(session.current.id, session.generation, request, "Fetching performances"))


def test_navigation_preserves_complete_source_and_switches_atomically():
    session = ViewSession(source())
    session.position.query = "miku"
    session.position.scroll = 3
    initial = session.current
    begin(session)
    assert session.current is initial
    assert session.depth == 1
    assert session.navigation.message == "Fetching performances"
    assert session.auto_event() is None
    session.post(CompleteNavigation(initial.id, session.generation, "request", destination()))
    session.drain_updates()
    assert session.current == destination()
    assert session.depth == 2
    assert session.navigation is None
    session.dispatch(UiEvent("back", session.current.id, session.generation))
    assert session.current is initial
    assert session.position.query == "miku"
    assert session.position.scroll == 3


def test_back_cancels_pending_navigation_and_late_completion_is_ignored():
    session = ViewSession(source())
    begin(session)
    generation = session.generation
    assert session.dispatch(UiEvent("back", session.current.id, generation))
    assert session.depth == 1 and not session.exited
    assert session.navigation is None
    assert not session.apply(CompleteNavigation("artists", generation, "request", destination()))
    assert session.current.id == "artists"


def test_failure_preserves_source_fields_and_reports_message():
    session = ViewSession(source())
    initial = session.current
    begin(session)
    assert session.apply(FailNavigation(initial.id, session.generation, "request", "Request failed"))
    assert session.current.items == initial.items
    assert session.current.details == initial.details
    assert session.current.prompt == initial.prompt
    assert session.current.state == "ready"
    assert session.current.message == "Request failed"
    assert session.navigation is None


def test_obsolete_request_cannot_replace_newer_navigation():
    session = ViewSession(source())
    begin(session, "old")
    begin(session, "new")
    generation = session.generation
    assert not session.apply(CompleteNavigation("artists", generation, "old", destination()))
    assert session.navigation.request_id == "new"
    assert session.apply(CompleteNavigation("artists", generation, "new", destination(), replace_current=True))
    assert session.depth == 1 and session.current.id == "groups"


def test_pending_destination_cannot_be_activated_as_loading_view():
    from media_cli.ui.view_session import ReplaceView
    session = ViewSession(source())
    initial = session.current
    begin(session)
    incomplete = replace(destination(), state="loading")
    with pytest.raises(ValueError, match="loading"):
        session.apply(CompleteNavigation("artists", session.generation, "request", incomplete))
    assert session.current is initial
    with pytest.raises(ValueError, match="loading"):
        session.apply(PushView(incomplete))
    with pytest.raises(ValueError, match="loading"):
        session.apply(ReplaceView(initial.id, session.generation, incomplete))
    with pytest.raises(ValueError, match="loading"):
        session.apply(ReplaceView(initial.id, session.generation, replace(initial, state="loading"), new_activation=True))
    assert session.current is initial


def test_focusing_another_result_cancels_pending_navigation():
    session = ViewSession(source())
    begin(session)
    generation = session.generation
    session.dispatch(UiEvent("focus", "artists", generation, "other"))
    assert session.navigation is None
    assert not session.apply(CompleteNavigation("artists", generation, "request", destination()))


def test_removing_source_item_cancels_pending_navigation():
    from media_cli.ui.view_session import UpdateItems
    session = ViewSession(source())
    begin(session)
    generation = session.generation
    session.apply(UpdateItems("artists", generation, (session.current.items[1],), replace_all=True))
    assert session.navigation is None
    assert not session.apply(CompleteNavigation("artists", generation, "request", destination()))


def test_browse_key_is_ignored_while_destination_is_pending(monkeypatch):
    import sys
    from types import SimpleNamespace
    from media_cli.ui.view_picker import run_view_session

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    session = ViewSession(source())
    begin(session)
    generation = session.generation
    calls = []

    def picker(flags, ui, prompt, options, **hooks):
        calls.append(options)
        if len(calls) == 1:
            from media_cli.ui.input_result import KeyResult
            assert hooks["key_fn"]("UP", 0) is KeyResult.CONSUMED
            assert session.navigation is not None
            session.post(CompleteNavigation("artists", generation, "request", destination()))
            hooks["live_fn"]("")
            return -6
        assert prompt == "Select group"
        return -5

    manager = SimpleNamespace(enabled=lambda: False, loading_frame=lambda: "*", needs_tick=lambda show: False)
    run_view_session(SimpleNamespace(plain_mode=False, show_image=False), SimpleNamespace(), session,
                     picker=picker, poster_manager=manager)
    assert calls[1] == ["Song Performances"]


def test_queued_item_updates_snapshot_mutable_input():
    from media_cli.ui.view_session import UpdateItems
    items = [source().items[0]]
    images = [("miku", "portrait")]
    command = UpdateItems("artists", 1, items, expected_images=images)
    items.clear()
    images.clear()
    assert len(command.items) == 1
    assert command.expected_images == (("miku", "portrait"),)


def test_pending_navigation_and_cover_loading_are_both_visible():
    from media_cli.ui.poster import PosterManager
    from media_cli.ui.view_model import ImageSpec
    from media_cli.ui.view_presentation import ViewPresentation
    from media_cli.core.terminal import strip_ansi

    session = ViewSession(replace(source(), image=ImageSpec("portrait", "available", "https://example.test/portrait")), loading_delay=0)
    begin(session)
    manager = PosterManager(enabled=lambda: True, cache_dir=lambda: "/tmp",
                            hovered_show_id=lambda: None, request_redraw=lambda: None, loading_frame=lambda: "*")
    manager.get = lambda show: manager.set_status(show, "loading") or ""
    details = strip_ansi(ViewPresentation(session, manager).details(width=160)).splitlines()
    assert details[:3] == ["Artist: Miku Itou", "Member of Group", "ARTIST"]
    assert "Fetching performances" in details[-1]
    assert "Loading cover" in details[-1]
