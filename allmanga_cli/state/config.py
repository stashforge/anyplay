"""Configuration defaults and secure JSON persistence."""

import json
import os
import tempfile
import time

DEFAULT_CONFIG = {
    "quality": "1080p",
    "translation_type": "sub",
    "binge": False,
    "player": "mpv",
    "anilist_token": "",
    "sync": False,
    "cover": False,
    "download_dir": "",
    "anilist_sort": "recent",
    "anilist_sort_reverse": False,
    "spinner": "braille",
    "provider": "miruro",
    "title_language": "romaji",
    "aniskip": True,
    "auto_skip": True,
    "auto_download_next": False,
    "auto_delete_watched": False,
    "auto_delete_buffer": 1,
}

LEGACY_CONFIG_KEY_MAP = {
    "auto_track": "sync",
    "aniskip_enabled": "aniskip",
    "aniskip_auto": "auto_skip",
    "default_provider": "provider",
    "episode_order": "order",
}


def migrate_config_keys(config):
    """Migrate legacy configuration keys to canonical names in-place."""
    if not isinstance(config, dict):
        return False
    changed = False
    for old_key, new_key in LEGACY_CONFIG_KEY_MAP.items():
        if old_key in config:
            if new_key not in config:
                config[new_key] = config[old_key]
            del config[old_key]
            changed = True
    if "allanime_frontend_domain" in config:
        del config["allanime_frontend_domain"]
        changed = True
    return changed



def parse_conf_text(text: str) -> dict:
    """Parse key = value configuration text with support for comments and types."""
    config = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
        elif ":" in line:
            key, val = line.split(":", 1)
        else:
            continue
        key = key.strip()
        val = val.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val_unquoted = val[1:-1]
        else:
            val_unquoted = val
        val_lower = val_unquoted.lower()
        if val_lower in ("true", "yes", "on"):
            config[key] = True
        elif val_lower in ("false", "no", "off"):
            config[key] = False
        else:
            try:
                config[key] = int(val_unquoted)
            except ValueError:
                try:
                    config[key] = float(val_unquoted)
                except ValueError:
                    config[key] = val_unquoted
    return config


def serialize_conf(config: dict) -> str:
    """Serialize configuration dict into clean .conf INI-style text with comments."""
    lines = [
        "# AllManga-CLI Configuration",
        "# Options can be edited manually. Lines starting with '#' are comments.",
        "",
        "# Playback & Video Quality",
        f"quality = {config.get('quality', '1080p')}",
        f"translation_type = {config.get('translation_type', 'sub')}",
        f"player = {config.get('player', 'mpv')}",
        f"provider = {config.get('provider', 'miruro')}",
        f"title_language = {config.get('title_language', 'romaji')}",
        "",
        "# Download Destination",
        f"download_dir = {config.get('download_dir', '')}",
        "",
        "# Automation & Binge",
        f"auto_download_next = {'true' if config.get('auto_download_next') else 'false'}",
        f"auto_delete_watched = {'true' if config.get('auto_delete_watched') else 'false'}",
        f"auto_delete_buffer = {config.get('auto_delete_buffer', 1)}",
        f"binge = {'true' if config.get('binge') else 'false'}",
        "",
        "# AniList & Scrobbling",
        f"sync = {'true' if config.get('sync') else 'false'}",
        f"anilist_token = {config.get('anilist_token', '')}",
        f"anilist_sort = {config.get('anilist_sort', 'recent')}",
        f"anilist_sort_reverse = {'true' if config.get('anilist_sort_reverse') else 'false'}",
        "",
        "# UI & Player Enhancements",
        f"cover = {'true' if str(config.get('cover', '')).lower() in ('true', '1') else 'false'}",
        f"spinner = {config.get('spinner', 'braille')}",
        f"aniskip = {'true' if config.get('aniskip', True) else 'false'}",
        f"auto_skip = {'true' if config.get('auto_skip', True) else 'false'}",
    ]
    known = {
        "quality", "translation_type", "player", "provider", "title_language",
        "download_dir", "auto_download_next", "auto_delete_watched", "auto_delete_buffer",
        "binge", "sync", "anilist_token", "anilist_sort", "anilist_sort_reverse",
        "cover", "spinner", "aniskip", "auto_skip"
    }
    extra_keys = [k for k in config.keys() if k not in known and not k.startswith("_")]
    if extra_keys:
        lines.append("")
        lines.append("# Custom Options")
        for k in extra_keys:
            v = config[k]
            if isinstance(v, bool):
                val_str = "true" if v else "false"
            else:
                val_str = str(v)
            lines.append(f"{k} = {val_str}")
    lines.append("")
    return "\n".join(lines)


def secure_permissions(path):
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def secure_directory(path):
    try:
        os.chmod(path, 0o700)
    except Exception:
        pass


