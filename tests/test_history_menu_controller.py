"""History routing uses displayed identities and delegates application work."""

from types import SimpleNamespace

import pytest
from test_ui_view_terminal import Terminal

from media_cli.app.history_menu_view import build_history_view
from media_cli.ui.transition import PreparationCancelled
from media_cli.ui.view_session import UiEvent


@pytest.fixture
def menu(monkeypatch):
    monkeypatch.setattr("media_cli.domain.titles.get_cached_title_language", lambda: "romaji")
    source = SimpleNamespace(mode="Active", calls=[], readonly=False)
    source.entries = tuple(
        dict(show=dict(_id=identity, _provider="example", name="Example", _local_progress=0),
             translation_type="sub", timestamp=140)
        for identity in ("one", "two")
    )

    def read():
        return build_history_view(source.entries, mode=source.mode, total_count=2,
                                  now=200, show_cover=False, incognito=source.readonly)

    def switch(mode):
        source.calls.append(("tab", mode))
        source.mode = mode

    source.read, source.switch = read, switch
    return source


def controller(menu, **changes):
    from media_cli.app.history_menu_controller import HistoryMenuController

    options = dict(
        read_view=menu.read, switch_mode=menu.switch,
        select=lambda reference: menu.calls.append(("select", reference)) or True,
        refresh_title=lambda reference: menu.calls.append(("refresh_title", reference)),
        refresh_view=lambda: menu.calls.append(("refresh_view",)),
        delete=lambda reference: menu.calls.append(("delete", reference)),
        information=lambda reference: menu.calls.append(("information", reference)),
    )
    return HistoryMenuController(**(options | changes))


def send(control, kind, item_id=None, value=None):
    return control.session.dispatch(UiEvent(kind, control.session.current.id, control.session.generation, item_id, value))


@pytest.mark.parametrize("action", ["refresh_title", "delete", "information"])
def test_actions_use_displayed_reference_not_reordered_source_index(menu, action):
    control = controller(menu)
    row = control.session.current.items[0]
    menu.entries = menu.entries[::-1]
    send(control, "action", row.id, action)
    assert menu.calls == [(action, row.reference)]
    assert not control.session.exited


def test_selection_prepares_then_returns_typed_reference(menu):
    from media_cli.app.history_menu_controller import HistoryMenuResult

    control = controller(menu)
    row = control.session.current.items[1]
    send(control, "select", row.id)
    assert control.session.result == HistoryMenuResult("selected", row.reference)
    assert menu.calls == [("select", row.reference)]


@pytest.mark.parametrize("cancelled", [False, True])
def test_refused_or_cancelled_selection_retains_filter_and_cursor(menu, cancelled):
    from media_cli.ui.picker_state import PickerState

    def select(reference):
        if cancelled:
            raise PreparationCancelled
        return False

    control = controller(menu, select=select)
    row = control.session.current.items[0]
    control.session.position.query = "Example"
    frame = control.session.frame(control.session.current.id, control.session.generation)
    state = PickerState(query="Example", query_cursor=3)
    frame.picker_state = state
    send(control, "select", row.id)
    assert not control.session.exited
    assert control.session.position.query == "Example" and frame.picker_state is state
    assert state.query_cursor == 3


def test_tab_and_view_refresh_do_not_require_selected_row(menu):
    menu.entries = ()
    control = controller(menu)
    send(control, "tab", value="All")
    send(control, "action", value="refresh_view")
    send(control, "action", value="refresh_title")
    assert menu.calls == [("tab", "All"), ("refresh_view",)]
    assert control.session.current.active_tab_id == "All"


def test_readonly_view_never_dispatches_delete(menu):
    menu.readonly = True
    control = controller(menu, delete=None)
    assert not send(control, "action", control.session.current.items[0].id, "delete")
    assert menu.calls == []


@pytest.mark.parametrize("kind", ["back", "quit"])
def test_navigation_outcomes_are_distinct(menu, kind):
    control = controller(menu)
    send(control, kind)
    assert control.session.result.kind == kind


def test_missing_delete_handler_fails_during_development(menu):
    with pytest.raises(ValueError, match="delete"):
        controller(menu, delete=None)


def test_cancelled_tab_restores_previous_mode_and_query(menu):
    def switch(mode):
        raise PreparationCancelled

    control = controller(menu, switch_mode=switch)
    control.session.position.query = "Example"
    send(control, "tab", value="All")
    assert control.session.position.active_tab_id == "Active"
    assert control.session.position.query == "Example"


def test_prepared_updates_are_published_without_running_actions(menu):
    control = controller(menu)
    assert control.updates(force=True) == ()
    menu.entries = menu.entries[::-1]
    commands = control.updates(force=True)
    assert len(commands) == 1
    control.session.apply(commands[0])
    assert control.session.current.items[0].reference.show_id == "two"
    assert menu.calls == []


def test_focus_routes_the_highlighted_reference(menu):
    control = controller(menu, focus=lambda reference: menu.calls.append(("focus", reference)))
    row = control.session.current.items[1]
    send(control, "focus", row.id)
    assert menu.calls == [("focus", row.reference)]


