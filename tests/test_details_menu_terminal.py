"""Live action-menu wiring exercised through the real shared picker."""

import os

import pytest
from test_ui_view_terminal import Terminal


def menu_script(*, plain=False, cancel_switch=False):
    return f"""
from media_cli import app_core
from media_cli.app.playback_menu import handle_action_menu_state
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.transition import PreparationCancelled
flags = CliFlags(plain_mode={plain!r}, incognito_mode=True, show_image=False)
show = dict(_id='one', _provider='example', _provider_name='Example', name='Example Show',
    englishName='Another name', status='FINISHED', type='TV', episodeCount=2,
    _episode_ids=['1','2'], _episode_labels={{'1':'1','2':'2'}}, _local_progress=0)
ui = UiState(ui_show_ctx=show, ui_ttype_ctx='sub', search_prev_state='SEARCH', action_prev_state='SEARCH')
ms = MachineState(show_id='one', show_title='Example Show', current_ep='1', shows=[show, show])
app_core.ensure_episode_ids = lambda *a, **kw: ('1','2')
app_core.get_show_anilist_id = lambda *a: None
app_core.get_local_progress = lambda *a: 0
app_core.get_resume_time = lambda *a: 0
app_core.prepare_show_display_state = lambda *a, **kw: None
app_core.check_translation_switch_capability = lambda *a: (True, '')
app_core.is_confirmed_offline = lambda: False
app_core.with_loading = lambda message, fn, *a, **kw: fn(*a, **kw)
def information(*args):
    app_core.set_action_feedback(show, 'INFO-SEEN')
app_core.make_single_show_info_fn = lambda *a: information
if {cancel_switch!r}:
    def cancel(*a, **kw):
        app_core.set_action_feedback(show, 'SWITCH-CANCELLED')
        raise PreparationCancelled
    app_core.with_loading = cancel
state = handle_action_menu_state(flags, ui, ms, {{}}, SimpleNamespace(), 'sub', lambda *a: False)
finish(dict(state=state, ttype=ui.ui_ttype_ctx, query=ui.active_picker_query, episode=ms.current_ep))
"""


def test_live_menu_filter_selects_domain_action_without_opening_player(tmp_path):
    with Terminal(tmp_path, menu_script()) as terminal:
        terminal.send_after(b"Esc=back", b"Play\r")
        assert terminal.result()["state"] == "PLAY"


def test_title_refresh_reopens_menu_with_filter_and_selection(tmp_path):
    script = menu_script().replace(
        "state = handle_action_menu_state(",
        "app_core.refresh_history_entry_provider_catalog = lambda *a, **kw: True\nstate = handle_action_menu_state(",
    ).replace(
        "finish(dict(",
        "assert state == 'DETAILS'\n"
        "state = handle_action_menu_state(flags, ui, ms, {}, SimpleNamespace(), 'sub', lambda *a: False)\n"
        "finish(dict(",
    )
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Esc=back", b"Progress\x12")
        terminal.send_after(b"Title refreshed", b"\x12")
        result = terminal.result()
        assert result["state"] == "DETAILS"
        assert result["query"] == "Progress"


@pytest.mark.parametrize("key", [b"\t", b"\x0e", b"\x1b[Z"])
def test_translation_keys_refresh_metadata_and_keep_filter(tmp_path, key):
    with Terminal(tmp_path, menu_script()) as terminal:
        terminal.send_after(b"Esc=back", b"Progress")
        terminal.send_after(b"Progress", key)
        terminal.send_after(b"DUB", b"\x1b")
        result = terminal.result()
        assert result["state"] == "SEARCH"
        assert result["ttype"] == "dub"
        assert result["query"] == "Progress"


def test_cached_translation_switches_confirm_every_change(tmp_path):
    with Terminal(tmp_path, menu_script()) as terminal:
        terminal.send_after(b"Esc=back", b"Progress\t")
        terminal.send_after(b"Switched to Dub.", b"\t")
        terminal.send_after(b"Switched to Sub.", b"\t")
        terminal.send_after(b"Switched to Dub.", b"\x1b")
        result = terminal.result()
        assert result["ttype"] == "dub"
        assert result["query"] == "Progress"


def test_cancelled_translation_stays_in_menu_with_filter(tmp_path):
    with Terminal(tmp_path, menu_script(cancel_switch=True)) as terminal:
        terminal.send_after(b"Esc=back", b"Progress\t")
        terminal.send_after(b"SWITCH-CANCELLED", b"\x1b")
        result = terminal.result()
        assert result["state"] == "SEARCH"
        assert result["ttype"] == "sub"
        assert result["query"] == "Progress"


