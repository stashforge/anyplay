"""Live History delegates basic navigation to its shared controller."""

from copy import deepcopy
from types import SimpleNamespace
from threading import Event

import pytest
from test_ui_view_terminal import Terminal

from media_cli.app import history_menu_controller, search
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.transition import PreparationCancelled, call_on_ui_thread, run_preparation


@pytest.fixture
def navigation(monkeypatch):
    state = SimpleNamespace(events=[], prepared=[], writes=[])
    state.entries = [dict(show=dict(_id="one", _provider=provider, name="Same title",
                                   availableEpisodes={"sub": 3}, _local_progress=1),
                          episode="1", translation_type="sub", progress=1)
                     for provider in ("first", "second")]
    original = history_menu_controller.HistoryMenuController

    class ObservedController(original):
        def _handle_event(self, event):
            state.events.append(event.kind)
            return super()._handle_event(event)

    monkeypatch.setattr(history_menu_controller, "HistoryMenuController", ObservedController)
    monkeypatch.setattr(search.app_core, "load_history", lambda: deepcopy(state.entries))
    monkeypatch.setattr(search.app_core, "batch_prepare_shows_display_state", lambda *a: None)
    monkeypatch.setattr(search.app_core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(search.app_core, "filter_history_entries", lambda entries, mode: list(entries))
    monkeypatch.setattr(search.app_core, "format_history_entry", lambda entry: entry["show"]["name"])
    monkeypatch.setattr(search.app_core, "_poster_manager", SimpleNamespace())
    monkeypatch.setattr(search.app_core, "_get_poster", lambda *a: "")
    monkeypatch.setattr(search.app_core, "make_info_fn", lambda *a: None)
    monkeypatch.setattr(search.app_core, "patch_history_entry_state", lambda entry: state.writes.append(deepcopy(entry)))
    monkeypatch.setattr(search.history_domain, "history_entry_refresh_is_stale", lambda *a, **kw: False)
    monkeypatch.setattr(search.app_core, "with_loading", lambda message, work: work())
    monkeypatch.setattr(search.app_core, "playback_ep_from_history_entry", lambda *a, **kw: "1")

    def episodes(show, ttype):
        state.prepared.append(show["_provider"])
        show.update(_episode_ids=["1", "2", "3"], _episode_ids_ttype=ttype)
        return show["_episode_ids"]

    monkeypatch.setattr(search.app_core, "ensure_episode_ids", episodes)
    state.ui, state.machine = UiState(history_filter="All"), MachineState()
    state.enter = lambda: search.handle_history_state(
        CliFlags(incognito_mode=True), state.ui, state.machine, {}, SimpleNamespace(), "sub", None,
    )
    return state


def test_history_reuses_rows_while_footer_changes_and_invalidates_after_info_and_tab(monkeypatch, navigation):
    from media_cli.app.history_menu_controller import HistoryMenuResult
    from media_cli.ui.footer import FooterStatus, StatusKind

    builds = []
    original = search.build_history_view

    def build(*args, **kwargs):
        builds.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(search, 'build_history_view', build)
    monkeypatch.setattr(search.sys.stdin, 'isatty', lambda: True)
    footer = [None]
    monkeypatch.setattr(search.BackgroundLoadingStatus, 'read', lambda *a, **kw: footer[0])

    def information(shows, ui):
        return lambda index: shows()[index].update(name='Updated title')

    monkeypatch.setattr(search.app_core, 'make_info_fn', information)

    def menu(**hooks):
        read = hooks['read_view']
        first = read()
        assert read().items is first.items
        footer[0] = FooterStatus('Refreshing this title...', StatusKind.LOADING, '~')
        loading = read()
        assert loading.items[0] is first.items[0]
        assert loading.footer_statuses == (footer[0],)
        footer[0] = None
        assert not read().footer_statuses
        assert len(builds) == 1, 'Idle/status reads rebuilt all History rows'

        hooks['information'](first.items[0].reference)
        updated = read()
        assert updated.items[0].label_text == 'Updated title'
        assert len(builds) == 2
        hooks['switch_mode']('Active')
        assert read().active_tab_id == 'Active'
        assert len(builds) == 3
        return HistoryMenuResult('back')

    monkeypatch.setattr(history_menu_controller, 'run_history_menu', lambda flags, ui, **hooks: menu(**hooks))
    assert navigation.enter() == 'SEARCH'


def test_history_cache_accepts_refresh_results_and_deletion(monkeypatch, navigation):
    import time

    from media_cli.app.history_menu_controller import HistoryMenuResult

    monkeypatch.setattr(search.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(search.history_domain, 'history_entry_should_auto_refresh', lambda *a, **kw: False)
    monkeypatch.setattr(search.app_core, 'get_show_anilist_id', lambda show: None)
    monkeypatch.setattr(search.app_core, 'refresh_history_anilist_airing_batch', lambda *a, **kw: False)

    def refresh(entry):
        entry['show']['name'] = 'Refreshed title'
        return True

    def delete(*args, **kwargs):
        navigation.entries.pop(0)
        return True

    monkeypatch.setattr(search.app_core, 'refresh_history_entry_provider_catalog', refresh)
    monkeypatch.setattr(search.app_core, 'delete_history_entry', delete)

    def menu(**hooks):
        first = hooks['read_view']()
        reference = first.items[0].reference
        hooks['refresh_title'](reference)
        for _ in range(200):
            hooks['poll']()
            current = hooks['read_view']()
            if current.items[0].label_text == 'Refreshed title':
                break
            time.sleep(0.005)
        assert current.items[0].label_text == 'Refreshed title'
        assert hooks['read_view']().items is current.items
        hooks['delete'](reference)
        remaining = hooks['read_view']()
        assert len(remaining.items) == remaining.total_count == 1
        assert remaining.items[0].reference.provider_id == 'second'
        return HistoryMenuResult('back')

    monkeypatch.setattr(history_menu_controller, 'run_history_menu', lambda flags, ui, **hooks: menu(**hooks))
    assert search.handle_history_state(
        CliFlags(), navigation.ui, navigation.machine, {}, SimpleNamespace(), 'sub', None,
    ) == 'SEARCH'


@pytest.mark.parametrize("through_preparation", [False, True])
@pytest.mark.parametrize("outcome,expected,event", [(-2, "QUIT", "quit"), (-3, "SEARCH", "back"), (1, "DETAILS", "select")])
def test_live_history_basic_events_use_shared_controller(monkeypatch, navigation, through_preparation, outcome, expected, event):
    def pick(*args, **hooks):
        if outcome >= 0:
            assert hooks["select_fn"](outcome) is True
        return outcome

    monkeypatch.setattr(search, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: pick(*a, **kw)))
    result = run_preparation(navigation.enter, tick=lambda: None) if through_preparation else navigation.enter()
    assert result == expected
    assert navigation.events == [event]
    if outcome >= 0:
        assert navigation.prepared == ["second"]
        assert navigation.ui.ui_show_ctx["_provider"] == "second"
        assert navigation.machine.current_ep == "1"


def test_cancelled_selection_stays_in_history_without_accepting_prepared_data(monkeypatch, navigation):
    def cancel(message, work):
        raise PreparationCancelled

    monkeypatch.setattr(search.app_core, "with_loading", cancel)

    def pick(*args, **hooks):
        assert hooks["select_fn"](1) is False
        return -3

    monkeypatch.setattr(search, "tui_pick", pick)
    assert navigation.enter() == "SEARCH"
    assert navigation.events == ["select", "back"]
    assert not navigation.writes and not navigation.prepared


@pytest.mark.parametrize("through_preparation", [False, True])
def test_information_routes_exact_identity_and_preserves_picker(monkeypatch, navigation, through_preparation):
    shown = []

    def information(shows, ui):
        def open_info(index):
            items = shows()
            if 0 <= index < len(items):
                shown.append(items[index]["_provider"])
        return open_info

    monkeypatch.setattr(search.app_core, "make_info_fn", information)

    def pick(*args, **hooks):
        state = hooks["state"]
        state.query, state.query_cursor, state.selected_index = "Same", 2, 1
        hooks["info_fn"](1)
        hooks["info_fn"](-1)
        hooks["info_fn"](20)
        assert shown == ["second"]
        assert (state.query, state.query_cursor, state.selected_index) == ("Same", 2, 1)
        assert hooks["help_dict"]["Ctrl+O"] == "Title information"
        shared = search.build_history_view(navigation.entries, mode="All", total_count=2, incognito=True)
        assert hooks["help_dict"] == dict(shared.help)
        return -3

    monkeypatch.setattr(search, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: pick(*a, **kw)))
    result = run_preparation(navigation.enter, tick=lambda: None) if through_preparation else navigation.enter()
    assert result == "SEARCH" and navigation.events == ["action", "back"]
    assert not navigation.prepared and not navigation.writes