def save_config_file(path, config, disabled=False):
    if disabled:
        return False
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    secure_directory(directory)
    suffix = ".tmp.json" if path.endswith(".json") else ".tmp.conf"
    fd, temp_path = tempfile.mkstemp(
        prefix=".config.",
        suffix=suffix,
        dir=directory,
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            if path.endswith(".json"):
                json.dump(config, handle, indent=4)
                handle.write("\n")
            else:
                handle.write(serialize_conf(config))
            handle.flush()
            os.fsync(handle.fileno())
        secure_permissions(temp_path)
        os.replace(temp_path, path)
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        try:
            os.remove(temp_path)
        except Exception:
            pass
        raise
    secure_permissions(path)
    return True


def load_config_file(
    path,
    *,
    disabled=False,
    on_error=None,
    on_invalid=None,
):
    import re
    import shutil

    defaults = dict(DEFAULT_CONFIG)
    directory = os.path.dirname(path)
    legacy_json_path = os.path.join(directory, "config.json")

    # 1. If path (.conf) exists, load it
    if os.path.exists(path):
        try:
            if not disabled:
                secure_permissions(path)
            with open(path, encoding="utf-8") as handle:
                raw_text = handle.read()
            if path.endswith(".json"):
                try:
                    config = json.loads(raw_text)
                except Exception:
                    cleaned = re.sub(r",\s*([}\]])", r"\1", raw_text)
                    config = json.loads(cleaned)
            else:
                config = parse_conf_text(raw_text)
            changed = migrate_config_keys(config)
            for key, value in defaults.items():
                if key not in config:
                    config[key] = value
                    changed = True
            if changed and not disabled:
                save_config_file(path, config, disabled=disabled)
            return config
        except Exception as exc:
            if on_error:
                on_error(exc)
            if on_invalid:
                on_invalid(path)
            return defaults

    # 2. If .conf does not exist, check if legacy config.json exists to migrate
    if not path.endswith(".json") and os.path.exists(legacy_json_path):
        try:
            if not disabled:
                secure_permissions(legacy_json_path)
            with open(legacy_json_path, encoding="utf-8") as handle:
                raw_text = handle.read()
            try:
                config = json.loads(raw_text)
            except Exception:
                cleaned = re.sub(r",\s*([}\]])", r"\1", raw_text)
                config = json.loads(cleaned)
            migrate_config_keys(config)
            for key, value in defaults.items():
                if key not in config:
                    config[key] = value
            if not disabled:
                save_config_file(path, config, disabled=disabled)
                # Keep backup of legacy config.json
                bak_path = legacy_json_path + ".bak"
                try:
                    shutil.copy2(legacy_json_path, bak_path)
                except Exception:
                    pass
            return config
        except Exception as exc:
            if on_error:
                on_error(exc)

    # 3. Neither exists: create new config file with defaults
    save_config_file(path, defaults, disabled=disabled)
    return defaults


def resolve_editor(editor_override: str | None = None) -> str | None:
    """Resolve the editor executable command, honoring override, environment, and system fallbacks."""
    import shutil
    import sys

    if editor_override and editor_override.strip():
        return editor_override.strip()

    visual = os.environ.get("VISUAL", "").strip()
    if visual:
        return visual

    editor = os.environ.get("EDITOR", "").strip()
    if editor:
        return editor

    for cand in ("nano", "micro", "vim", "vi"):
        if shutil.which(cand):
            return cand

    if sys.platform == "win32" and shutil.which("notepad"):
        return "notepad"

    return None


def open_config_editor(editor_override: str | None = None) -> int:
    """Ensure config exists and open it in the user's preferred or fallback editor."""
    import shlex
    import subprocess
    import sys
    from .paths import CONFIG_PATH

    # Ensure config file exists on disk
    if not os.path.exists(CONFIG_PATH):
        save_config_file(CONFIG_PATH, dict(DEFAULT_CONFIG))

    editor_cmd = resolve_editor(editor_override)
    if not editor_cmd:
        print("\033[91m[!] No suitable editor found.\033[0m", file=sys.stderr)
        print(f"Please specify an editor with -e <editor> or edit manually: {CONFIG_PATH}", file=sys.stderr)
        return 1

    cmd_parts = shlex.split(editor_cmd) + [CONFIG_PATH]
    try:
        res = subprocess.run(cmd_parts)
        if res.returncode == 0:
            print(f"Configuration file closed: {CONFIG_PATH}")
        return res.returncode
    except Exception as exc:
        print(f"\033[91m[!] Failed to launch editor '{editor_cmd}': {exc}\033[0m", file=sys.stderr)
        print(f"You can edit the file manually: {CONFIG_PATH}", file=sys.stderr)
        return 1

