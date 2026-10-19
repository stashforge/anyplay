"""Malformed internal input must fail before corrupting navigation state."""

from dataclasses import replace

import pytest

from media_cli.ui.footer import StatusKind
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_session import (
    BeginNavigation,
    CompleteNavigation,
    FailNavigation,
    PopView,
    PushView,
    UiEvent,
    UpdateItems,
    UpdateStatus,
    ViewPosition,
    ViewSession,
)


def root():
    return ViewSpec(
        "root",
        "Pick",
        (ViewItem("a", "A", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        actions=(ViewAction("retry", "Retry"),),
    )


@pytest.mark.parametrize("capacity", (0, -1, True, "2"))
def test_invalid_queue_capacity_fails_at_session_creation(capacity):
    with pytest.raises((TypeError, ValueError), match="max_pending"):
        ViewSession(root(), max_pending=capacity)


@pytest.mark.parametrize(
    "changes",
    ({"kind": "selct"}, {"view_id": ""}, {"generation": True}, {"generation": 0}, {"item_id": 7}),
)
def test_invalid_event_address_or_kind_is_rejected(changes):
    fields = dict(kind="select", view_id="root", generation=1, item_id="a")
    fields.update(changes)
    with pytest.raises((TypeError, ValueError)):
        UiEvent(**fields)


@pytest.mark.parametrize(
    ("kind", "value"),
    (("query", None), ("query", 2), ("tab", 1), ("action", ""), ("select", "ab"), ("select", ("a", 3))),
)
def test_event_payload_matches_event_kind(kind, value):
    with pytest.raises((TypeError, ValueError)):
        UiEvent(kind, "root", 1, value=value)


def test_multiple_selection_payload_is_snapshotted():
    ids = ["a"]
    event = UiEvent("select", "root", 1, value=ids)
    ids.append("b")
    assert event.value == ("a",)


@pytest.mark.parametrize(
    "command",
    (
        lambda: UpdateStatus("root", 1, "ready", kind="error"),
        lambda: UpdateStatus("root", 1, "unknown"),
        lambda: UpdateItems("root", 1, ("bad item",)),
        lambda: UpdateItems("root", 1, root().items * 2),
        lambda: UpdateItems("root", 1, (), revision=True),
        lambda: UpdateItems("root", 1, (), expected_images=(("a",),)),
        lambda: UpdateItems("root", 1, (), expected_images=(("a", object()),)),
        lambda: UpdateItems("root", 1, (), expected_images=(("a", "cover"), ("a", "cover"))),
        lambda: BeginNavigation("root", 1, "", "Loading"),
        lambda: FailNavigation("root", 1, "request", ""),
        lambda: CompleteNavigation("root", 1, "request", "not a view"),
    ),
)
def test_malformed_commands_are_rejected_when_constructed(command):
    with pytest.raises((TypeError, ValueError)):
        command()


@pytest.mark.parametrize(
    "kind,value,item",
    (("select", None, "missing"), ("focus", None, "missing"), ("tab", "missing", None), ("action", "missing", None)),
)
def test_invalid_interaction_does_not_cancel_pending_navigation(kind, value, item):
    session = ViewSession(root())
    session.apply(BeginNavigation("root", 1, "request", "Loading"))
    pending = session.navigation
    assert not session.dispatch(UiEvent(kind, "root", 1, item, value))
    assert session.navigation is pending


def test_invalid_controller_batch_cannot_partially_push_views():
    child = replace(root(), id="child")
    session = ViewSession(root(), lambda event: (PushView(child), object()))
    with pytest.raises(TypeError, match="command"):
        session.dispatch(UiEvent("select", "root", 1, "a"))
    assert session.depth == 1


@pytest.mark.parametrize("changes", ({"query": None}, {"scroll": -1}, {"scroll": True}, {"marked_ids": {2}}))
def test_position_rejects_invalid_fields(changes):
    with pytest.raises((TypeError, ValueError)):
        ViewPosition(**changes)


def test_position_marks_are_owned_and_bad_mutation_is_caught_at_boundary():
    marks = {"a"}
    position = ViewPosition(marked_ids=marks)
    marks.clear()
    assert position.marked_ids == {"a"}
    session = ViewSession(root())
    session.position.scroll = -1
    with pytest.raises(ValueError, match="scroll"):
        session.dispatch(UiEvent("select", "root", 1, "a"))


def test_stale_well_formed_completion_remains_an_ignored_outcome():
    session = ViewSession(root())
    before = session.current
    assert not session.apply(UpdateStatus("root", 999, "failed", "Old request", StatusKind.ERROR))
    assert session.current is before


@pytest.mark.parametrize("target", ("missing", "disabled"))
def test_multiple_marks_do_not_bypass_explicit_target_validation(target):
    from media_cli.ui.view_model import SelectionPolicy

    view = replace(
        root(),
        items=(*root().items, ViewItem("disabled", "Disabled", enabled=False, details=Inherit)),
        selection_policy=SelectionPolicy(multiple=True),
    )
    received = []
    session = ViewSession(view, received.append)
    session.apply(BeginNavigation("root", 1, "request", "Loading"))
    pending = session.navigation
    assert not session.dispatch(UiEvent("select", "root", 1, target, ("a",)))
    assert session.navigation is pending
    assert not received


@pytest.mark.parametrize("transition", ("pop", "update"))
def test_bad_hidden_position_is_rejected_before_mutating_any_frame(transition):
    session = ViewSession(root())
    parent = session.frame("root", 1)
    original = parent.view
    session.apply(PushView(replace(root(), id="child")))
    version = session.version
    parent.position.scroll = -1
    command = PopView() if transition == "pop" else UpdateStatus("root", 1, "failed", "Network error")
    with pytest.raises(ValueError, match="scroll"):
        session.apply(command)
    assert session.depth == 2
    assert session.current.id == "child"
    assert session.version == version
    assert parent.view is original


@pytest.mark.parametrize("field", ("items", "expected_images"))
@pytest.mark.parametrize("container", ("", b"", bytearray(), {}))
def test_bad_empty_container_is_not_interpreted_as_an_empty_update(field, container):
    values = dict(view_id="root", generation=1, items=(), replace_all=True)
    values[field] = container
    with pytest.raises(TypeError, match=field):
        UpdateItems(**values)