def test_history_filter_and_identity_restore_after_details_and_reorder(monkeypatch, navigation):
    def first(*args, **hooks):
        state = hooks["state"]
        state.query, state.query_cursor, state.selected_index = "Same", 4, 1
        assert hooks["select_fn"](1) is True
        return 1

    monkeypatch.setattr(search, "tui_pick", first)
    assert navigation.enter() == "DETAILS"
    navigation.entries.reverse()
    from media_cli.ui.picker_state import PickerState

    navigation.ui._last_picker_position = ("Select action", PickerState(query="unrelated"))

    def second(*args, **hooks):
        state = hooks["state"]
        assert state.query == "Same" and state.query_cursor == 4
        assert state.selected_index == 0
        assert hooks["item_key_fn"](state.selected_index).provider_id == "second"
        return -3

    monkeypatch.setattr(search, "tui_pick", second)
    assert navigation.enter() == "SEARCH"


def test_hover_refresh_uses_shared_focus_and_deduplicates_full_reference(monkeypatch, navigation):
    controls, checked = [], []
    original = history_menu_controller.HistoryMenuController
    completed = Event()

    class ObservedController(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            controls.append(self)

    monkeypatch.setattr(history_menu_controller, "HistoryMenuController", ObservedController)
    monkeypatch.setattr(search.history_domain, "history_entry_should_auto_refresh", lambda *a, **kw: True)
    monkeypatch.setattr(search.app_core, "get_show_anilist_id", lambda show: None)

    def refresh(entry):
        checked.append(entry["show"]["_provider"])
        completed.set()
        return False

    monkeypatch.setattr(search.app_core, "refresh_history_entry_provider_catalog", refresh)

    def pick(*args, **hooks):
        from media_cli.ui.view_session import UiEvent

        control = controls[0]
        for row in control.session.current.items:
            completed.clear()
            control.session.dispatch(UiEvent("focus", control.session.current.id,
                                            control.session.generation, row.id))
            assert completed.wait(1)
        row = control.session.current.items[0]
        control.session.dispatch(UiEvent("focus", control.session.current.id,
                                        control.session.generation, row.id))
        assert checked == ["first", "second"]
        return -3

    monkeypatch.setattr(search, "tui_pick", pick)
    assert navigation.enter() == "SEARCH"


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize("through_preparation", [False, True])
def test_live_refresh_routes_through_controller_without_resetting_filter(monkeypatch, navigation, visible, through_preparation):
    from media_cli.app.history_refresh_handoff import HistoryRefreshInbox

    done, refreshed = Event(), []

    class ObservedInbox(HistoryRefreshInbox):
        def prepare(self, *args):
            try:
                return super().prepare(*args)
            finally:
                done.set()

    def refresh(entry):
        refreshed.append(entry["show"]["_provider"])
        entry["show"]["availableEpisodes"]["sub"] = 4
        return True

    monkeypatch.setattr(search, "HistoryRefreshInbox", ObservedInbox)
    monkeypatch.setattr(search, "history_refresh_status", {})
    monkeypatch.setattr(search.app_core, "get_show_anilist_id", lambda show: None)
    monkeypatch.setattr(search.app_core, "refresh_history_anilist_airing_batch", lambda *a, **kw: False)
    monkeypatch.setattr(search.app_core, "refresh_history_entry_provider_catalog", refresh)
    monkeypatch.setattr(search.app_core, "build_info_panel", lambda *a, **kw: None)
    monkeypatch.setattr(search.app_core, "_poster_footer_line", lambda *a: "")

    def pick(*args, **hooks):
        state = hooks["state"]
        state.query, state.query_cursor, state.selected_index = "Same", 3, 1
        selected = hooks["item_key_fn"](1)
        hook = hooks["alt_reverse_fn"] if visible else hooks["reverse_fn"]
        hook(1)
        status = search.history_refresh_status["BATCH" if visible else "SINGLE"]
        assert status == ("Refreshing 2 titles..." if visible else "Refreshing this title...")
        assert done.wait(2)
        hooks["tick_fn"]()
        assert hooks["item_key_fn"](state.selected_index) == selected
        assert state.query == "Same" and state.query_cursor == 3
        return -3

    monkeypatch.setattr(search, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: pick(*a, **kw)))
    result = run_preparation(navigation.enter, tick=lambda: None) if through_preparation else navigation.enter()
    assert result == "SEARCH"
    assert navigation.events == ["action", "back"]
    assert set(refreshed) == ({"first", "second"} if visible else {"second"})


