"""Structured sessions retain identity across the legacy renderer boundary."""

from dataclasses import replace
import sys
from types import SimpleNamespace

import pytest

from media_cli.ui import picker as picker_module
from media_cli.ui import view_picker
from media_cli.ui.poster import PosterManager
from media_cli.ui.view_model import (
    Blank,
    Content,
    Inherit,
    DetailLines,
    ImageSpec,
    SelectionPolicy,
    ViewAction,
    ViewItem,
    ViewSpec,
    ViewTab,
)
from media_cli.ui.view_session import (
    ExitSession,
    PushView,
    ReplaceView,
    UpdateItems,
    UpdateStatus,
    ViewSession,
)


class ScriptedPicker:
    def __init__(self, *steps):
        self.steps = iter(steps)
        self.calls = 0
        self.active = False

    def __call__(self, flags, ui, prompt, options, **hooks):
        self.calls += 1
        self.active = True
        try:
            return next(self.steps)(options, hooks)
        finally:
            self.active = False


@pytest.fixture
def environment(monkeypatch, tmp_path):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    flags = SimpleNamespace(show_image=False, plain_mode=False)
    ui = SimpleNamespace(active_picker_query="", hovered_show_id=None, hovered_show_obj=None)
    manager = PosterManager(
        enabled=lambda: flags.show_image and not flags.plain_mode,
        cache_dir=lambda: str(tmp_path),
        hovered_show_id=lambda: ui.hovered_show_id,
        request_redraw=lambda: None,
        loading_frame=lambda: "*",
    )
    # Rendering/downloading is outside this adapter's ownership.
    monkeypatch.setattr(manager, "get", lambda show: show.get("_poster_raw", ""))
    return flags, ui, manager


def run(environment, session, picker):
    flags, ui, manager = environment
    return view_picker.run_view_session(flags, ui, session, picker=picker, poster_manager=manager)


def test_interrupt_wins_over_a_queued_back_action_during_loading(environment):
    from media_cli.ui.view_session import BeginNavigation

    initial = ViewSpec("source", "Source", (ViewItem("one", "One", details=Inherit),),
                       details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(initial)
    session.apply(BeginNavigation("source", session.generation, "request", "Fetching groups"))

    def interrupt(options, hooks):
        assert hooks["key_fn"]("ESC", 0)
        return -5

    run(environment, session, ScriptedPicker(interrupt))
    assert session.exited


def test_duplicate_labels_select_the_original_item_after_picker_returns(environment):
    events = []
    picker = ScriptedPicker(lambda options, hooks: 1)

    def handle(event):
        assert not picker.active
        events.append(event)
        return ExitSession(event.item_id)

    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("a", "Same", details=Inherit), ViewItem("b", "Same", details=Inherit)), details=DetailLines(Blank, Blank, Blank)), handle)
    assert run(environment, session, picker) == "b"
    assert events[0].kind == "select"
    assert events[0].view_id == "root"
    assert events[0].generation == session.generation


def test_disabled_and_heading_rows_cannot_execute_and_sections_filter_locally(environment):
    items = (
        ViewItem("h", "Credits", kind="heading", details=Inherit),
        ViewItem("off", "Song disabled", enabled=False, section_id="h", details=Inherit),
        ViewItem("on", "Song", section_id="h", hint="Live", prefix="OP ", details=Inherit),
        ViewItem("other", "Different", details=Inherit),
    )

    def inspect(options, hooks):
        assert hooks["heading_indices"] == {0}
        assert hooks["disabled_indices"] == {0, 1}
        assert hooks["filter_fn"]("song", options) == [0, 1, 2]
        assert hooks["filter_fn"]("credits", options) == []
        assert hooks["hints"](2) == "Live"
        assert hooks["item_prefix_fn"](2) == "OP "
        assert not hooks["select_fn"](0)
        assert not hooks["select_fn"](1)
        assert hooks["select_fn"](2)
        return 0  # Even a renderer returning an invalid selection cannot execute it.

    events = []
    session = ViewSession(ViewSpec("root", "Pick", items, details=DetailLines(Blank, Blank, Blank)), lambda event: events.append(event) or ExitSession(event.item_id))
    assert run(environment, session, ScriptedPicker(inspect, lambda options, hooks: 2)) == "on"
    assert [event.item_id for event in events] == ["on"]