def test_poll_runs_before_read_is_throttled_and_stops_on_exit(menu):
    now, polls = [0.0], []

    def poll():
        polls.append(now[0])
        menu.entries = menu.entries[::-1]

    control = controller(menu, poll=poll, clock=lambda: now[0])
    commands = control.updates()
    control.session.apply(commands[0])
    assert control.session.current.items[0].reference.show_id == "two"
    assert control.updates() == () and polls == [0.0]
    now[0] = 0.2
    assert control.updates() and polls == [0.0, 0.2]
    send(control, "quit")
    assert control.updates(force=True) == () and polls == [0.0, 0.2]


def test_poll_rejects_noncallable_during_development(menu):
    with pytest.raises(TypeError, match="callable"):
        controller(menu, poll=True)


def test_shared_renderer_accepts_worker_results_without_legacy_tick(tmp_path):
    source = """
from copy import deepcopy
from queue import SimpleQueue, Empty
from media_cli.app.history_menu_view import build_history_view
from media_cli.app.history_menu_controller import run_history_menu
from media_cli.ui.view_position_memory import SavedViewPosition
owner, worker_threads, accepted = threading.get_ident(), [], []
inbox = SimpleQueue()
entries = [dict(show=dict(_id='one', _provider='example', name='Same title'),
                translation_type='sub', timestamp=140)]
ui._view_positions = {('history',): SavedViewPosition('history:example:one:sub', 'Same', 0, 2, 'All')}
def refresh(reference):
    draft = deepcopy(entries)
    def work():
        worker_threads.append(threading.get_ident())
        draft[0]['timestamp'] = 200
        inbox.put(draft)
    threading.Thread(target=work, daemon=True).start()
def poll():
    try:
        result = inbox.get_nowait()
    except Empty:
        return
    accepted.append(threading.get_ident())
    entries[:] = result
def read():
    return build_history_view(entries, mode='All', total_count=1, now=200, show_cover=False)
result = run_history_menu(flags, ui, read_view=read, switch_mode=lambda mode: None,
    select=lambda ref: True, refresh_title=refresh, refresh_view=lambda: None,
    delete=lambda ref: None, information=lambda ref: None, poll=poll)
saved = ui._view_positions[('history',)]
finish(dict(kind=result.kind, owner_acceptance=accepted == [owner],
            detached_worker=len(worker_threads) == 1 and worker_threads[0] != owner,
            query=saved.query, cursor=saved.query_cursor, selected=saved.selected_id))
"""
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Updated 1m ago", b"\x12")
        terminal.send_after(b"Updated just now", b"\x1b")
        assert terminal.result() == dict(kind="quit", owner_acceptance=True, detached_worker=True,
                                         query="Same", cursor=2, selected="history:example:one:sub")


def test_selection_callback_must_return_a_boolean(menu):
    control = controller(menu, select=lambda reference: None)
    with pytest.raises(TypeError, match="boolean"):
        send(control, "select", control.session.current.items[0].id)


def test_shared_history_focus_waits_for_idle_and_ignores_passed_rows(tmp_path):
    source = """
from media_cli.app.history_menu_view import build_history_view
from media_cli.app.history_menu_controller import run_history_menu
started, focused = time.monotonic(), []
entries = [dict(show=dict(_id=identity, _provider='example', name=name), translation_type='sub')
           for identity, name in [('one', 'First title'), ('two', 'Second title')]]
def focus(reference):
    focused.append((reference.show_id, time.monotonic() - started))
    print('IDLE_REFRESH_READY', flush=True)
result = run_history_menu(flags, ui,
    read_view=lambda: build_history_view(entries, mode='All', total_count=2, show_cover=False),
    switch_mode=lambda mode: None, select=lambda ref: True,
    refresh_title=lambda ref: None, refresh_view=lambda: None,
    delete=lambda ref: None, information=lambda ref: None, focus=focus)
finish(dict(focused=focused, selected=result.reference.show_id))
"""
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Watch History", b"\x1b[A")
        terminal.send_after(b"IDLE_REFRESH_READY", b"\r")
        result = terminal.result()
    assert len(result["focused"]) == 1 and result["focused"][0][0] == "two"
    assert result["focused"][0][1] >= 1.15
    assert result["selected"] == "two"


@pytest.mark.parametrize("key,expected", [(b"\t", "Up to date"), (b"\x0e", "Up to date"),
                                          (b"\x1b[Z", "All"), (b"\x10", "All")])
def test_history_tab_keys_route_in_the_correct_direction(tmp_path, key, expected):
    source = """
from media_cli.app.history_menu_view import build_history_view
from media_cli.app.history_menu_controller import run_history_menu
state = {'mode': 'Active'}
entries = (dict(show=dict(_id='one', _provider='example', name='Example', _local_progress=0),
                translation_type='sub', timestamp=140),)
def read():
    return build_history_view(entries, mode=state['mode'], total_count=1, now=200, show_cover=False)
def switch(mode):
    state['mode'] = mode
result = run_history_menu(flags, ui, read_view=read, switch_mode=switch, select=lambda ref: True,
    refresh_title=lambda ref: None, refresh_view=lambda: None,
    delete=lambda ref: None, information=lambda ref: None)
finish(dict(mode=state['mode'], kind=result.kind, selected=result.reference.show_id))
"""
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Updated 1m ago", key)
        terminal.send_after(expected.encode(), b"\r")
        assert terminal.result() == {"mode": expected, "kind": "selected", "selected": "one"}
