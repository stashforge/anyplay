"""Configuration defaults and secure JSON persistence."""

from .theme_catalog import THEME_NAMES

import json
import os
import tempfile
import time

DEFAULT_CONFIG = {
    "history_refresh_minutes": 60,
    "quality": "1080p",
    "translation_type": "sub",
    "binge": False,
    "player": "mpv",
    "anilist_token": "",
    "sync": False,
    "cover": False,
    "details_cover": True,
    "download_dir": "",
    "anilist_sort": "recent",
    "anilist_sort_reverse": False,
    "spinner": "braille",
    "tui_layout": "bottom",
    "theme": "mint",
    "padding": "",
    "max_width": 120,
    "search_image": True,
    "search_image_path": "",
    "search_image_size": 15,
    "info_cover_shape": "portrait",
    "editor": "",
    "provider": "anipm",
    "title_language": "romaji",
    "aniskip": True,
    "auto_skip": True,
    "auto_download_next": "adaptive",
    "auto_delete_watched": False,
    "auto_delete_buffer": 1,
    "download_sub_langs": "multi",
    "download_audio_langs": "multi",
    "disabled_providers": "",
    "torrent_fallback": True,
    "torrent_search_limit": 50,
    "torrent_min_seeders": 2,
    "torrent_hydrate_top": 5,
    "torrent_prefer_size": "150MB:2500MB",
    "torrent_allow_unknown_seeders": True,
    "log_max_bytes": 1048576,
    "log_backup_count": 5,
}

# Defined lower bounds; zero remains valid where it means disabled/no minimum.
CONFIG_MINIMUMS = {
    'history_refresh_minutes': 30, 'search_image_size': 8,
    'auto_delete_buffer': 0, 'torrent_search_limit': 1,
    'torrent_min_seeders': 0, 'torrent_hydrate_top': 0,
    'log_max_bytes': 1, 'log_backup_count': 0, 'max_width': 0,
}
CONFIG_CHOICES = {
    'translation_type': ('sub', 'dub'),
    'anilist_sort': ('recent', 'anilist', 'title', 'progress'),
    'spinner': ('braille', 'dots', 'line', 'pulse'),
    'tui_layout': ('bottom', 'top'), 'theme': THEME_NAMES,
    'info_cover_shape': ('portrait', 'square'),
    'title_language': ('romaji', 'english', 'native'),
    'player': ('mpv', 'rex', 'mpvrx', 'mpvrex', 'vlc', 'next'),
    'auto_download_next': ('adaptive', 'off'),
}


def normalize_config(config):
    """Resolve known settings once; preserve extension keys and arbitrary paths."""
    import math
    result = dict(config) if isinstance(config, dict) else {}
    for key, default in DEFAULT_CONFIG.items():
        value = result.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            result[key] = default
            continue
        if isinstance(default, bool):
            if isinstance(value, bool):
                pass
            elif str(value).strip().casefold() in ('true', 'yes', 'on', '1'):
                value = True
            elif str(value).strip().casefold() in ('false', 'no', 'off', '0'):
                value = False
            else:
                value = default
        elif isinstance(default, int):
            try:
                number = float(value)
                if isinstance(value, bool) or not math.isfinite(number) or not number.is_integer():
                    raise ValueError
                value = max(CONFIG_MINIMUMS[key], int(number))
            except (TypeError, ValueError, OverflowError):
                value = default
        elif key == 'auto_download_next':
            aliases = {True: 'adaptive', False: 'off'}
            value = aliases[value] if isinstance(value, bool) else str(value).strip().casefold()
            value = {'true': 'adaptive', 'on': 'adaptive', 'yes': 'adaptive',
                     'false': 'off', 'no': 'off', 'none': 'off', 'disabled': 'off', '0': 'off'}.get(value, value)
            if value not in CONFIG_CHOICES[key]:
                value = default
        elif key == 'spinner' and isinstance(value, (list, tuple)):
            value = list(value) if value and all(isinstance(frame, str) and frame.strip() for frame in value) else default
        elif key in CONFIG_CHOICES:
            value = str(value).strip().casefold()
            if value not in CONFIG_CHOICES[key]:
                value = default
        elif key == 'provider':
            from ..provider_manifest import PROVIDER_REGISTRY
            value = str(value).strip().casefold()
            if not PROVIDER_REGISTRY.get(value) and value not in ('anilist', 'animethemes'):
                value = default
        elif key == 'quality':
            import re
            value = str(value).strip().casefold()
            if value not in ('best', 'worst', 'auto') and not re.fullmatch(r'[1-9]\d{1,3}p', value):
                value = default
        elif key == 'disabled_providers' and isinstance(value, (list, tuple)):
            value = ','.join(str(item).strip() for item in value if isinstance(item, str) and item.strip())
        elif key == 'torrent_prefer_size' and (value is False or value == 0):
            value = 'off'
        elif not isinstance(value, str):
            value = default
        result[key] = value
    return result


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
    from ..provider_manifest import RETIRED_PROVIDERS
    if str(config.get("provider", "")).casefold() in RETIRED_PROVIDERS:
        config["provider"] = "anipm"
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