@pytest.mark.parametrize("state", ["loading", "empty", "failed", "ready"])
def test_empty_states_do_not_invent_results_and_root_back_exits(environment, state):
    events = []
    session = ViewSession(
        ViewSpec("root", "Pick", state=state, message="Waiting", selection_policy=SelectionPolicy(auto_single=True), details=DetailLines(Blank, Blank, Blank)),
        lambda event: events.append(event),
    )

    def inspect(options, hooks):
        assert options == []
        assert hooks["filter_fn"]("", options) == []
        assert hooks["heading_indices"] == set()
        assert hooks["disabled_indices"] == set()
        assert not hooks["auto_select_single_when_done"]
        assert not hooks["select_fn"](0)
        assert hooks["live_fn"]("")[2] is (state != "loading")
        return -3

    assert run(environment, session, ScriptedPicker(inspect)) is None
    assert session.exited
    assert [event.kind for event in events] == ["back"]


@pytest.mark.parametrize("key, target", [("TAB", "dub"), ("BTAB", "third"), ("SHIFT_TAB", "third")])
def test_tab_events_use_tab_ids_and_run_after_picker_returns(environment, key, target):
    def choose(options, hooks):
        assert hooks["key_fn"](key, 0)
        return -6

    picker = ScriptedPicker(choose)

    def handle(event):
        assert not picker.active
        assert event.kind == "tab"
        assert session.position.active_tab_id == target
        return ExitSession(event.value)

    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),),
                    tabs=(ViewTab("sub", "Sub"), ViewTab("dub", "Dub"), ViewTab("third", "Third")),
                    active_tab_id="sub", details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(view, handle)
    assert run(environment, session, picker) == target


@pytest.mark.parametrize("plain", [False, True])
def test_tab_content_replacement_updates_displayed_prompt_label(environment, plain):
    environment[0].plain_mode = plain
    view = ViewSpec("root", "History", (ViewItem("sub-item", "Sub result", details=Inherit),),
                    tabs=(ViewTab("sub", "Sub"), ViewTab("dub", "Dub")), active_tab_id="sub", details=DetailLines(Blank, Blank, Blank))
    prompts = []

    def handle(event):
        if event.kind == "tab":
            # Keep the spec's initial tab: the live position takes precedence.
            return ReplaceView("root", event.generation, replace(view, items=(ViewItem("dub-item", "Dub result", details=Inherit),)))

    session = ViewSession(view, handle)
    session.position.active_tab_id = None

    def picker(flags, ui, prompt, options, **hooks):
        prompts.append(prompt() if callable(prompt) else prompt)
        if len(prompts) == 1:
            assert options == ["Sub result"]
            assert hooks["key_fn"]("t" if plain else "TAB", 0)
            return -6
        assert options == ["Dub result"]
        return -5

    run(environment, session, picker)
    assert prompts == ["History \u00b7 Sub", "History \u00b7 Dub"]


def test_back_restores_filter_focus_scroll_and_marks(environment):
    root = ViewSpec("root", "Root", tuple(ViewItem(str(i), f"Song {i}", details=Inherit) for i in range(8)),
                    selection_policy=SelectionPolicy(multiple=True), details=DetailLines(Blank, Blank, Blank))
    child = ViewSpec("child", "Child", (ViewItem("x", "Child row", details=Inherit),), details=DetailLines(Blank, Blank, Blank))
    events = []

    def handle(event):
        events.append(event)
        if event.kind == "select":
            return PushView(child)
        return ExitSession("done")

    def enter(options, hooks):
        state = hooks["state"]
        state.query, state.selected_index, state.scroll = "song", 5, 3
        state.marked_indices = {2, 5}
        return 5

    def back(options, hooks):
        assert options == ["Child row"]
        return -2

    def restored(options, hooks):
        state = hooks["state"]
        assert (state.query, state.selected_index, state.scroll, state.marked_indices) == ("song", 5, 3, {2, 5})
        return -3

    session = ViewSession(root, handle)
    assert run(environment, session, ScriptedPicker(enter, back, restored)) == "done"
    assert [event.kind for event in events] == ["select", "back"]


