"""Launch locally downloaded videos."""

import os
import shutil
import subprocess
import urllib.request

from .android import PLAYERS


def play_local_video(path, player, *, termux, error):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        error("Downloaded file no longer exists.")
        return False

    title = os.path.basename(path)
    if termux:
        chosen = player if player in PLAYERS else "mpv"
        package, activity = PLAYERS.get(chosen, PLAYERS["mpv"])
        file_uri = "file://" + urllib.request.pathname2url(path)
        command = [
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            file_uri,
            "-t",
            "video/*",
            "-n",
            f"{package}/{activity}",
            "--es",
            "title",
            title,
        ]
    else:
        chosen = "vlc" if player == "vlc" else "mpv"
        if not shutil.which(chosen):
            error(f"{chosen} is not installed.")
            return False
        command = [chosen, path]

    try:
        subprocess.run(command, check=False)
        return True
    except Exception as exc:
        error(f"Could not open downloaded file: {exc}")
        return False
