"""User-facing setup and read-only status for the Android companion."""

import getpass
import sys
import warnings

from ..constants import APP_NAME
from ..core.processes import is_termux


def handle_companion_command(action: str) -> int:
    if action not in {"pair", "status"}:
        raise ValueError("Unknown companion action")
    if not is_termux():
        print("Android companion commands are available in Termux only.", file=sys.stderr)
        return 1

    from ..services.android_webview.client import CompanionUnavailable, ProbeClient, ProbeError, open_companion

    client = ProbeClient()
    try:
        paired = client.has_pairing()
    except ProbeError:
        print("Saved pairing could not be read safely. Existing data was not changed.", file=sys.stderr)
        return 1

    if action == "status":
        if not paired:
            print(f"Companion is not paired. Run '{APP_NAME} companion pair' to connect.")
            return 1
        try:
            client.health(timeout=2)
        except CompanionUnavailable:
            print("Pairing: saved\nService: stopped (starts automatically when needed)")
            return 0
        except ProbeError:
            print("Companion connection could not be verified. Pairing was not changed.", file=sys.stderr)
            return 1
        print("Pairing: verified\nService: running")
        return 0

    if paired:
        print(f"Companion is already paired. Run '{APP_NAME} companion status' to check the connection.")
        return 0
    if not sys.stdin.isatty():
        print("Pairing requires an interactive terminal for hidden code entry.", file=sys.stderr)
        return 1
    try:
        if not open_companion():
            raise ProbeError("Open Anyplay WebView manually, then retry pairing")
        print("Tap Pair in Anyplay WebView, then copy its code and return here.", file=sys.stderr)
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            code = getpass.getpass("Pairing code (hidden): ").strip()
        client.pair(code)
    except (KeyboardInterrupt, EOFError):
        print("Pairing cancelled.", file=sys.stderr)
        return 130
    except getpass.GetPassWarning:
        print("Cannot read a hidden pairing code in this terminal. Pairing was not changed.", file=sys.stderr)
        return 1
    except ProbeError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("Companion paired successfully.")
    return 0