def test_parent_updates_while_nested_restore_marks_against_new_order(environment):
    items = (ViewItem("a", "Song", details=Inherit), ViewItem("b", "Song", details=Inherit), ViewItem("c", "Song", details=Inherit))
    root = ViewSpec("root", "Root", items, details=DetailLines(Blank, Blank, Blank))
    child = ViewSpec("child", "Child", (ViewItem("x", "Child", details=Inherit),), details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(root, lambda event: PushView(child) if event.kind == "select" else None)
    root_generation = session.generation

    def enter(options, hooks):
        state = hooks["state"]
        state.query, state.scroll, state.marked_indices = "song", 1, {1, 2}
        return 1

    def update_parent(options, hooks):
        session.post(UpdateItems("root", root_generation, (items[2], items[0], items[1]), replace_all=True))
        hooks["live_fn"]("")
        assert hooks["exit_fn"]()
        return -6

    def restored(options, hooks):
        state = hooks["state"]
        assert (state.query, state.selected_index, state.scroll, state.marked_indices) == ("song", 2, 1, {0, 2})
        return -3

    run(environment, session, ScriptedPicker(enter, update_parent, lambda options, hooks: -2, restored))
    assert session.exited


@pytest.mark.parametrize("remove_selected", [False, True])
def test_live_reorder_translates_focus_and_marks_by_id(environment, remove_selected):
    a, b, c = ViewItem("a", "Same", details=Inherit), ViewItem("b", "Same", details=Inherit), ViewItem("c", "Same", details=Inherit)
    session = ViewSession(ViewSpec("root", "Pick", (a, b, c), details=DetailLines(Blank, Blank, Blank)), lambda event: ExitSession(event.item_id))

    def update(options, hooks):
        state = hooks["state"]
        hooks["live_fn"]("")
        hooks["header_fn"](1)
        session.post(UpdateItems("root", session.generation, (a, c) if remove_selected else (b, a, c), replace_all=True))
        picker_module._needs_redraw = False
        live_options, _, _ = hooks["live_fn"]("")
        assert live_options == options
        assert hooks["exit_fn"]()
        assert picker_module._needs_redraw
        # The real renderer writes its position only in its finally block,
        # after live_fn has already accepted the reordered session data.
        state.selected_index, state.marked_indices = 1, {1}
        return -6

    def select(options, hooks):
        state = hooks["state"]
        assert state.selected_index == (1 if remove_selected else 0)
        assert state.marked_indices == (set() if remove_selected else {0})
        return state.selected_index

    assert run(environment, session, ScriptedPicker(update, select)) == ("c" if remove_selected else "b")


def test_focus_is_deduplicated_and_debounced_without_blocking_input(environment, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(view_picker.time, "monotonic", lambda: now[0])
    events = []
    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit), ViewItem("b", "B", details=Inherit)), details=DetailLines(Blank, Blank, Blank)),
                          lambda event: events.append(event))

    def hover(options, hooks):
        live, header = hooks["live_fn"], hooks["header_fn"]
        live("")
        header(0)
        now[0] = 10.49
        live("")
        assert events == []
        header(1)
        now[0] = 10.98
        live("")
        assert events == []
        now[0] = 11.0
        live("")
        live("")
        header(1)
        now[0] = 12.0
        live("")
        assert [(event.kind, event.item_id) for event in events] == [("focus", "b")]
        return -5

    run(environment, session, ScriptedPicker(hover))
    assert [event.kind for event in events] == ["focus", "quit"]