def test_information_feedback_replaces_footer_but_back_still_works(tmp_path):
    with Terminal(tmp_path, menu_script()) as terminal:
        terminal.send_after(b"Esc=back", b"\x0f")
        feedback = terminal.until(b"INFO-SEEN")
        assert b"An unexpected error" not in feedback
        os.write(terminal.master, b"\x1b")
        assert terminal.result()["state"] == "SEARCH"


def test_plain_menu_uses_same_action_selection(tmp_path):
    with Terminal(tmp_path, menu_script(plain=True)) as terminal:
        terminal.send_after(b"Enter choice:", b"1\n")
        assert terminal.result()["state"] == "PLAY"


def test_playback_adjustments_keep_filter_and_cursor_at_edges(tmp_path):
    script = menu_script().replace("episode=ms.current_ep", "episode=ms.current_ep, buffer=ms.auto_delete_buffer")
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Esc=back", b"Playback\r")
        terminal.send_after(b"Auto-Delete Buffer: 1 ep", b"Buffer\x1b[H\x1b[D")
        terminal.send_after(b"Auto-Delete Buffer: 0", b"\x1b[C\x1b[F\x1b[C")
        terminal.send_after(b"Auto-Delete Buffer: 1", b"\x1b")
        result = terminal.result()
        assert result["state"] == "DETAILS"
        assert result["buffer"] == 1
        assert result["query"] == "Buffer"


def test_playback_returns_from_skip_settings_to_same_filtered_row(tmp_path):
    with Terminal(tmp_path, menu_script()) as terminal:
        terminal.send_after(b"Esc=back", b"Playback\r")
        terminal.send_after(b"Auto-Delete Buffer", b"Skip Settings\r")
        terminal.send_after(b"AniSkip:", b"\x1b")
        terminal.send_after(b"Playback", b"\x1b")
        result = terminal.result()
        assert result["state"] == "DETAILS"
        assert result["query"] == "Skip Settings"


def test_skip_toggle_arrows_preserve_filter_and_reopened_position(tmp_path):
    script = menu_script().replace(
        "flags = CliFlags", "args = SimpleNamespace(aniskip=True)\nflags = CliFlags",
    ).replace("SimpleNamespace(), 'sub'", "args, 'sub'").replace(
        "episode=ms.current_ep", "episode=ms.current_ep, aniskip=args.aniskip",
    )
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b"Esc=back", b"Playback\r")
        terminal.send_after(b"Auto-Delete Buffer", b"Skip Settings\r")
        terminal.send_after(b"AniSkip: ON", b"AniSkip\x1b[H\x1b[D")
        terminal.send_after(b"AniSkip: OFF", b"\x1b[F\x1b[C")
        terminal.send_after(b"AniSkip: ON", b"\x1b")
        terminal.send_after(b"Playback", b"\r")
        terminal.send_after(b"global/configured AniSkip lookup", b"\x1b")
        terminal.send_after(b"Playback", b"\x1b")
        result = terminal.result()
        assert result["state"] == "DETAILS"
        assert result["aniskip"] is True
        assert result["query"] == "Skip Settings"


@pytest.mark.parametrize('playback_return', [False, True])
def test_only_playback_return_focuses_continue_instead_of_saved_next(tmp_path, playback_return):
    script = menu_script().replace(
        'app_core.get_resume_time = lambda *a: 0',
        'app_core.get_resume_time = lambda *a: 90',
    ).replace(
        'state = handle_action_menu_state(',
        "from media_cli.ui.view_position_memory import SavedViewPosition\n"
        "ui._view_positions[('details:example:one',)] = SavedViewPosition('action:NEXT', 'Next', 0, 4, None)\n"
        f"ui.details_focus_primary = {playback_return!r}\n"
        'state = handle_action_menu_state(',
    ).replace(
        'finish(dict(',
        'assert ui.details_focus_primary is False\nfinish(dict(',
    )
    with Terminal(tmp_path, script) as terminal:
        terminal.send_after(b'Esc=back', b'\r')
        result = terminal.result()
        assert result['state'] == 'PLAY'
        assert result['episode'] == ('1' if playback_return else '2')
        assert result['query'] == ('' if playback_return else 'Next')
