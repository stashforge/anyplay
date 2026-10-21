"""Saved Downloads rows paint before detached file validation completes."""

from copy import deepcopy
import sys
import re
from threading import Event, get_ident
from types import SimpleNamespace

import pytest

from media_cli.app import downloads
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.transition import call_on_ui_thread, run_preparation
from test_ui_view_terminal import Terminal


@pytest.mark.parametrize("through_preparation", [False, True])
@pytest.mark.parametrize("outcome", ["complete", "cancel", "failure", "changed"])
@pytest.mark.parametrize("cached", [False, True])
def test_cached_rows_are_visible_and_actions_wait_for_validation(monkeypatch, tmp_path, through_preparation, outcome, cached):
    owner = get_ident()
    started, release, finished = Event(), Event(), Event()
    stored = {"shows": {"Example": {"episodes": ["1", "2"], "metadata": {"name": "Example"}}}}
    if not cached:
        stored["shows"] = {}
    writes = []
    monkeypatch.setattr(downloads, "load_downloads_db", lambda: deepcopy(stored))
    monkeypatch.setattr(downloads, "save_downloads_db", lambda data: writes.append((get_ident(), deepcopy(data))))
    monkeypatch.setattr(downloads, "make_info_fn", lambda *a: None)
    monkeypatch.setattr(downloads, "build_info_panel", lambda *a, **kw: None)
    monkeypatch.setattr(downloads, "_poster_footer_line", lambda show, line, width: str(line))
    monkeypatch.setattr(downloads.app_core, "get_local_progress", lambda *a: None)
    monkeypatch.setattr(downloads.app_core, "get_local_episode_label", lambda *a: None)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)

    def scan(db, directory, ttype, *, persist=True):
        assert not persist and get_ident() != owner
        started.set()
        try:
            assert release.wait(2)
            if outcome == "failure":
                raise RuntimeError("Cannot read directory")
            db["shows"].setdefault("Example", {"metadata": {"name": "Example"}})["episodes"] = ["2"]
            return db, list(db["shows"].items()), True
        finally:
            finished.set()

    monkeypatch.setattr(downloads, "_scan_downloads", scan, raising=False)

    def pick(flags, ui, prompt, options, **hooks):
        assert get_ident() == owner and started.wait(1)
        assert options == (["Example  (2)"] if cached else [])
        assert not finished.is_set()
        assert hooks["select_fn"](0) is False
        assert hooks["can_delete_fn"](0) is False
        if cached:
            assert hooks["item_key_fn"](0) == "Example"
        hooks["delete_fn"](0)
        assert not writes
        if outcome == "cancel":
            return -2
        if outcome == "changed":
            stored["shows"].setdefault("Example", {"metadata": {}})["watched_episodes"] = ["2"]
        release.set()
        assert finished.wait(1)
        hooks["tick_fn"]()
        rows, _, done = hooks["live_fn"]("")
        if outcome == "failure":
            assert rows == (["Example  (2)"] if cached else []) and done
            assert not hooks["select_fn"](0) and not hooks["can_delete_fn"](0)
            assert "Couldn't check downloads" in hooks["header_fn"](0)
            assert not writes
            return -2
        assert rows == ["Example  (1)"] and done
        assert hooks["select_fn"](0) is True
        assert hooks["can_delete_fn"](0) is True
        if outcome == "changed":
            assert not writes and stored["shows"]["Example"]["watched_episodes"] == ["2"]
        else:
            assert writes and all(thread == owner for thread, _ in writes)
        return -2

    monkeypatch.setattr(downloads, "tui_pick", lambda *a, **kw: call_on_ui_thread(lambda: pick(*a, **kw)))
    def enter():
        return downloads.handle_downloads_state(CliFlags(), UiState(), MachineState(),
                                                {"download_dir": str(tmp_path)}, SimpleNamespace(), "sub", None)

    try:
        result = run_preparation(enter, tick=lambda: None) if through_preparation else enter()
        assert result == "QUIT"
    finally:
        release.set()
        assert finished.wait(1)
    if outcome == "cancel":
        assert not writes


def test_real_terminal_keeps_browsed_title_after_validation(tmp_path):
    gate = tmp_path / "scan-ready"
    script = f"""
from copy import deepcopy
from pathlib import Path
from media_cli.app import downloads
from media_cli.context import CliFlags, MachineState, UiState
from media_cli.ui.transition import run_preparation
from media_cli.ui.footer import Footer, render_footer
from media_cli.ui.terminal_runtime import TerminalRuntime
stored = {{'shows': {{'First': {{'episodes': ['1'], 'metadata': {{'name': 'First'}}}},
                      'Second': {{'episodes': ['1'], 'metadata': {{'name': 'Second'}}}}}}}}
downloads.load_downloads_db = lambda: deepcopy(stored)
downloads.save_downloads_db = lambda data: None
downloads.make_info_fn = lambda *a: None
downloads._get_poster = lambda *a: ''
downloads.build_info_panel = lambda show, tt, width, parts, **kw: parts.extend([show['name'], 'Secondary', 'Metadata'])
downloads._poster_footer_line = lambda show, line, width: render_footer(line, width) if isinstance(line, Footer) else line
downloads.app_core.get_local_progress = lambda *a: None
downloads.app_core.get_local_episode_label = lambda *a: None
downloads.app_core.is_confirmed_offline = lambda: False
def scan(db, *args, **kwargs):
    while not Path({str(gate)!r}).exists():
        time.sleep(.01)
    db['shows']['Second']['episodes'] = ['1', '2']
    return db, list(reversed(list(db['shows'].items()))), True
downloads._scan_downloads = scan
with TerminalRuntime():
    result = run_preparation(lambda: downloads.handle_downloads_state(
        CliFlags(show_image=False), UiState(), MachineState(), {{'download_dir': {str(tmp_path)!r}}},
        SimpleNamespace(), 'sub', None), tick=lambda: None)
finish(result)
"""
    with Terminal(tmp_path, script, rows=12) as terminal:
        terminal.until(b"Checking downloaded files...")
        assert b"First" in terminal.output and b"Second" in terminal.output
        terminal.send_after(b"\x1b[12;100H", b"\x1b[A")
        terminal.until(b"\x1b[9;1H")
        terminal.until(b"\x1b[12;100H")
        gate.write_text("ready")
        terminal.until(b"(2)")
        terminal.until(b"\x1b[12;100H")
        # Bottom-up navigation selected Second before the worker reordered rows.
        headers = re.findall(rb"\x1b\[9;1H\x1b\[2K(?:\x1b\[2K)?([^\x1b\r\n]*)", terminal.output)
        assert headers[-1] == b"Second"
        import os
        os.write(terminal.master, b"\x1b")
        assert terminal.result() == "QUIT"
