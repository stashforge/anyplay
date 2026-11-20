"""Optional systemd user scheduling; imported only by the service command."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

UNIT = "anyplay-history"


def unit_contents(python=None):
    executable = str(python or sys.executable)
    # systemd quoting is not shell quoting; escape specifiers and C escapes.
    executable = executable.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    return {
        f"{UNIT}.service": (
            "[Unit]\nDescription=Anyplay scheduled History refresh\n\n"
            "[Service]\nType=oneshot\n"
            f'ExecStart="{executable}" -m media_cli service run\n'
            "Environment=ANYPLAY_BROWSER_ACCESS=off\n"
            "Environment=ANYPLAY_BROWSER_COOKIES=off\nTimeoutStartSec=15min\n"),
        f"{UNIT}.timer": (
            "[Unit]\nDescription=Anyplay History refresh schedule\n\n"
            "[Timer]\nOnStartupSec=2min\nOnUnitInactiveSec=5min\nAccuracySec=1min\n\n"
            "[Install]\nWantedBy=timers.target\n"),
    }


def run_history_check(*, manual=False):
    """Use the same per-title cooldown as the CLI, without interactive access."""
    import fcntl
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from ..core import storage
    from ..domain.history import history_entry_should_auto_refresh, refresh_history_entry_provider_catalog, history_entry_category
    from ..state.paths import STATE_DIR

    os.environ['ANYPLAY_BROWSER_ACCESS'] = 'off'
    os.environ['ANYPLAY_BROWSER_COOKIES'] = 'off'
    config = storage.load_config()
    root = Path(STATE_DIR)
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'scheduled-history.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if manual:
                print("A scheduled check is already running; retry after it finishes.")
            return 0
        from ..services.history_notifications import (
            load_state, save_state, observe_release, check_anilist, notify, deliver_releases,
        )
        state = load_state(root)
        attempted = delivered_count = 0
        def deliver(title, body):
            nonlocal attempted, delivered_count
            attempted += 1
            # Persist pending release events before attempting desktop delivery.
            save_state(root, state)
            success = notify(title, body)
            delivered_count += int(success)
            return success

        history = storage.load_history()
        for entry in history:
            if history_entry_category(entry) in ("Active", "Up to date"):
                before_event = entry.get("show", {}).get("_release_notification")
                observe_release(state, entry, send=deliver, defer=True, recover=manual)
                if entry.get("show", {}).get("_release_notification") != before_event:
                    storage.patch_history_entry_state(entry)
        for entry in history:
            if not (history_entry_should_auto_refresh(entry) or (manual and history_entry_category(entry) in ("Active", "Up to date"))):
                continue
            if refresh_history_entry_provider_catalog(entry):
                storage.patch_history_entry_state(entry)
                observe_release(state, entry, send=deliver, defer=True)
        try:
            if manual:
                state.pop("anilist_attempted_at", None)
            check_anilist(state, config, history, send=deliver, defer=True)
            deliver_releases(state, send=deliver)
        finally:
            save_state(root, state)
        if manual:
            pending = len(state.get("pending_releases", {}))
            print(f"Check complete: {delivered_count} notification(s) accepted by desktop; {pending} release(s) pending retry." if attempted or pending else "Check complete: no unnotified releases found; no notification sent.")
            return 1 if pending else 0
    return 0


def handle_service_command(action):
    if sys.platform != 'linux' or not shutil.which('systemctl'):
        print('Scheduled History refresh requires Linux with systemd user services.', file=sys.stderr)
        return 1
    if action in ('run', 'check'):
        try:
            return run_history_check(manual=True) if action == "check" else run_history_check()
        finally:
            # Imported provider helpers can register UI cleanup. This worker
            # never owns a terminal, including when a refresh raises.
            display = sys.modules.get('media_cli.ui.display')
            if display is not None:
                import atexit
                atexit.unregister(display.restore_terminal)
    directory = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user'
    try:
        if action == 'install':
            directory.mkdir(parents=True, exist_ok=True)
            for name, contents in unit_contents().items():
                destination = directory / name
                temporary = destination.with_suffix(destination.suffix + '.tmp')
                temporary.write_text(contents)
                temporary.replace(destination)
            result = subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
            if result.returncode:
                return result.returncode
            print('History service installed. Run anyplay service start to enable it.')
            return 0
        commands = {
            'start': ['enable', '--now', f'{UNIT}.timer'],
            'restart': ['restart', f'{UNIT}.timer'],
            'stop': ['disable', '--now', f'{UNIT}.timer', f'{UNIT}.service'],
            'status': ['status', '--no-pager', f'{UNIT}.timer', f'{UNIT}.service'],
        }
        if action == 'restart':
            result = subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
            if result.returncode:
                return result.returncode
        return subprocess.run(['systemctl', '--user', *commands[action]], check=False).returncode
    except OSError as error:
        print(f'Could not manage History service: {error}', file=sys.stderr)
        return 1