@pytest.mark.parametrize("through_preparation", [False, True])
def test_confirmed_delete_routes_identity_and_retains_filter(monkeypatch, navigation, through_preparation):
    deleted = []

    def delete(show_id, translation, *, provider_id=None):
        deleted.append((provider_id, show_id, translation))
        navigation.entries[:] = [entry for entry in navigation.entries if entry["show"]["_provider"] != provider_id]
        return True

    monkeypatch.setattr(search.app_core, "delete_history_entry", delete)
    monkeypatch.setattr(search.app_core, "build_info_panel", lambda *a, **kw: None)
    monkeypatch.setattr(search.app_core, "_poster_footer_line", lambda *a: "")

    def pick(*args, **hooks):
        state = hooks["state"]
        state.query, state.query_cursor, state.selected_index = "Same", 3, 1
        assert hooks["can_delete_fn"](1) is True
        monkeypatch.setattr(search.app_core, "filter_history_entries", lambda entries, mode: list(reversed(entries)))
        hooks["tab_fn"]()
        rows, header = hooks["delete_fn"](1)
        assert len(rows) == 1 and hooks["item_key_fn"](0).provider_id == "first"
        assert state.query == "Same" and state.query_cursor == 3
        state.selected_index = 0
        return -3

    monkeypatch.setattr(search, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: pick(*a, **kw)))
    def enter():
        return search.handle_history_state(CliFlags(plain_mode=True), navigation.ui, navigation.machine,
                                           {}, SimpleNamespace(), "sub", None)
    result = run_preparation(enter, tick=lambda: None) if through_preparation else enter()
    assert result == "SEARCH" and navigation.events == ["tab", "action", "back"]
    assert deleted == [("second", "one", "sub")]


