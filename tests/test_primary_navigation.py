from types import SimpleNamespace

import pytest

from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.view_model import Blank, DetailLines, Inherit, ViewAction, ViewItem, ViewSpec
from media_cli.ui.view_presentation import ViewPresentation
from media_cli.ui.view_session import ViewSession
from media_cli.ui.view_tui import TuiViewBackend


def test_footer_only_advertises_primary_keys_but_help_keeps_arrows():
    spec = ViewSpec(
        "test",
        "Pick",
        (ViewItem("one", "One", details=Inherit),),
        details=DetailLines(Blank, Blank, Blank),
        actions=(ViewAction("select", "open", ("ENTER", "RIGHT")),),
    )
    assert ViewPresentation(ViewSession(spec), None)._footer().actions == ("Enter=open", "Esc=back")
    assert "[Enter/Right] open" in TuiViewBackend().action_labels(spec)


@pytest.mark.parametrize("key", ("LEFT", "RIGHT"))
def test_auxiliary_arrows_leave_filter_cursor_editing_to_picker(key):
    spec = ViewSpec(
        "test", "Pick", (ViewItem("one", "One", details=Inherit),), details=DetailLines(Blank, Blank, Blank)
    )
    session = ViewSession(spec)
    session.position.query = "on"
    emitted = []
    context = SimpleNamespace(
        view=spec,
        session=session,
        focus=lambda index: None,
        emit=lambda *args: emitted.append(args),
        tab=lambda *args: None,
    )
    assert not TuiViewBackend().handle_key(key, 0, context)
    assert emitted == []


@pytest.mark.parametrize("key", ("LEFT", "RIGHT"))
def test_explicit_arrow_control_action_precedes_auxiliary_navigation(key):
    spec = ViewSpec(
        "test", "Pick", (), details=DetailLines(Blank, Blank, Blank), actions=(ViewAction("adjust", "Adjust", (key,)),)
    )
    session = ViewSession(spec)
    emitted = []
    context = SimpleNamespace(
        view=spec,
        session=session,
        focus=lambda index: None,
        emit=lambda *args: emitted.append(args),
        tab=lambda *args: None,
    )
    assert TuiViewBackend().handle_key(key, 0, context)
    assert emitted == [("action", 0, "adjust")]
    assert ViewPresentation(session, None)._footer().actions == ("Esc=back",)


@pytest.mark.parametrize("code", (-2, -3))
@pytest.mark.parametrize("provider", ("streaming", "anilist"))
def test_regular_results_back_returns_to_query_entry(monkeypatch, code, provider):
    from media_cli import app_core
    from media_cli.app import anilist, search

    module = anilist if provider == "anilist" else search

    monkeypatch.setattr(module, "tui_pick", lambda *args, **kwargs: code)
    monkeypatch.setattr(app_core, "_clear_poster_downloads", lambda: None)
    monkeypatch.setattr(app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(
        app_core, "_cached_search_results", lambda *args: (None, lambda: [], lambda: "Loading", lambda: "")
    )
    ms = MachineState(query_str="wrong query")
    handler = anilist.handle_anilist_search_state if provider == "anilist" else search.handle_search_state
    result = handler(
        CliFlags(plain_mode=True, incognito_mode=True),
        UiState(),
        ms,
        {},
        SimpleNamespace(provider="anikoto"),
        "sub",
        lambda *args: False,
    )
    assert result == ("ANILIST_SEARCH" if provider == "anilist" else "SEARCH")
    assert ms.query_str == ""


@pytest.mark.parametrize("code", (-2, -3))
def test_animethemes_results_back_returns_to_its_query_entry(monkeypatch, code):
    from media_cli.app import animethemes

    class ImmediateThread:
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(animethemes.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(animethemes.animethemes_service, "search", lambda query: {})
    monkeypatch.setattr(animethemes.app_core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(animethemes.app_core, "make_info_fn", lambda *args: None)
    monkeypatch.setattr(animethemes, "tui_pick", lambda *args, **kwargs: code)
    ms = MachineState(query_str="wrong query")
    result = animethemes.handle_animethemes_search_state(
        CliFlags(plain_mode=True, incognito_mode=True),
        UiState(),
        ms,
        {},
        SimpleNamespace(query=["wrong query"]),
        "sub",
    )
    assert result == "ANIMETHEMES_SEARCH"
    assert ms.query_str == ""


def test_animethemes_query_entry_does_not_reuse_original_cli_query(monkeypatch):
    from media_cli.app import animethemes, search

    seen = []
    monkeypatch.setattr(search, "prompt_search_query", lambda *args, **kwargs: seen.append(kwargs) or -2, raising=False)
    monkeypatch.setattr(animethemes.animethemes_service, "search", lambda query: pytest.fail("Unexpected search"))
    result = animethemes.handle_animethemes_search_state(
        CliFlags(plain_mode=True, incognito_mode=True),
        UiState(),
        MachineState(),
        {},
        SimpleNamespace(query=["old query"]),
        "sub",
    )
    assert result == "QUIT"
    assert len(seen) == 1


@pytest.mark.parametrize("code", (-2, -3))
@pytest.mark.parametrize("count", (1, 2))
def test_title_menu_back_skips_only_an_auto_single_results_screen(monkeypatch, code, count):
    from media_cli import app_core
    from media_cli.app import playback_menu

    show = {"_id": "one", "name": "One", "_episode_ids": ["1"], "availableEpisodes": {"sub": 1}}
    ui = UiState(ui_show_ctx=show, search_prev_state="SEARCH", action_prev_state="SEARCH")
    ms = MachineState(query_str="original", shows=[show] * count, current_ep="1")
    monkeypatch.setattr(playback_menu, "tui_pick", lambda *args, **kwargs: code)
    monkeypatch.setattr(app_core, "ensure_episode_ids", lambda *args, **kwargs: ["1"])
    monkeypatch.setattr(app_core, "get_resume_time", lambda *args: 0)
    result = playback_menu.handle_action_menu_state(
        CliFlags(), ui, ms, {}, SimpleNamespace(), "sub", lambda *args: False
    )
    assert result == "SEARCH"
    assert ms.query_str == ("" if count == 1 else "original")
