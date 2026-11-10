"""Playback-owned pickers retain the running mpv entry and terminal ownership."""

import json
import math
import time


def run_playback_menu(player, labels, title, *, picker, clock=time.monotonic):
    last_input = clock()
    ended = False
    incoming = player._ipc_buffer
    player._ipc_buffer = ""
    replay = []

    def activity(key, index):
        nonlocal last_input
        last_input = clock()
        return False

    def should_exit():
        nonlocal ended, incoming
        if player.process is None or player.process.poll() is not None:
            ended = True
            player.props["_menu_end_reason"] = "quit"
        if player.client is not None:
            try:
                chunk = player.client.recv(65536)
                if not chunk:
                    ended = True
                incoming += chunk.decode("utf-8", errors="replace")
            except BlockingIOError:
                pass
            except OSError:
                ended = True
        while "\n" in incoming:
            line, incoming = incoming.split("\n", 1)
            replay.append(line + "\n")
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("event") in ("end-file", "shutdown"):
                ended = True
                player.props["_menu_end_reason"] = message.get("reason", "quit")
            if message.get("event") == "property-change":
                player.props[message.get("name")] = message.get("data")
        return ended or clock() - last_input >= 30

    def header(index):
        remaining = max(0, math.ceil(30 - (clock() - last_input)))
        return f"Returning to player in {remaining}s • Esc=player" if remaining <= 10 else "Esc=player"

    from ..ui.picker import invalidate_screen_buffer
    from ..ui import terminal_images
    from ..ui.terminal_output import write_terminal

    invalidate_screen_buffer()
    write_terminal(terminal_images.clear_if_active() + "\033[H\033[2J")
    player.send_cmd("show-text", "Choose in terminal", 3000)
    try:
        index = picker(prompt=title, options=labels, header_fn=header, key_fn=activity,
                       exit_fn=should_exit, reverse_items=True, initial_selected=0,
                       live_fn=lambda query: (labels, header(0), False))
        should_exit()
    finally:
        player._ipc_buffer = "".join(replay) + incoming + player._ipc_buffer
        from ..ui.player_screen import _player_ui_state

        _player_ui_state["_cached_w"] = 0
        _player_ui_state["_row_state"] = None
        _player_ui_state["_last_poster_key"] = None
        _player_ui_state["_cleared_terminal_image"] = False
    if ended:
        return "ENDED", None
    return ("SELECT", index) if index >= 0 else ("RETURN", None)


def playback_picker(picker, lifecycle, *args, **kwargs):
    """Add playback exit/activity hooks to an existing picker invocation."""
    original_key = kwargs.get("key_fn")
    original_exit = kwargs.get("exit_fn")
    original_header = kwargs.get("header_fn")
    from ..core.terminal import truncate_display

    def key(value, index):
        lifecycle["key_fn"](value, index)
        return original_key(value, index) if original_key else False

    def header(index):
        text = original_header(index) if original_header else kwargs.get("header", "")
        countdown = lifecycle["header_fn"](index)
        lines = str(text or "").splitlines()
        if lines:
            import shutil

            from ..ui.footer import MUTED, RESET

            footer = lines[-1].replace("Esc=back", "Esc=player")
            prefix = countdown + " • " if countdown.startswith("Returning") else ""
            lines[-1] = MUTED + truncate_display(prefix + footer, shutil.get_terminal_size().columns - 1) + RESET
            return "\n".join(lines)
        return countdown

    return picker(*args, **{
        **kwargs, "key_fn": key, "header_fn": header,
        "exit_fn": lambda: lifecycle["exit_fn"]() or bool(original_exit and original_exit()),
    })


def run_playback_view(player, view, flags, ui, *, selected_id=None):
    """Render the original Details submenu with a playback-owned return target."""
    from ..ui.view_picker import run_view_session
    from ..ui.view_session import ViewSession, ExitSession
    from ..ui.picker import tui_pick
    from .. import app_core

    def choose(event):
        if event.kind == "select":
            item = session.item(event.item_id)
            if item is not None:
                return ExitSession(item.reference)
        if event.kind in ("back", "quit"):
            return ExitSession(-2)
        return None

    session = ViewSession(view, choose)
    if selected_id is not None:
        session.position.selected_id = selected_id

    def picker(**lifecycle):
        app_core.enter_alt_screen()

        def leave():
            if lifecycle["exit_fn"]():
                session.apply(ExitSession(-2))
                return True
            return False

        guarded = {**lifecycle, "exit_fn": leave}
        result = run_view_session(
            flags, ui, session, restore_position=False,
            picker=lambda *args, **kwargs: playback_picker(tui_pick, guarded, *args, **kwargs),
        )
        return result if isinstance(result, int) else -2

    return run_playback_menu(player, [], view.prompt, picker=picker)