def test_readonly_history_does_not_offer_delete(monkeypatch, navigation):
    def pick(*args, **hooks):
        assert hooks["delete_fn"] is None
        return -3

    monkeypatch.setattr(search, "tui_pick", pick)
    assert navigation.enter() == "SEARCH"


@pytest.mark.parametrize("direction,expected", [(1, "Active"), (-1, "Completed")])
@pytest.mark.parametrize("through_preparation", [False, True])
def test_live_tabs_use_shared_controller_and_keep_filter(monkeypatch, navigation, direction, expected, through_preparation):
    modes = []

    def filtered(entries, mode):
        modes.append(mode)
        return [] if mode == "Completed" else list(entries)

    monkeypatch.setattr(search.app_core, "filter_history_entries", filtered)
    monkeypatch.setattr(search.app_core, "build_info_panel", lambda *a, **kw: None)
    monkeypatch.setattr(search.app_core, "_poster_footer_line", lambda *a: "")

    def pick(*args, **hooks):
        state = hooks["state"]
        state.query, state.query_cursor = "Same", 3
        rows, header = hooks["tab_fn"](None, direction=direction)
        assert navigation.ui.history_filter == expected
        assert len(rows) == (0 if expected == "Completed" else 2)
        assert state.query == "Same" and state.query_cursor == 3
        return -3

    monkeypatch.setattr(search, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: pick(*a, **kw)))
    result = run_preparation(navigation.enter, tick=lambda: None) if through_preparation else navigation.enter()
    assert result == "SEARCH" and navigation.events == ["tab", "back"]
    assert expected in modes