def test_plain_supplies_structured_header_and_actions_without_images(environment):
    flags, _, _ = environment
    flags.plain_mode = True
    flags.show_image = True
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),), details=DetailLines(Content("Title"), Blank, Content("Loading...")),
                    context=("Left/Esc=back",), actions=(ViewAction("back", "Return", ("ESC",)),))
    session = ViewSession(view)

    def inspect(options, hooks):
        assert hooks["structured"] is True
        assert hooks["return_on_quit"] is True
        assert hooks["header"] == "Title\n\nLoading...\nLeft/Esc=back"
        assert "[b] Return" in hooks["action_labels"]
        assert hooks["top_header_fn"] is None
        return -5

    run(environment, session, ScriptedPicker(inspect))


@pytest.mark.parametrize("multiple, select_command", [(False, "1-N"), (True, "1,2")])
def test_plain_action_labels_describe_typed_commands(environment, multiple, select_command):
    environment[0].plain_mode = True
    view = ViewSpec(
        "root", "Pick", (ViewItem("a", "A", details=Inherit),),
        actions=(ViewAction("select", "Open", ("ENTER", "RIGHT")),
                 ViewAction("back", "Return", ("ESC",)),
                 ViewAction("quit", "Leave", ("CTRL_C",)),
                 ViewAction("tab", "Switch", ("TAB", "BTAB")),
                 ViewAction("refresh", "Refresh", ("CTRL_R",))),
        tabs=(ViewTab("one", "One"), ViewTab("two", "Two")),
        selection_policy=SelectionPolicy(multiple=multiple),
        details=DetailLines(Blank, Blank, Blank),
    )

    def inspect(options, hooks):
        assert hooks["action_labels"] == [f"[{select_command}] Open", "[b] Return", "[q] Leave", "[t] Switch", "[refresh] Refresh"]
        return -5

    run(environment, ViewSession(view), ScriptedPicker(inspect))


@pytest.mark.parametrize("action_id", ["refresh", "x"])
def test_plain_typed_action_id_executes_after_picker_returns(environment, action_id):
    from media_cli.ui.plain_picker import plain_pick

    flags, ui, _ = environment
    flags.plain_mode = True
    output = []
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),),
                    actions=(ViewAction(action_id, "Refresh", ("CTRL_R",)),), details=DetailLines(Blank, Blank, Blank))

    def choose(options, hooks):
        assert not hooks["key_fn"]("CTRL_R", 0)
        answers = iter([action_id])
        return plain_pick(flags, ui, "Pick", options, input_fn=lambda prompt: next(answers),
                          output_fn=output.append, **hooks)

    picker = ScriptedPicker(choose)

    def handle(event):
        assert not picker.active
        assert (event.kind, event.item_id) == ("action", "a")
        return ExitSession(event.value)

    assert run(environment, ViewSession(view, handle), picker) == action_id
    assert f"[{action_id}] Refresh" in "\n".join(output)
    assert "[1-N] Select" in "\n".join(output)
    assert "[b] Back" in "\n".join(output)
    assert "[q] Quit" in "\n".join(output)


def test_plain_tab_hint_and_command_use_t(environment):
    environment[0].plain_mode = True
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),),
                    tabs=(ViewTab("sub", "Sub"), ViewTab("dub", "Dub")), active_tab_id="sub", details=DetailLines(Blank, Blank, Blank))

    def choose(options, hooks):
        assert "[t] Next tab" in hooks["action_labels"]
        assert hooks["key_fn"]("t", 0)
        return -6

    def handle(event):
        assert event.kind == "tab"
        return ExitSession(event.value)

    assert run(environment, ViewSession(view, handle), ScriptedPicker(choose)) == "dub"


