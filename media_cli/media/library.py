"""Local download library discovery."""

import os
import re

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".m4v"}


def natural_key(value):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", str(value))
    ]


def scan_download_library(download_dir):
    base = os.path.expanduser(str(download_dir or "").strip())
    if not base or not os.path.isdir(base):
        return base, []

    groups = {}
    for root, _, files in os.walk(base):
        relative_root = os.path.relpath(root, base)
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTENSIONS:
                continue
            path = os.path.join(root, name)
            group_name = (
                "Downloads"
                if relative_root == "."
                else relative_root.split(os.sep, 1)[0]
            )
            groups.setdefault(group_name, []).append(path)

    library = [
        {
            "name": group_name,
            "files": sorted(
                paths,
                key=lambda path: natural_key(os.path.basename(path)),
            ),
        }
        for group_name, paths in groups.items()
    ]
    return base, sorted(library, key=lambda group: natural_key(group["name"]))
