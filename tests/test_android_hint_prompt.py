import os
import pty
import termios
import threading
import tty
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from media_cli.app import playback_player
from media_cli.context import FLAGS, CliFlags, MachineState, UiState
from media_cli.ui import player_screen
from media_cli.ui.terminal_runtime import TerminalRuntime, current_runtime
from media_cli.ui.transition import PreparationCancelled


@pytest.mark.parametrize("raw_initial", [False, True])
@pytest.mark.parametrize("failure", [None, KeyboardInterrupt, ValueError])
def test_prompt_handoff_uses_line_mode_and_restores_owner(monkeypatch, raw_initial, failure):
    master, slave = pty.openpty()
    if raw_initial:
        tty.setraw(slave)
    original = termios.tcgetattr(slave)
    monkeypatch.setattr(FLAGS, "plain_mode", False)
    monkeypatch.setattr(player_screen, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True),
    ))
    monkeypatch.setattr(player_screen, "_loading_cancel_event", threading.Event())
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    monkeypatch.setattr(player_screen, "_player_ui_state", {})

    def prompt():
        with player_screen.prompt_input_scope():
            assert current_runtime() is None
            attrs = termios.tcgetattr(slave)
            assert attrs[3] & termios.ECHO
            assert attrs[3] & termios.ICANON
            assert attrs[3] & termios.ISIG
            assert attrs[0] & termios.ICRNL
            assert attrs[1] & termios.OPOST
            if failure:
                raise failure

    try:
        with TerminalRuntime(fd=slave) as runtime:
            if failure:
                with pytest.raises(failure):
                    prompt()
            else:
                prompt()
            assert current_runtime() is runtime
            assert not termios.tcgetattr(slave)[3] & (termios.ECHO | termios.ICANON)
        assert termios.tcgetattr(slave) == original
    finally:
        os.close(slave)
        os.close(master)


@pytest.mark.parametrize("answer,expected", [(" y ", "y"), ("NO", "no"), ("", ""), (EOFError, "s")])
def test_android_hint_reads_choice_only_inside_prompt_scope(monkeypatch, capsys, answer, expected):
    calls = []

    @contextmanager
    def scope():
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("leave")

    def read(prompt=''):
        assert calls == ["enter"]
        assert "Don't show again" in capsys.readouterr().out
        if answer is EOFError:
            raise EOFError
        return answer

    monkeypatch.setattr(player_screen, "prompt_input_scope", scope)
    monkeypatch.setattr("builtins.input", read)
    assert playback_player._android_skip_hint_choice(SimpleNamespace(plain_mode=True)) == expected
    assert calls == ["enter", "leave"]


def test_android_hint_ctrl_c_is_not_treated_as_skip_and_cursor_is_restored(monkeypatch):
    from contextlib import nullcontext

    output = []
    monkeypatch.setattr('media_cli.ui.terminal_output.write_terminal', output.append)
    monkeypatch.setattr(player_screen, "prompt_input_scope", nullcontext)
    monkeypatch.setattr(playback_player, "sys", SimpleNamespace(
        stdin=SimpleNamespace(isatty=lambda: True),
        stdout=SimpleNamespace(write=output.append, flush=lambda: None),
    ))

    def interrupt(prompt=''):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    with pytest.raises(KeyboardInterrupt):
        playback_player._android_skip_hint_choice(SimpleNamespace(plain_mode=False))
    assert output[-1] == "\033[2J\033[H\033[?25l"


def test_prompt_refuses_pending_cancel_before_reading(monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(player_screen, "_loading_cancel_event", cancelled)
    monkeypatch.setattr(player_screen, "_loading_quit_event", threading.Event())
    with pytest.raises(PreparationCancelled):
        with player_screen.prompt_input_scope():
            pytest.fail("Cancelled handoff entered prompt")


@pytest.mark.parametrize("failure", [PreparationCancelled, KeyboardInterrupt])
@pytest.mark.parametrize("stage", ["hint", "countdown"])
def test_cancelled_android_hint_does_not_continue_to_countdown_or_player(monkeypatch, failure, stage):
    core = playback_player.app_core
    show = {"_id": "one", "_provider": "torrent", "name": "Example"}
    ui = UiState(ui_show_ctx=show)
    ms = MachineState(current_ep="1", selected_stream={"link": "file", "source_name": "Local File"},
                      ep_cache_key=("one", "1", "sub", "torrent"), ep_cache_data={"episode": {"sourceUrls": []}})
    calls = []

    def cancel(*args, **kwargs):
        raise failure

    monkeypatch.setattr(player_screen, "_player_ui_state", {})
    monkeypatch.setattr(player_screen, "reset_loading_cancel", lambda: None)
    monkeypatch.setattr(player_screen, "loading_cancel_requested", lambda: False)
    monkeypatch.setattr(player_screen, "render", lambda **kw: None)
    monkeypatch.setattr(core, "with_loading", lambda msg, work: work())
    monkeypatch.setattr(core, "prepare_show_display_state", lambda *a: None)
    monkeypatch.setattr(core, "ensure_episode_ids", lambda *a: ["1"])
    monkeypatch.setattr(core, "get_show_anilist_id", lambda *a: None)
    monkeypatch.setattr(core, "get_show_mal_id", lambda *a, **kw: None)
    monkeypatch.setattr(core, "get_custom_skips", lambda *a: {})
    monkeypatch.setattr(core, "enter_alt_screen", lambda: None)
    monkeypatch.setattr(core, "_ipc_player", None)
    monkeypatch.setattr(core, "_exit_player_screen", lambda *a, **kw: calls.append("exit"))
    monkeypatch.setattr(core, "set_action_feedback", lambda *a: calls.append("feedback"))
    monkeypatch.setattr(core, "is_termux", lambda: True)
    monkeypatch.setattr(core, "play_android", lambda *a, **kw: pytest.fail("Cancelled hint launched playback"))
    monkeypatch.setattr(playback_player, "_android_skip_hint_choice", cancel if stage == "hint" else lambda *a: "s")
    if stage == "countdown":
        monkeypatch.setattr(playback_player, "_wait_for_android_start", cancel)
    monkeypatch.setattr(playback_player.time, "sleep", lambda *a: pytest.fail("Cancelled hint reached countdown"))
    args = SimpleNamespace(aniskip=True, download=False, print_url=False, sources=False, binge=False)

    def handle():
        return playback_player.handle_play_state(
            CliFlags(), ui, ms, {}, args, "sub", "best", lambda *a: False,
        )

    if failure is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            handle()
    else:
        assert handle() == "DETAILS"
        assert calls == ["exit", "feedback"]
    assert ui.aniskip_android_hint_shown is (stage == "countdown")