def test_info_callback_opens_existing_modal_with_supplied_text_only(environment, monkeypatch):
    from media_cli.ui import info_screen

    shown = []
    monkeypatch.setattr(info_screen, "show_info_screen", lambda show, **kwargs: shown.append(show))
    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("a", "A", info="Supplied information", details=Inherit),), details=DetailLines(Blank, Blank, Blank)))

    def info(options, hooks):
        hooks["info_fn"](0)
        return -5

    run(environment, session, ScriptedPicker(info))
    assert len(shown) == 1
    assert shown[0]["description"] == "Supplied information"
    assert shown[0]["_enrichment_attempted"] is True
    assert "Supplied information" in "\n".join(info_screen._build_content(shown[0], 80))


def test_info_modal_receives_sanitized_name_and_multiline_description(environment, monkeypatch):
    from media_cli.core.terminal import sanitize_terminal_text
    from media_cli.ui import info_screen

    shown = []
    monkeypatch.setattr(info_screen, "show_info_screen", lambda show, **kwargs: shown.append(show))
    raw_name = "\x1b[2JTitle\x00 name"
    with pytest.raises(ValueError):
        ViewItem("a", raw_name, details=Inherit)
    item = ViewItem("a", sanitize_terminal_text(raw_name),
                    info="\x1b]0;Injected\x07First\n\nSecond\tline\x1b[31m", details=Inherit)

    def inspect(options, hooks):
        hooks["info_fn"](0)
        return -5

    run(environment, ViewSession(ViewSpec("root", "Pick", (item,), details=DetailLines(Blank, Blank, Blank))), ScriptedPicker(inspect))
    assert shown[0]["name"] == "Title name"
    assert shown[0]["description"] == "First\n\nSecond line"
    assert shown[0]["_enrichment_attempted"] is True


def test_reactivation_does_not_restore_stale_picker_position(environment):
    session = ViewSession(ViewSpec("root", "Before", (ViewItem("old", "zzzz", details=Inherit),), details=DetailLines(Blank, Blank, Blank)),
                          lambda event: ExitSession(event.item_id))
    session.position.query, session.position.scroll = "zzzz", 7
    session.position.marked_ids = {"old"}
    generation = session.generation

    def replace_activation(options, hooks):
        assert (hooks["state"].query, hooks["state"].scroll) == ("zzzz", 7)
        session.post(ReplaceView("root", generation, ViewSpec("root", "After", (ViewItem("fresh", "New result", details=Inherit),), details=DetailLines(Blank, Blank, Blank)),
                                 new_activation=True))
        hooks["live_fn"]("zzzz")
        assert session.generation != generation
        assert session.position.query == ""
        assert hooks["exit_fn"]()
        return -6

    def fresh(options, hooks):
        state = hooks["state"]
        assert (state.query, state.scroll, state.marked_indices) == ("", 0, set())
        assert session.position.selected_id == "fresh"
        assert hooks["filter_fn"](state.query, options) == [0]
        return 0

    assert run(environment, session, ScriptedPicker(replace_activation, fresh)) == "fresh"


@pytest.mark.parametrize("action_id, key, kind, value", [
    ("refresh", "TAB", "action", "refresh"),
    ("refresh", "ENTER", "action", "refresh"),
    ("quit", "ESC", "quit", None),
    ("select", "ESC", "select", None),
    ("back", "RIGHT", "back", None),
    ("tab", "ESC", "tab", "dub"),
    ("refresh", "CTRL_C", "quit", None),
])
def test_declared_reserved_key_semantics_precede_defaults(environment, action_id, key, kind, value):
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),),
                    actions=(ViewAction(action_id, action_id.title(), (key,)),),
                    tabs=(ViewTab("sub", "Sub"), ViewTab("dub", "Dub")) if action_id == "tab" else (),
                    active_tab_id="sub" if action_id == "tab" else None, details=DetailLines(Blank, Blank, Blank))

    def choose(options, hooks):
        assert hooks["key_fn"](key, 0)
        return -6

    picker = ScriptedPicker(choose)

    def handle(event):
        assert not picker.active
        assert (event.kind, event.value) == (kind, value)
        if kind in {"action", "select"}:
            assert event.item_id == "a"
        return ExitSession("handled")

    assert run(environment, ViewSession(view, handle), picker) == "handled"


