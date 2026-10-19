from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from media_cli.ui.loading_model import LoadingPhase
from media_cli.ui.message_choices import MessagePicker
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_session import (
    BeginNavigation,
    CompleteNavigation,
    ExitSession,
    FailNavigation,
    UiEvent,
    ViewSession,
)


def source():
    return ViewSpec("source", "Pick artist", (ViewItem("one", "Artist", details=Inherit),),
                    details=DetailLines(Blank, Blank, Blank))


def destination():
    return replace(source(), id="destination", prompt="Pick group")


def pending(**kwargs):
    session = ViewSession(source(), **kwargs)
    lifecycle = session.loading
    session.apply(BeginNavigation("source", session.generation, "request", "Fetching groups"))
    return session, lifecycle, session.navigation_token


def test_navigation_uses_shared_lifecycle_and_delay_without_changing_source():
    clock = [0.0]
    session, lifecycle, token = pending(clock=lambda: clock[0])
    assert session.current.id == "source" and session.input_blocked
    snapshot = lifecycle.snapshots(token.owner)[0]
    assert snapshot.message == "Fetching groups" and not snapshot.visible
    clock[0] = 0.15
    assert session.loading_snapshot().visible
    session.cancel_navigation()
    assert lifecycle.snapshots(token.owner) == ()
    assert not session.input_blocked


@pytest.mark.parametrize("replace_current", (False, True))
def test_completion_stays_data_ready_until_matching_frame_is_painted(replace_current):
    session, lifecycle, token = pending()
    source_generation = session.generation
    session.apply(CompleteNavigation("source", source_generation, "request", destination(), replace_current))
    assert session.navigation is None and session.current.id == "destination"
    assert session.input_blocked
    assert lifecycle.snapshots(token.owner)[0].phase is LoadingPhase.DATA_READY
    assert not session.acknowledge_frame("source", source_generation)
    assert session.acknowledge_frame(session.current.id, session.generation)
    assert lifecycle.snapshots(token.owner) == ()
    assert not session.input_blocked
    assert not session.acknowledge_frame(session.current.id, session.generation)


@pytest.mark.parametrize("replace_current", (False, True))
def test_back_before_destination_paint_restores_source_including_replacement(replace_current):
    session, lifecycle, token = pending()
    original = session.current
    session.position.query = "Art"
    session.position.scroll = 2
    session.apply(CompleteNavigation("source", session.generation, "request", destination(), replace_current))
    assert session.dispatch(UiEvent("back", session.current.id, session.generation))
    assert session.current is original and session.depth == 1
    assert session.position.query == "Art" and session.position.scroll == 2
    assert lifecycle.snapshots(token.owner) == ()


def test_failure_retry_and_exit_retire_only_their_own_loading():
    session, lifecycle, token = pending()
    generation = session.generation
    session.apply(FailNavigation("source", generation, "request", "Could not load groups"))
    assert session.current.message == "Could not load groups"
    assert lifecycle.snapshots(token.owner) == ()
    session.apply(BeginNavigation("source", generation, "retry", "Trying again"))
    retry = session.navigation_token
    assert retry != token
    assert not session.apply(CompleteNavigation("source", generation, "request", destination()))
    assert session.navigation_token == retry
    session.apply(ExitSession())
    assert lifecycle.snapshots(retry.owner) == ()


def test_pending_session_can_be_built_on_worker_then_bound_to_ui_owner():
    def construct():
        session = ViewSession(source())
        session.apply(BeginNavigation("source", session.generation, "request", "Fetching groups"))
        return session

    with ThreadPoolExecutor(max_workers=1) as worker:
        session = worker.submit(construct).result()
    assert session.loading_snapshot().message == "Fetching groups"
    session.cancel_navigation()
    assert not session.input_blocked


@pytest.mark.parametrize("replace_current", (False, True))
def test_completion_before_binding_retains_data_ready_handoff(replace_current):
    session = ViewSession(source())
    session.apply(BeginNavigation("source", session.generation, "request", "Fetching groups"))
    session.apply(CompleteNavigation("source", session.generation, "request", destination(), replace_current))
    assert session.input_blocked
    token = session.loading_snapshot().token
    assert session.loading_snapshot().phase is LoadingPhase.DATA_READY
    session.acknowledge_frame(session.current.id, session.generation)
    assert session.loading.snapshots(token.owner) == ()


def test_cold_replacement_can_be_cancelled_without_binding_lifecycle():
    session = ViewSession(source())
    original = session.current
    session.apply(BeginNavigation("source", session.generation, "request", "Fetching groups"))
    session.apply(CompleteNavigation("source", session.generation, "request", destination(), True))
    assert session.cancel_navigation()
    assert session.current is original and session.depth == 1


def test_invalid_failure_text_is_rejected_before_state_mutation():
    session, lifecycle, token = pending()
    with pytest.raises(ValueError, match="120"):
        command = FailNavigation("source", session.generation, "request", "x" * 121)
        session.apply(command)
    assert session.navigation is not None
    assert session.cancel_navigation()
    assert lifecycle.snapshots(token.owner) == ()


def test_navigation_message_choices_share_immutable_data_and_rotation():
    clock = [0.0]
    session = ViewSession(source(), clock=lambda: clock[0], loading_delay=0)
    session.loading._message_picker = MessagePicker(choose=lambda choices: choices[0])
    session.loading._rotation_interval = lambda: 3
    alternatives = ["Fetching groups", "Getting the groups ready"]
    session.apply(BeginNavigation("source", session.generation, "request", alternatives))
    alternatives.append("Unrelated mutation")
    assert session.navigation.message == ("Fetching groups", "Getting the groups ready")
    assert session.loading_snapshot().message == "Fetching groups"
    clock[0] = 3
    assert session.loading_snapshot().message == "Getting the groups ready"


@pytest.mark.parametrize("message", ([], ["Same", "Same"], "x" * 121))
def test_navigation_applies_shared_message_validation(message):
    with pytest.raises(ValueError, match="navigation.message"):
        BeginNavigation("source", 1, "request", message)


@pytest.mark.parametrize("kwargs", ({"clock": None}, {"loading_delay": -1}, {"loading_delay": True}))
def test_invalid_lifecycle_settings_fail_before_navigation_changes(kwargs):
    with pytest.raises((ValueError, TypeError)):
        ViewSession(source(), **kwargs)