@pytest.mark.parametrize("key,expected,event", [(b"\x1b", "QUIT", "quit"), (b"\x1b[D", "SEARCH", "back"),
                                               (b"Second\r", "DETAILS", "select"),
                                               (b"\x12", "QUIT", "refresh_title"),
                                               (b"\x1b[15~", "QUIT", "refresh_view"),
                                               (b"\x1br", "QUIT", "refresh_view"),
                                               (b"title\x1b[3~", "QUIT", "delete"),
                                               (b"title\x04", "QUIT", "delete"),
                                               (b"title\x1b[3~", "QUIT", "delete_cancel"),
                                               (b"title\t", "QUIT", "tab_next"),
                                               (b"title\x0e", "QUIT", "tab_next"),
                                               (b"title\x1b[Z", "QUIT", "tab_previous"),
                                               (b"title\x10", "QUIT", "tab_previous"),
                                               (b"Second\x0f", "QUIT", "information"),
                                               (b"Second?", "QUIT", "help"),
                                               (b"Second\r", "DETAILS", "select_without_episode_wait")])
def test_real_terminal_history_shared_renderer(tmp_path, key, expected, event):
    source = """
from copy import deepcopy
from media_cli.app import search, history_menu_controller
from media_cli.app.terminal_controller import run_screen_controller
from media_cli.context import CliFlags, UiState, MachineState
events, actions, deleted = [], [], []
refresh_release = threading.Event()
original = history_menu_controller.HistoryMenuController
class ObservedController(original):
    def _handle_event(self, event):
        events.append(event.kind)
        if event.kind == 'action':
            actions.append(event.value)
        return super()._handle_event(event)
history_menu_controller.HistoryMenuController = ObservedController
entries = [dict(show=dict(_id='one', _provider=provider, name=name, availableEpisodes={'sub': 3}),
                episode='1', translation_type='sub', progress=1)
           for provider, name in [('first', 'First title'), ('second', 'Second title')]]
for entry in entries:
    entry['show'].update(_enrichment_attempted=True, description='Information for ' + entry['show']['_provider'])
search.app_core.load_history = lambda: deepcopy(entries)
search.app_core.filter_history_entries = lambda entries, mode: list(entries)
search.app_core.batch_prepare_shows_display_state = lambda *a: None
search.app_core.prepare_show_display_state = lambda *a: None
search.app_core._poster_manager = SimpleNamespace()
search.app_core._get_poster = lambda *a: ''
search.app_core.ensure_episode_ids = lambda *a: ['1', '2', '3']
search.app_core.playback_ep_from_history_entry = lambda *a, **kw: '1'
search.app_core.patch_history_entry_state = lambda *a: True
search.app_core.with_loading = lambda message, work: work()
search.app_core.get_show_anilist_id = lambda show: None
search.app_core.refresh_history_anilist_airing_batch = lambda *a, **kw: False
def refresh(entry):
    refresh_release.wait(2)
    return False
search.app_core.refresh_history_entry_provider_catalog = refresh
search.history_domain.history_entry_should_auto_refresh = lambda *a, **kw: False
def delete(show_id, translation, *, provider_id=None):
    deleted.append(provider_id)
    entries[:] = [entry for entry in entries if entry['show']['_provider'] != provider_id]
    return True
search.app_core.delete_history_entry = delete
search.history_domain.history_entry_refresh_is_stale = lambda *a, **kw: False
def legacy_picker(*args, **kwargs):
    raise AssertionError('Interactive History must use the shared renderer')
search.tui_pick = legacy_picker
def legacy_rows(*args, **kwargs):
    raise AssertionError('Shared History must not assemble legacy row strings')
search.app_core.format_history_entry = legacy_rows
flags, ui, ms = CliFlags(incognito_mode=True, show_image=False), UiState(history_filter='All'), MachineState()
result = run_screen_controller(search.handle_history_state, flags, ui, ms, {}, SimpleNamespace(),
                               'sub', None, fallback='HISTORY')
first_result, provider = result, ui.ui_show_ctx.get('_provider')
restored = {}
if result == 'DETAILS':
    from media_cli.ui.picker_state import PickerState
    ui._last_picker_position = ('Select action', PickerState(query='unrelated'))
    entries.reverse()
    print('REOPEN_BEGIN', flush=True)
    result = run_screen_controller(search.handle_history_state, flags, ui, ms, {}, SimpleNamespace(),
                                   'sub', None, fallback='HISTORY')
    saved = ui._view_positions[('history',)]
    rows = search.build_history_view(entries, mode='All', total_count=len(entries)).items
    restored.update(query=saved.query, index=next(i for i, row in enumerate(rows) if row.id == saved.selected_id))
refresh_release.set()
finish(dict(state=first_result, events=events, actions=actions, deleted=deleted, provider=provider,
            reopened=result, restored=restored, query=ui._view_positions[('history',)].query, mode=ui.history_filter))
"""
    if event.startswith("delete"):
        source = source.replace("CliFlags(incognito_mode=True,", "CliFlags(incognito_mode=False,")
    if event == "select_without_episode_wait":
        source = source.replace("search.app_core.ensure_episode_ids = lambda *a: ['1', '2', '3']",
                                "def unexpected_episode_fetch(*args, **kwargs):\n"
                                "    raise AssertionError('History must defer episode fetching to Details')\n"
                                "search.app_core.ensure_episode_ids = unexpected_episode_fetch")
    selection = event in ("select", "select_without_episode_wait")
    with Terminal(tmp_path, source, columns=140) as terminal:
        terminal.send_after(b"Watch History", key)
        if selection:
            terminal.until(b"REOPEN_BEGIN")
            terminal.send_after(b"Watch History", b"\x1b")
        if event.startswith("refresh_"):
            message = b"Refreshing this title..." if event == "refresh_title" else b"Refreshing 2 titles..."
            terminal.send_after(message, b"\x1b")
        if event.startswith("delete"):
            terminal.send_after(b"Delete this history entry? y/N", b"n" if event == "delete_cancel" else b"y")
            terminal.send_after(b"Watch History", b"\x1b")
        if event.startswith("tab_"):
            terminal.send_after(b"Active" if event == "tab_next" else b"Completed", b"\x1b")
        if event in ("information", "help"):
            terminal.send_after(b"Information for second" if event == "information" else b"Title information", b"q")
            terminal.send_after(b"Watch History", b"\x1b")
        result = terminal.result()
        assert result["state"] == expected
        expected_events = ["action", "quit"] if event.startswith("refresh_") else [event]
        if event.startswith("delete"):
            expected_events = ["action", "quit"]
        if event.startswith("tab_"):
            expected_events = ["tab", "quit"]
        if event in ("information", "help"):
            expected_events = ["action", "quit"] if event == "information" else ["quit"]
        assert result["events"] == (["select", "quit"] if selection else expected_events)
        if event.startswith("refresh_"):
            assert result["actions"] == [event]
        if event.startswith("delete"):
            assert len(result["deleted"]) == (0 if event == "delete_cancel" else 1)
            assert result["actions"] == ["delete"]
            assert result["query"] == "title"
        if event.startswith("tab_"):
            assert result["query"] == "title"
            assert result["mode"] == ("Active" if event == "tab_next" else "Completed")
        if event in ("information", "help"):
            assert result["query"] == "Second"
            assert result["actions"] == (["information"] if event == "information" else [])
        if event == "select_without_episode_wait":
            assert b"Checking episode list" not in terminal.output
        if selection:
            assert result["provider"] == "second"
            assert result["reopened"] == "QUIT"
            assert result["restored"] == {"query": "Second", "index": 0}