def test_plain_reserved_key_custom_action_keeps_its_typed_id(environment):
    environment[0].plain_mode = True
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),),
                    actions=(ViewAction("refresh", "Refresh", ("TAB",)),), details=DetailLines(Blank, Blank, Blank))

    def choose(options, hooks):
        assert "[refresh] Refresh" in hooks["action_labels"]
        assert hooks["key_fn"]("refresh", 0)
        return -6

    def handle(event):
        assert (event.kind, event.value) == ("action", "refresh")
        return ExitSession("handled")

    assert run(environment, ViewSession(view, handle), ScriptedPicker(choose)) == "handled"


def test_declared_select_key_uses_marks_captured_after_picker_returns(environment):
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit), ViewItem("b", "B", details=Inherit)),
                    actions=(ViewAction("select", "Choose", ("ESC",)),), selection_policy=SelectionPolicy(multiple=True), details=DetailLines(Blank, Blank, Blank))

    def choose(options, hooks):
        assert hooks["key_fn"]("ESC", 0)
        hooks["state"].marked_indices = {1}
        return -6

    def handle(event):
        assert (event.kind, event.item_id, event.value) == ("select", "b", ("b",))
        return ExitSession("handled")

    assert run(environment, ViewSession(view, handle), ScriptedPicker(choose)) == "handled"


def test_footer_updates_while_idle_and_shares_hovered_poster_state(environment):
    flags, ui, manager = environment
    flags.show_image = True
    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("a", "A", image=ImageSpec("cover", "available", "https://example.test/cover"), details=Inherit),), details=DetailLines(Blank, Blank, Blank)))

    def idle(options, hooks):
        hooks["live_fn"]("")
        hooks["top_header_fn"](0)
        show = ui.hovered_show_obj
        assert ui.hovered_show_id == show["_id"]
        manager.set_status(show, "loading")
        assert hooks["tick_fn"]()
        assert "Loading cover" in hooks["live_fn"]("")[1]
        manager.set_status(show, "failed")
        assert "Cover failed" in hooks["live_fn"]("")[1]
        assert ui.hovered_show_obj is show
        return -5

    run(environment, session, ScriptedPicker(idle))


def test_custom_nonprintable_actions_leave_printable_keys_for_filtering(environment):
    view = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),),
                    actions=(ViewAction("refresh", "Refresh", ("CTRL_R", "x")),), details=DetailLines(Blank, Blank, Blank))
    picker = ScriptedPicker()

    def choose(options, hooks):
        assert not hooks["key_fn"]("x", 0)
        assert not hooks["key_fn"]("refresh", 0)
        assert "[Ctrl+R/x] Refresh" in hooks["action_labels"]
        assert "[Enter/Right] Select" in hooks["action_labels"]
        assert "[Esc/Left] Back" in hooks["action_labels"]
        assert "[Ctrl+C] Quit" in hooks["action_labels"]
        assert hooks["key_fn"]("CTRL_R", 0)
        return -6

    picker.steps = iter([choose])

    def handle(event):
        assert not picker.active
        assert (event.kind, event.item_id) == ("action", "a")
        return ExitSession(event.value)

    assert run(environment, ViewSession(view, handle), picker) == "refresh"


def test_playback_handler_returning_none_reopens_same_view(environment):
    events = []
    picker = ScriptedPicker(lambda options, hooks: 0, lambda options, hooks: -5)

    def handle(event):
        assert not picker.active
        events.append(event.kind)

    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("video", "Play", details=Inherit),), details=DetailLines(Blank, Blank, Blank)), handle)
    run(environment, session, picker)
    assert events == ["select", "quit"]
    assert session.position.selected_id == "video"
    assert picker.calls == 2


