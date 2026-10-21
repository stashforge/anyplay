from test_ui_view_picker import ScriptedPicker
from test_ui_view_picker import environment as environment

from media_cli.ui import view_picker
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewItem, ViewSpec
from media_cli.ui.view_session import ExitSession, ViewSession


def view(items=("one", "two")):
    return ViewSpec(
        "menu",
        "Menu",
        tuple(ViewItem(key, key, details=Inherit) for key in items),
        details=DetailLines(Blank, Blank, Blank),
    )


def run(env, content, step, *, key=("menu",), restore=True):
    flags, ui, manager = env
    session = ViewSession(content, lambda event: ExitSession())
    view_picker.run_view_session(
        flags,
        ui,
        session,
        picker=ScriptedPicker(step),
        poster_manager=manager,
        position_key=key,
        restore_position=restore,
    )


def test_reopened_view_restores_stable_selection_filter_and_edit_cursor(environment):
    def select(options, hooks):
        hooks["state"].query = "o"
        hooks["state"].query_cursor = 0
        hooks["state"].selected_index = 1
        return 1

    run(environment, view(), select)

    def restored(options, hooks):
        assert options[hooks["state"].selected_index] == "two"
        assert hooks["state"].query == "o"
        assert hooks["state"].query_cursor == 0
        return -2

    run(environment, view(("two", "one")), restored)


def test_fresh_search_or_different_context_does_not_restore(environment):
    def select(options, hooks):
        hooks["state"].query = "two"
        return 1

    run(environment, view(), select)

    def reset(options, hooks):
        assert hooks["state"].query == ""
        assert hooks["state"].selected_index == 0
        return -2

    run(environment, view(), reset, key=("another title",))
    run(environment, view(), reset, restore=False)


def test_missing_saved_item_falls_back_to_current_selectable_row(environment):
    run(environment, view(), lambda options, hooks: 1)

    def fallback(options, hooks):
        assert hooks["state"].selected_index == 0
        return -2

    run(environment, view(("one",)), fallback)


def test_ui_memory_is_bounded_and_contains_no_result_records(environment):
    for index in range(35):
        run(environment, view(), lambda options, hooks: -2, key=(str(index),))
    memory = environment[1]._view_positions
    assert len(memory) == 32
    assert ("0",) not in memory
    assert all(not hasattr(snapshot, "items") for snapshot in memory.values())


def test_restore_waits_for_loading_results_before_reconciling_identity(environment):
    from dataclasses import replace

    from media_cli.ui.view_session import ReplaceView

    run(environment, view(), lambda options, hooks: 1)
    content = replace(view(()), state="loading")
    session = ViewSession(content, lambda event: ExitSession())

    def loaded(options, hooks):
        session.apply(ReplaceView(session.current.id, session.generation, view()))
        return -6

    def restored(options, hooks):
        assert options[hooks["state"].selected_index] == "two"
        assert hooks["auto_select_single_when_done"] is False
        return -2

    flags, ui, manager = environment
    view_picker.run_view_session(
        flags, ui, session, picker=ScriptedPicker(loaded, restored), poster_manager=manager, position_key=("menu",)
    )


def test_cancelled_preparation_keeps_only_ui_position_changes(monkeypatch):
    from types import SimpleNamespace

    from media_cli.app import terminal_controller
    from media_cli.context import CliFlags, MachineState, UiState
    from media_cli.ui.transition import PreparationCancelled
    from media_cli.ui.view_position_memory import SavedViewPosition

    saved = SavedViewPosition("two", "tw", 0, 1, None)
    ui, ms = UiState(), MachineState(query_str="original")

    def handler(flags, draft_ui, draft_ms, cfg, args):
        draft_ui._view_positions[("menu",)] = saved
        draft_ms.query_str = "abandoned"
        return "PLAY"

    def cancel(message, work):
        work()
        raise PreparationCancelled

    monkeypatch.setattr(terminal_controller.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(terminal_controller, "with_loading", cancel)
    assert (
        terminal_controller.run_screen_controller(
            handler, CliFlags(), ui, ms, {}, SimpleNamespace(), fallback="DETAILS"
        )
        == "DETAILS"
    )
    assert ui._view_positions == {("menu",): saved}
    assert ms.query_str == "original"
