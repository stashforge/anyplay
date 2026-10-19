import os
import pty
import select
import termios
import tty
from functools import partial
from types import SimpleNamespace

import pytest

from media_cli.cli import main
from media_cli.ui import terminal_runtime


@pytest.mark.parametrize("interrupted", [False, True])
def test_cli_keeps_input_silent_through_cleanup_and_discards_exit_keys(monkeypatch, interrupted):
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    events = []
    real_runtime = terminal_runtime.TerminalRuntime
    monkeypatch.setattr(terminal_runtime, "TerminalRuntime", partial(real_runtime, fd=slave))

    @terminal_runtime.terminal_application
    def app_main():
        with terminal_runtime.borrow_terminal():
            events.append("ui")

    def cleanup():
        attrs = termios.tcgetattr(slave)
        assert not attrs[3] & termios.ECHO, "Echo was restored before cleanup completed"
        assert attrs[3] & termios.ISIG, "Ctrl+C must remain a signal during shutdown"
        os.write(master, b"\x1b\x1b")
        assert not select.select([master], [], [], 0.05)[0]
        events.append("cleanup")
        if interrupted:
            raise KeyboardInterrupt

    def restore():
        events.append("restore")
        termios.tcsetattr(slave, termios.TCSAFLUSH, original)

    monkeypatch.setenv("ALLMANGA_NO_OS_EXIT", "1")
    monkeypatch.setattr(main.sys, "argv", ["anyplay", "--incognito"])
    monkeypatch.setattr(main.app, "app_main", app_main)
    monkeypatch.setattr(main.app, "restore_terminal", restore)
    monkeypatch.setattr(main.app, "flush_anilist_writes", cleanup)
    monkeypatch.setattr(main.app, "_ipc_player", SimpleNamespace(quit=lambda: None))
    monkeypatch.setattr(main.app, "kill_active_subprocesses", lambda: None)
    monkeypatch.setattr("media_cli.services.catalog._catalog_executor.shutdown", lambda **kw: None)
    monkeypatch.setattr("media_cli.media.local_proxy.cleanup_active_local_proxy", lambda: None)
    monkeypatch.setattr("media_cli.core.storage.cleanup_incognito_cache", lambda: None)
    monkeypatch.setattr("media_cli.media.download_worker.stop_global_worker", lambda: None)
    try:
        if interrupted:
            with pytest.raises(KeyboardInterrupt):
                main.run()
        else:
            assert main.run() == 0
        assert events == ["ui", "cleanup", "restore"]
        assert termios.tcgetattr(slave) == original
        tty.setraw(slave)
        assert not select.select([slave], [], [], 0)[0], "Exit keys leaked back to the shell"
    finally:
        termios.tcsetattr(slave, termios.TCSANOW, original)
        os.close(slave)
        os.close(master)