def test_auto_single_waits_for_final_results_and_does_not_reenter_after_back(environment):
    root = ViewSpec("root", "Pick", (ViewItem("h", "Heading", kind="heading", details=Inherit), ViewItem("x", "Unavailable", enabled=False, details=Inherit), ViewItem("a", "A", details=Inherit)),
                    state="loading", selection_policy=SelectionPolicy(auto_single=True), details=DetailLines(Blank, Blank, Blank))
    child = ViewSpec("child", "Child", (ViewItem("b", "B", details=Inherit),), details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(root, lambda event: PushView(child) if event.kind == "select" else None)

    def loading(options, hooks):
        assert not hooks["auto_select_single_when_done"]
        session.post(UpdateStatus("root", session.generation, "ready"))
        hooks["live_fn"]("")
        assert hooks["exit_fn"]()
        return -6

    def ready(options, hooks):
        assert hooks["auto_select_single_when_done"]
        return 2

    def restored(options, hooks):
        assert not hooks["auto_select_single_when_done"]
        return -3

    run(environment, session, ScriptedPicker(loading, ready, lambda options, hooks: -2, restored))
    assert session.exited


def test_metadata_restart_does_not_consume_auto_entry(environment):
    root = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),), selection_policy=SelectionPolicy(auto_single=True), details=DetailLines(Blank, Blank, Blank))
    child = ViewSpec("child", "Child", (ViewItem("b", "B", details=Inherit),), details=DetailLines(Blank, Blank, Blank))
    session = ViewSession(root, lambda event: PushView(child) if event.kind == "select" else None)

    def interrupted(options, hooks):
        assert hooks["auto_select_single_when_done"]
        session.post(UpdateItems("root", session.generation, (replace(root.items[0], hint="Updated"),)))
        hooks["live_fn"]("")
        assert hooks["exit_fn"]()
        return -6

    def select(options, hooks):
        assert hooks["auto_select_single_when_done"]
        return 0

    def restored(options, hooks):
        assert not hooks["auto_select_single_when_done"]
        return -3

    run(environment, session, ScriptedPicker(interrupted, select, lambda options, hooks: -2, restored))
    assert session.exited


def test_header_survives_terminal_size_lookup_failure(environment, monkeypatch):
    import os

    def detached(*args):
        raise OSError("No terminal")

    monkeypatch.setattr(os, "get_terminal_size", detached)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),), details=DetailLines(Content("Title"), Blank, Blank)))

    def inspect(options, hooks):
        assert len(hooks["header_fn"](0).split("\n")) == 4
        return -5

    run(environment, session, ScriptedPicker(inspect))


def test_root_back_handler_gets_first_chance_to_replace_the_view(environment):
    events = []
    root = ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit),), details=DetailLines(Blank, Blank, Blank))

    def handle(event):
        events.append(event.kind)
        if len(events) == 1:
            return ReplaceView("root", event.generation, replace(root, prompt="Updated"))

    session = ViewSession(root, handle)
    run(environment, session, ScriptedPicker(lambda options, hooks: -2, lambda options, hooks: -3))
    assert events == ["back", "back"]
    assert session.exited


def test_query_and_multi_selection_events_carry_stable_values(environment):
    events = []

    def handle(event):
        events.append(event)
        if event.kind == "query":
            # Submitted search text is not a filter on the new result list.
            return ReplaceView("root", event.generation,
                               replace(session.current, selection_policy=SelectionPolicy(multiple=True)),
                               new_activation=True)
        return ExitSession(event.value)

    session = ViewSession(ViewSpec("root", "Pick", (ViewItem("a", "A", details=Inherit), ViewItem("b", "B", details=Inherit)),
                                   selection_policy=SelectionPolicy(query_input=True), details=DetailLines(Blank, Blank, Blank)), handle)

    def query(options, hooks):
        assert hooks["return_query_on_enter"]
        hooks["state"].query = "new query"
        return "new query"

    def multi(options, hooks):
        assert hooks["multi_select"]
        return [1, 0]

    assert run(environment, session, ScriptedPicker(query, multi)) == ("b", "a")
    assert [(event.kind, event.value) for event in events] == [("query", "new query"), ("select", ("b", "a"))]
