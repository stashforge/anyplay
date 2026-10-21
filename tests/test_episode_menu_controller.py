"""Episode actions reuse the shared controller without index-based dispatch."""

import os
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_ui_view_terminal import Terminal

from media_cli.app.details_menu_controller import DetailsMenuController, DetailsMenuResult
from media_cli.app.episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from media_cli.context import UiState
from media_cli.ui.picker_state import PickerState
from media_cli.ui.transition import PreparationCancelled
from media_cli.ui.view_session import UiEvent


@pytest.fixture
def menu(monkeypatch):
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    source = SimpleNamespace(options=(EpisodeMenuOption("one", "Episode 1", downloaded=True),
                                     EpisodeMenuOption("two", "Episode 2")), calls=[])

    def read():
        return build_episode_menu_view(
            {"_id": "show", "name": "Example"}, source.options,
            view_id="episodes:example:show", image_key="example:show", ttype="sub", show_cover=False,
        )

    def flip(reference):
        source.calls.append(("flip", reference))
        source.options = tuple(reversed(source.options))

    def delete(reference):
        source.calls.append(("delete", reference))
        source.options = tuple(replace(row, downloaded=False) if row.episode_id == reference else row
                               for row in source.options)

    source.read, source.handlers = read, {"flip": flip, "delete": delete}
    return source


def controller(menu):
    return DetailsMenuController(ui=UiState(), read_view=menu.read, switch_translation=lambda: None,
                                 information=lambda: None, action_handlers=menu.handlers)


def send(control, action, item_id="episode:sub:one"):
    control.session.dispatch(UiEvent("action", control.session.current.id, control.session.generation,
                                     item_id, action))


def test_flip_preserves_identity_filter_and_existing_picker_state(menu):
    control = controller(menu)
    control.session.position.selected_id = "episode:sub:one"
    control.session.position.query = "Episode 1"
    state = PickerState(query="Episode 1", query_cursor=4, scroll=1)
    frame = control.session.frame(control.session.current.id, control.session.generation)
    frame.picker_state = state
    send(control, "flip")
    assert menu.calls == [("flip", "one")]
    assert control.session.current.items[0].reference == "two"
    assert control.session.position.selected_id == "episode:sub:one"
    assert control.session.position.query == "Episode 1"
    assert control.session.frame(control.session.current.id, control.session.generation).picker_state is state
    assert state.query_cursor == 4


def test_delete_receives_displayed_episode_id_not_changed_source_index(menu):
    control = controller(menu)
    menu.options = tuple(reversed(menu.options))
    send(control, "delete")
    assert menu.calls == [("delete", "one")]
    assert control.session.item("episode:sub:one").hint_text == ""
    assert not any(action.id == "delete" for action in control.session.current.actions)
    assert not control.session.exited


def test_cancelled_action_returns_to_same_menu_without_selection(menu):
    def cancel(reference):
        raise PreparationCancelled

    menu.handlers["delete"] = cancel
    control = controller(menu)
    control.session.position.query = "Episode 1"
    send(control, "delete")
    assert not control.session.exited
    assert control.session.position.query == "Episode 1"
    assert control.session.item("episode:sub:one").hint_text == "ready to watch"


def test_selection_still_returns_the_provider_episode_id(menu):
    control = controller(menu)
    control.session.dispatch(UiEvent("select", control.session.current.id, control.session.generation,
                                     "episode:sub:two"))
    assert control.session.result == DetailsMenuResult("selected", "two")


def test_missing_custom_action_callback_fails_early(menu):
    menu.handlers.pop("flip")
    with pytest.raises(ValueError, match="flip"):
        controller(menu)


def test_live_flip_shortcut_keeps_filtered_episode_selection(tmp_path):
    script = """
from media_cli.app.details_menu_controller import run_details_menu
from media_cli.app.episode_menu_view import EpisodeMenuOption, build_episode_menu_view
from media_cli.context import UiState
ui = UiState(ui_show_ctx={'_id': 'show', 'name': 'Example'})
rows = [EpisodeMenuOption('one', 'Episode 1'), EpisodeMenuOption('two', 'Episode 2')]
calls = []
def read():
    return build_episode_menu_view({'_id': 'show', 'name': 'Example'}, rows,
        view_id='episodes:example:show', image_key='example:show', ttype='sub', show_cover=False,
        feedback='Flipped ' + calls[-1] if calls else '')
def flip(reference):
    calls.append(reference)
    rows.reverse()
result = run_details_menu(flags, ui, read_view=read, switch_translation=lambda: None,
    information=lambda: None, action_handlers={'flip': flip})
finish(dict(kind=result.kind, selected=result.action_id, calls=calls, query=ui.active_picker_query))
"""
    with Terminal(tmp_path, script) as terminal:
        terminal.until(b"Select episode")
        os.write(terminal.master, b"Episode 1\x12")
        terminal.until(b"Flipped one")
        os.write(terminal.master, b"\r")
        assert terminal.result() == {"kind": "selected", "selected": "one", "calls": ["one"], "query": "Episode 1"}
