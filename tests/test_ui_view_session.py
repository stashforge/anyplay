from dataclasses import replace

from media_cli.ui.view_model import Blank, DetailLines, Inherit, ImageSpec, SelectionPolicy, ViewItem, ViewSpec
from media_cli.ui.view_session import PopView, PushView, ReplaceView, UiEvent, UpdateItems, ViewSession


def view(name="root", items=None, **kwargs):
    kwargs.setdefault("details", DetailLines(Blank, Blank, Blank))
    return ViewSpec(name, name, tuple(items or (ViewItem("a", "Same", details=Inherit), ViewItem("b", "Same", details=Inherit))), **kwargs)


def test_back_restores_full_parent_position():
    session = ViewSession(view())
    session.position.selected_id = "b"
    session.position.query = "Same"
    session.position.scroll = 3
    session.position.marked_ids = {"b"}
    session.apply(PushView(view("child")))
    session.apply(PushView(view("grandchild")))
    session.apply(PopView())
    session.apply(PopView())
    assert session.current.id == "root"
    assert (session.position.selected_id, session.position.query, session.position.scroll) == ("b", "Same", 3)
    assert session.position.marked_ids == {"b"}


def test_reorder_preserves_id_and_removed_selection_has_safe_fallback():
    session = ViewSession(view())
    session.position.selected_id = "b"
    generation = session.generation
    session.apply(UpdateItems("root", generation, (ViewItem("b", "B", details=Inherit), ViewItem("a", "A", details=Inherit)), replace_all=True))
    assert session.position.selected_id == "b"
    session.apply(UpdateItems("root", generation, (ViewItem("a", "A", details=Inherit),), replace_all=True))
    assert session.position.selected_id == "a"


def test_closed_generation_is_not_reused():
    session = ViewSession(view())
    session.apply(PushView(view("child")))
    old = session.generation
    session.apply(PopView())
    session.apply(PushView(view("child")))
    assert session.generation != old
    assert not session.apply(UpdateItems("child", old, (ViewItem("a", "Stale", details=Inherit),)))
    assert session.current.items[0].label == "Same"


def test_hidden_parent_update_does_not_switch_view():
    session = ViewSession(view())
    parent_generation = session.generation
    session.apply(PushView(view("child")))
    assert session.apply(UpdateItems("root", parent_generation, (ViewItem("a", "Updated", details=Inherit),)))
    assert session.current.id == "child"
    session.apply(PopView())
    assert session.current.items[0].label == "Updated"


def test_auto_entry_waits_for_final_result_and_is_suppressed_after_back():
    only = ViewItem("a", "Only", details=Inherit)
    root = view(items=(only,), state="loading", selection_policy=SelectionPolicy(auto_single=True))
    session = ViewSession(root)
    assert session.auto_event() is None
    session.apply(ReplaceView("root", session.generation, replace(root, state="ready")))
    event = session.auto_event()
    assert event.item_id == "a"
    session.apply(PushView(view("child")))
    session.apply(PopView())
    assert session.auto_event() is None


def test_stale_image_completion_and_revision_are_rejected():
    item = ViewItem("a", "A", image=ImageSpec("new", state="resolving"), details=Inherit)
    session = ViewSession(view(items=(item,), revision=5))
    update = UpdateItems("root", session.generation, (replace(item, image=ImageSpec("old", "available", "https://x.test/old")),), expected_images=(("a", "old"),))
    assert not session.apply(update)
    assert not session.apply(UpdateItems("root", session.generation, (replace(item, label="old"),), revision=4))


def test_update_queue_coalesces_repeated_completion_and_invalidates():
    session = ViewSession(view())
    for number in range(1000):
        session.post(UpdateItems("root", session.generation, (ViewItem("a", str(number), details=Inherit),)))
    assert session.pending_count == 1
    assert session.drain_updates()
    assert session.current.items[0].label == "999"


def test_invalid_selection_does_not_reach_controller():
    received = []
    session = ViewSession(view(items=(ViewItem("h", "Heading", kind="heading", details=Inherit), ViewItem("x", "Disabled", enabled=False, details=Inherit))), received.append)
    session.dispatch(UiEvent("select", "root", session.generation, "h"))
    session.dispatch(UiEvent("select", "root", session.generation, "x"))
    assert received == []


def test_late_item_completion_cannot_reinsert_removed_item():
    session = ViewSession(view())
    generation = session.generation
    session.apply(UpdateItems("root", generation, (ViewItem("b", "B", details=Inherit),), replace_all=True))
    assert not session.apply(UpdateItems("root", generation, (ViewItem("a", "Late", details=Inherit),)))
    assert [item.id for item in session.current.items] == ["b"]


def test_queue_coalescing_does_not_replace_newer_revision_with_older():
    session = ViewSession(view())
    session.post(UpdateItems("root", session.generation, (ViewItem("a", "New", details=Inherit),), revision=6))
    session.post(UpdateItems("root", session.generation, (ViewItem("a", "Old", details=Inherit),), revision=5))
    session.drain_updates()
    assert session.item("a").label == "New"


def test_multiple_selection_validates_each_item():
    received = []
    session = ViewSession(view(items=(ViewItem("a", "A", details=Inherit), ViewItem("x", "Disabled", enabled=False, details=Inherit)),
                               selection_policy=SelectionPolicy(multiple=True)), received.append)
    session.dispatch(UiEvent("select", "root", session.generation, value=("a", "x")))
    assert not received
    session.dispatch(UiEvent("select", "root", session.generation, value=("a",)))
    assert received[0].value == ("a",)


def test_resolver_completion_cannot_overwrite_a_new_url_with_the_same_image_key():
    old = ImageSpec("cover", "resolving")
    item = ViewItem("a", "A", image=ImageSpec("cover", "available", "https://example.test/new"), details=Inherit)
    session = ViewSession(view(items=(item,)))
    completed = replace(item, image=ImageSpec("cover", "available", "https://example.test/old"))
    assert not session.apply(UpdateItems("root", session.generation, (completed,), expected_images=(("a", old),)))
    assert session.item().image.url.endswith("/new")


def test_replacing_a_search_activation_discards_previous_requests():
    session = ViewSession(view())
    old_generation = session.generation
    session.apply(ReplaceView("root", old_generation, view(), new_activation=True))
    assert session.generation != old_generation
    assert not session.apply(UpdateItems("root", old_generation, (ViewItem("a", "Old query", details=Inherit),)))
    assert session.current.state == "ready"


def test_new_query_allows_auto_entry_again_after_back():
    session = ViewSession(view(items=(ViewItem("a", "A", details=Inherit),), selection_policy=SelectionPolicy(auto_single=True)))
    assert session.auto_event() is not None
    session.apply(PushView(view("child")))
    session.apply(PopView())
    assert session.auto_event() is None
    session.dispatch(UiEvent("query", "root", session.generation, value="new search"))
    assert session.auto_event() is not None