def _is_truthy(val, default=False) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _auto_download_config_value(val) -> str:
    if isinstance(val, bool):
        return "adaptive" if val else "off"
    norm = str(val if val is not None else "adaptive").strip().lower()
    if norm in ("", "0", "false", "no", "off", "none", "disabled"):
        return "off"
    return "adaptive"


def serialize_conf(config: dict) -> str:
    """Serialize configuration dict into clean .conf INI-style text with comments."""
    config = normalize_config(config)
    lines = [
        "# Anyplay Configuration",
        "# Options can be edited manually. Lines starting with '#' are comments.",
        "",
        "# Playback & Video Quality",
        f"quality = {config.get('quality', '1080p')}",
        f"translation_type = {config.get('translation_type', 'sub')}",
        f"player = {config.get('player', 'mpv')}",
        f"provider = {config.get('provider', 'anipm')}",
        f"title_language = {config.get('title_language', 'romaji')}",
        "",
        "# History automatic refresh (shared with the optional timer; minimum 30 minutes)",
        f"history_refresh_minutes = {config.get('history_refresh_minutes', 60)}",
        "",
        "# Providers",
        "# Comma-separated provider IDs to hide/disable for this user, e.g. animegg,anidb",
        f"disabled_providers = {config.get('disabled_providers', '')}",
        "",
        "# Download Destination & Media Preferences",
        f"download_dir = {config.get('download_dir', '')}",
        f"download_sub_langs = {config.get('download_sub_langs', 'multi')}",
        f"download_audio_langs = {config.get('download_audio_langs', 'multi')}",
        "",
        "# Torrent Search & Fallback",
        f"torrent_fallback = {'true' if _is_truthy(config.get('torrent_fallback'), True) else 'false'}",
        f"torrent_search_limit = {config.get('torrent_search_limit', 50)}",
        f"torrent_min_seeders = {config.get('torrent_min_seeders', 2)}",
        f"torrent_hydrate_top = {config.get('torrent_hydrate_top', 5)}",
        f"torrent_prefer_size = {config.get('torrent_prefer_size', '150MB:2500MB')}",
        f"torrent_allow_unknown_seeders = {'true' if _is_truthy(config.get('torrent_allow_unknown_seeders'), True) else 'false'}",
        "",
        "# Automation & Binge",
        f"auto_download_next = {_auto_download_config_value(config.get('auto_download_next', 'adaptive'))}",
        f"auto_delete_watched = {'true' if _is_truthy(config.get('auto_delete_watched'), False) else 'false'}",
        f"auto_delete_buffer = {config.get('auto_delete_buffer', 1)}",
        f"binge = {'true' if _is_truthy(config.get('binge'), False) else 'false'}",
        "",
        "# AniList & Scrobbling",
        f"sync = {'true' if _is_truthy(config.get('sync'), False) else 'false'}",
        f"anilist_token = {config.get('anilist_token', '')}",
        f"anilist_sort = {config.get('anilist_sort', 'recent')}",
        f"anilist_sort_reverse = {'true' if _is_truthy(config.get('anilist_sort_reverse'), False) else 'false'}",
        "",
        "# UI & Player Enhancements",
        f"cover = {'true' if _is_truthy(config.get('cover'), False) else 'false'}",
        "# Show covers in title details/action menus when global covers are enabled.",
        f"details_cover = {'true' if _is_truthy(config.get('details_cover'), True) else 'false'}",
        f"spinner = {config.get('spinner', 'braille')}",
        "# Color preset: mint, lavender, ember, midnight or hearth. ANYPLAY_THEME overrides this for one launch.",
        f"theme = {config.get('theme', 'mint')}",
        "# Terminal cells: all | vertical horizontal | top horizontal bottom | top right bottom left.",
        "# Empty uses theme padding; 0 disables it. Small terminals reduce padding automatically.",
        f"padding = {config.get('padding', '')}",
        "# Center the TUI at this maximum column width (minimum 40); 0 uses all available width.",
        f"max_width = {config.get('max_width', 120)}",
        "# New list screens result placement: bottom or top.",
        f"tui_layout = {config.get('tui_layout', 'bottom')}",
        "# Optional local image above ANYPLAY; empty path uses the data-folder search-image.png.",
        f"search_image = {'true' if _is_truthy(config.get('search_image'), True) else 'false'}",
        f"search_image_path = {config.get('search_image_path', '')}",
        "# Width in terminal columns, clamped to 8–30 and available screen space.",
        f"search_image_size = {config.get('search_image_size', 15)}",
        "# Selected-title info cover: portrait or square (center crop).",
        f"info_cover_shape = {config.get('info_cover_shape', 'portrait')}",
        f"editor = {config.get('editor', '')}",
        f"aniskip = {'true' if _is_truthy(config.get('aniskip'), True) else 'false'}",
        f"auto_skip = {'true' if _is_truthy(config.get('auto_skip'), True) else 'false'}",
        "",
        "# Diagnostics",
        f"log_max_bytes = {config.get('log_max_bytes', 1048576)}",
        f"log_backup_count = {config.get('log_backup_count', 5)}",
    ]
    known = {
        "quality", "translation_type", "player", "provider", "title_language",
        "disabled_providers", "history_refresh_minutes",
        "download_dir", "download_sub_langs", "download_audio_langs", "subtitle_mode",
        "torrent_fallback", "torrent_search_limit", "torrent_min_seeders",
        "torrent_hydrate_top", "torrent_prefer_size", "torrent_allow_unknown_seeders",
        "auto_download_next", "auto_delete_watched", "auto_delete_buffer",
        "binge", "sync", "anilist_token", "anilist_sort", "anilist_sort_reverse",
        "cover", "details_cover", "spinner", "theme", "tui_layout", "editor", "aniskip", "auto_skip",
        "search_image", "search_image_path", "search_image_size",
        "info_cover_shape",
        "log_max_bytes", "log_backup_count",
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
            normalized = normalize_config(config)
            changed = changed or normalized != config
            config = normalized
            if changed and not disabled:
                if path.endswith(".json"):
                    save_config_file(path, config, disabled=disabled)
                else:
                    # Defaults are effective values, not explicit file entries.
                    from .config_editor import merge_edits, write_text
                    explicit = parse_conf_text(raw_text)
                    migrated = dict(explicit)
                    migrate_config_keys(migrated)
                    normalized_explicit = {key: config[key] for key in migrated}
                    def value_text(value):
                        return ('true' if value else 'false') if isinstance(value, bool) else str(value)
                    edited = '\n'.join(f'{key} = {value_text(value)}' for key, value in normalized_explicit.items())
                    updated = merge_edits(raw_text, edited)
                    if updated != raw_text:
                        write_text(path, updated)
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
            config = normalize_config(config)
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

    from .paths import CONFIG_PATH
    from pathlib import Path
    try:
        configured = str(parse_conf_text(Path(CONFIG_PATH).read_text(encoding="utf-8")).get("editor") or "").strip()
    except OSError:
        configured = ""
    if configured:
        return configured

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


def open_config_editor(editor_override: str | None = None, *, expanded=False) -> int:
    """Ensure config exists and open it in the user's preferred or fallback editor."""
    from ..ui.transition import call_on_ui_thread, in_preparation
    if in_preparation():
        return call_on_ui_thread(lambda: open_config_editor(editor_override, expanded=expanded))
    import shlex
    import subprocess
    import sys
    from contextlib import nullcontext

    from ..ui.terminal_runtime import current_runtime
    from .paths import CONFIG_PATH

    # Ensure config file exists on disk
    if not os.path.exists(CONFIG_PATH):
        if expanded:
            from .config_editor import write_text
            write_text(CONFIG_PATH, "# Anyplay Configuration\n")
        else:
            save_config_file(CONFIG_PATH, dict(DEFAULT_CONFIG))

    editor_cmd = resolve_editor(editor_override)
    if not editor_cmd:
        print("\033[91m[!] No suitable editor found.\033[0m", file=sys.stderr)
        print(f"Please specify an editor with -e <editor> or edit manually: {CONFIG_PATH}", file=sys.stderr)
        return 1

    if expanded:
        from .config_editor import expanded_view, merge_edits, write_text
        from pathlib import Path
        original = Path(CONFIG_PATH).read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="anyplay-config-") as directory:
            view_path = Path(directory) / "config.conf"
            view = expanded_view(original)
            write_text(str(view_path), view)
            runtime = current_runtime()
            try:
                with runtime.suspend() if runtime is not None else nullcontext():
                    result = subprocess.run(shlex.split(editor_cmd) + [str(view_path)])
                if result.returncode:
                    return result.returncode
                edited = view_path.read_text(encoding="utf-8")
                if edited != view:
                    if Path(CONFIG_PATH).read_text(encoding="utf-8") != original:
                        print("Config changed while editing; no changes applied. Please reopen it.", file=sys.stderr)
                        return 1
                    merged = merge_edits(original, edited)
                    if merged != original:
                        write_text(CONFIG_PATH, merged)
                return 0
            except OSError as exc:
                print(f"Could not edit expanded config: {exc}", file=sys.stderr)
                return 1
    cmd_parts = shlex.split(editor_cmd) + [CONFIG_PATH]
    try:
        runtime = current_runtime()
        with runtime.suspend() if runtime is not None else nullcontext():
            res = subprocess.run(cmd_parts)
        if res.returncode == 0:
            print(f"Configuration file closed: {CONFIG_PATH}")
        return res.returncode
    except Exception as exc:
        print(f"\033[91m[!] Failed to launch editor '{editor_cmd}': {exc}\033[0m", file=sys.stderr)
        print(f"You can edit the file manually: {CONFIG_PATH}", file=sys.stderr)
        return 1


def print_saved_config():
    """Show the saved file, not expanded defaults; never reveal credentials."""
    import re
    from pathlib import Path
    from .paths import CONFIG_PATH
    try:
        text = Path(CONFIG_PATH).read_text(encoding="utf-8")
    except FileNotFoundError:
        print("No custom configuration saved. Use anyplay config -e to create it.")
        return
    pattern = re.compile(r'^(\s*(?:[#;]\s*)?([^#;=:\s]+)\s*[=:]\s*)(.*)$')
    for line in text.splitlines():
        match = pattern.match(line)
        if match and any(word in match[2].lower() for word in ('token', 'secret', 'password', 'api_key', 'apikey', 'cookie', 'credential')):
            line = match[1] + ('[hidden]' if match[3].strip() else '')
        print(line)
