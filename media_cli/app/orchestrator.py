"""Main application orchestrator and state-machine coordinator."""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import time
from typing import Any

from .. import app as handlers
from ..constants import APP_NAME, LEGACY_APP_NAMES
from ..cli.args import parse_cli_args
from ..cli.completion import generate_completion, install_completion
from ..context import FLAGS as runtime_flags
from ..context import MachineState, UiState
from ..core.anilist import (
    anilist_auth_login_existing_lines,
    anilist_auth_status_lines,
    anilist_auth_token_lines,
    anilist_token_storage_status,
    clear_anilist_token,
    fetch_anilist_list,
    get_show_anilist_id,
    prompt_anilist_token,
    retry_queued_anilist_writes,
    save_anilist_token,
    search_anilist,
)
from ..core.api import ProviderDependencyError
from ..core.processes import (
    kill_active_subprocesses,
)
from ..core.storage import (
    clear_search_history,
    get_default_download_dir,
    load_config,
    load_downloads_db,
    load_history,
    load_search_history,
    save_config,
    save_downloads_db,
    set_incognito_history_allowed,
)
from ..domain.episodes import episode_id_at, episode_index_for_id
from ..domain.metadata import prepare_show_display_state
from ..domain.titles import get_show_display_title
from ..providers import (
    _DEFAULT_PROVIDER_ID,
    get_provider,
    get_provider_registry,
    provider_key,
)
from ..ui import display
from ..ui.display import restore_terminal


def trigger_migration(old_dir_full: str, new_dir: str) -> None:
    db = load_downloads_db()
    if os.path.isdir(old_dir_full) and os.path.abspath(old_dir_full) != os.path.abspath(
        new_dir
    ):
        ans = (
            input(f"Move existing downloads from {old_dir_full} to {new_dir}? [y/N]: ")
            .strip()
            .lower()
        )
        if ans == "y":
            print(f"Moving downloads to {new_dir}...")
            try:
                os.makedirs(new_dir, exist_ok=True)
                moved_anything = False
                shows = db.get("shows", {})
                for title in shows.keys():
                    s = os.path.join(old_dir_full, title)
                    d = os.path.join(new_dir, title)
                    if os.path.isdir(s):
                        shutil.move(s, d)
                        moved_anything = True

                if (
                    moved_anything
                    and (os.path.basename(old_dir_full.rstrip(os.sep)) in (APP_NAME, *LEGACY_APP_NAMES))
                ):
                    try:
                        os.rmdir(old_dir_full)
                        print(f"Cleaned up empty folder: {old_dir_full}")
                    except OSError:
                        pass

                print("Migration complete!")
            except Exception as e:
                print(f"Failed to migrate files: {e}", file=sys.stderr)


def _compact_path(p: str) -> str:
    if not p:
        return ""
    home = os.path.expanduser("~")
    p = os.path.abspath(os.path.expanduser(p))
    if p == home:
        return "~"
    if p.startswith(home + os.sep):
        return "~" + p[len(home):]
    return p


def setup_download_directory(cfg: dict, db: dict, runtime_dir: str | None = None) -> str:
    """Resolve effective download directory, safely handling runtime overrides,
    missing/inaccessible paths, and interactive recovery prompts."""
    # 1. Custom directory for running instance (--download-dir / -d)
    if isinstance(runtime_dir, str) and runtime_dir.strip():
        custom_dir = os.path.abspath(os.path.expanduser(runtime_dir.strip()))
        try:
            os.makedirs(custom_dir, exist_ok=True)
        except Exception:
            pass
        return custom_dir

    raw_cfg_dir = str(cfg.get("download_dir", "") or "").strip()
    db_dir = str(db.get("current_download_dir", "") or "").strip()
    if db_dir:
        db_dir = os.path.abspath(os.path.expanduser(db_dir))

    # 2. If download_dir is non-empty in config
    if raw_cfg_dir:
        cfg_dir = os.path.abspath(os.path.expanduser(raw_cfg_dir))
        accessible = False
        try:
            os.makedirs(cfg_dir, exist_ok=True)
            test_file = os.path.join(cfg_dir, ".write_test")
            with open(test_file, "w") as f:
                f.write("")
            os.remove(test_file)
            accessible = True
        except Exception:
            accessible = False

        if accessible:
            if db_dir and os.path.abspath(db_dir) != os.path.abspath(cfg_dir):
                if os.path.isdir(db_dir) and db.get("shows"):
                    trigger_migration(db_dir, cfg_dir)
                db["current_download_dir"] = cfg_dir
                save_downloads_db(db)
            return cfg_dir

        # Configured path is not accessible
        print(f"\n\033[91m[!] Configured download_dir '{raw_cfg_dir}' is inaccessible (permission denied or invalid path).\033[0m")
        if not sys.stdin.isatty():
            return get_default_download_dir()

    # 3. If download_dir is empty or inaccessible (in interactive mode)
    if not sys.stdin.isatty():
        if db_dir and os.path.isdir(db_dir) and db.get("shows"):
            return db_dir
        return get_default_download_dir()

    prev_library_valid = False
    summary = ""
    if db_dir and os.path.isdir(db_dir):
        from .downloads import VIDEO_EXTS, extract_offline_episode_info
        from media_cli.media.download import sanitize_filename

        shows = db.get("shows", {})
        dirty = False

        # Prune dead shows that have no matching directory on disk
        for title in list(shows.keys()):
            show_dir = os.path.join(db_dir, title)
            if not os.path.isdir(show_dir):
                alt_dir = os.path.join(db_dir, sanitize_filename(title))
                if os.path.isdir(alt_dir):
                    shows[sanitize_filename(title)] = shows.pop(title)
                    dirty = True
                else:
                    del shows[title]
                    dirty = True

        # Scan actual folders on disk in db_dir
        valid_shows = 0
        total_eps = 0
        try:
            for item in sorted(os.listdir(db_dir)):
                subpath = os.path.join(db_dir, item)
                if not os.path.isdir(subpath):
                    continue
                try:
                    files = [
                        f for f in os.listdir(subpath)
                        if f.lower().endswith(VIDEO_EXTS)
                    ]
                except OSError:
                    continue
                if not files:
                    continue
                ep_ids = set()
                for f in files:
                    _, _, clean_id, _ = extract_offline_episode_info(f)
                    if clean_id:
                        ep_ids.add(clean_id)
                eps_count = len(ep_ids) if ep_ids else len(files)
                if eps_count > 0:
                    valid_shows += 1
                    total_eps += eps_count
                    if item not in shows:
                        shows[item] = {"metadata": {"name": item, "episodeCount": eps_count}, "episodes": sorted(list(ep_ids))}
                        dirty = True
                    else:
                        shows[item]["episodes"] = sorted(list(ep_ids)) if ep_ids else shows[item].get("episodes", [])
        except OSError:
            pass

        if dirty:
            save_downloads_db(db)

        if valid_shows > 0:
            summary = f"{valid_shows} show{'' if valid_shows == 1 else 's'}, {total_eps} episode{'' if total_eps == 1 else 's'}"
            prev_library_valid = True

    os_default = get_default_download_dir(require_existing=True)

    print("\n\033[1;34m[?]\033[0m \033[1mDownload directory is not set.\033[0m\n")

    info_lines = []
    options = []
    # Build options list: (label, action_type, path_value)
    if prev_library_valid:
        prev_disp = _compact_path(db_dir)
        info_lines.append(f"    \033[1;36mPrevious:\033[0m {prev_disp} ({summary})")
        options.append(("Restore previous directory  (Recommended)", "prev", db_dir))

        if os_default and os.path.abspath(os_default) != os.path.abspath(db_dir):
            def_disp = _compact_path(os_default)
            info_lines.append(f"    \033[1;36mDefault:\033[0m  {def_disp}")
            options.append(("Use default directory", "default", os_default))

        options.append(("Choose custom directory", "custom", None))

    elif os_default:
        def_disp = _compact_path(os_default)
        info_lines.append(f"    \033[1;36mDefault:\033[0m  {def_disp}")
        options.append(("Use default directory  (Recommended)", "default", os_default))
        options.append(("Choose custom directory", "custom", None))

    else:
        # Scenario D: 1st time, no previous directory, and cannot detect OS default directory
        options.append(("Choose custom directory", "custom", None))

    if info_lines:
        for line in info_lines:
            print(line)
        print()

    selected_path = None
    if len(options) == 1 and options[0][1] == "custom":
        pass  # Jump directly to custom path input below
    else:
        for idx, (label, _, _) in enumerate(options, start=1):
            print(f"  {idx}) {label}")
        print()

        choice_idx = 1
        while True:
            try:
                ans = input("Select [1]: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(130)
            if not ans:
                choice_idx = 1
                break
            if ans.isdigit() and 1 <= int(ans) <= len(options):
                choice_idx = int(ans)
                break
            print(f"\033[91mPlease enter a number between 1 and {len(options)}.\033[0m")

        selected_opt = options[choice_idx - 1]
        if selected_opt[1] in ("prev", "default"):
            selected_path = selected_opt[2]

    # Custom path input loop
    if not selected_path:
        while True:
            try:
                raw_input_path = input("Enter download directory path: ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                sys.exit(130)
            if not raw_input_path:
                print("\033[91m[!] Path cannot be empty.\033[0m")
                continue
            cand = os.path.abspath(os.path.expanduser(raw_input_path))
            try:
                os.makedirs(cand, exist_ok=True)
                test_file = os.path.join(cand, ".write_test")
                with open(test_file, "w") as f:
                    f.write("")
                os.remove(test_file)
                selected_path = cand
                break
            except Exception as exc:
                print(f"\033[91m[!] Inaccessible path '{raw_input_path}': {exc}. Please try again.\033[0m")

    # Save to config.conf and update database
    cfg["download_dir"] = selected_path
    save_config(cfg)
    print(f"\033[32m✓ Saved download_dir = {selected_path} to config\033[0m\n")

    if prev_library_valid and db_dir and os.path.abspath(db_dir) != os.path.abspath(selected_path):
        trigger_migration(db_dir, selected_path)

    db["current_download_dir"] = selected_path
    save_downloads_db(db)
    return selected_path


def handle_config_command(args: Any) -> None:
    action = getattr(args, "config_action", None)
    if action is None:
        editor_override = getattr(args, "config_editor", None)
        from ..state.config import open_config_editor
        code = open_config_editor(editor_override)
        if code != 0:
            sys.exit(code)
        return

    if action == "get":
        key = getattr(args, "config_key", None)
        if not key:
            print(f"{APP_NAME} config: error: 'get' requires a key (e.g., {APP_NAME} config get quality)", file=sys.stderr)
            sys.exit(2)
        cfg = load_config()
        if key in cfg:
            val = cfg[key]
            if isinstance(val, bool):
                print(str(val).lower())
            elif val is None:
                print("")
            else:
                print(val)
            return
        else:
            print(f"Key '{key}' not found in configuration.", file=sys.stderr)
            sys.exit(1)

    if action == "set":
        key = getattr(args, "config_key", None)
        val = getattr(args, "config_value", None)
        if not key or val is None:
            print(f"{APP_NAME} config: error: 'set' requires both a key and a value (e.g., {APP_NAME} config set download_dir ~/Downloads/{APP_NAME})", file=sys.stderr)
            sys.exit(2)

        cfg = load_config()
        if key == "download_dir":
            old_dir = cfg.get("download_dir", "")
            new_dir = os.path.expanduser(str(val or "").strip())

            if not new_dir:
                print("download_dir requires a value.", file=sys.stderr)
                return

            if not new_dir.endswith(APP_NAME):
                ans_append = (
                    input(f"Create '{APP_NAME}' subfolder in {new_dir}? [Y/n]: ")
                    .strip()
                    .lower()
                )
                if ans_append != "n":
                    new_dir = os.path.join(new_dir, APP_NAME)
                    val = os.path.join(str(val or "").strip(), APP_NAME)

            if not old_dir:
                old_dir_full = get_default_download_dir()
            else:
                old_dir_full = os.path.expanduser(old_dir)

            trigger_migration(old_dir_full, new_dir)
            cfg["download_dir"] = val
            save_config(cfg)
            db = load_downloads_db()
            db["current_download_dir"] = new_dir
            save_downloads_db(db)
            print(f"Config updated: {key} = {val}")
        else:
            val_to_save = val
            if isinstance(val, str):
                v_lower = val.strip().lower()
                if v_lower in ("true", "yes", "on"):
                    val_to_save = True
                elif v_lower in ("false", "no", "off"):
                    val_to_save = False
                else:
                    try:
                        val_to_save = int(val)
                    except ValueError:
                        try:
                            val_to_save = float(val)
                        except ValueError:
                            val_to_save = val
            cfg[key] = val_to_save
            save_config(cfg)
            print(f"Config updated: {key} = {val}")


def main() -> None:
    from .. import app_core

    def _force_exit(sig, frame):
        kill_active_subprocesses()
        try:
            restore_terminal()
        except Exception:
            pass
        os._exit(130)

    signal.signal(signal.SIGINT, _force_exit)

    args, pa = parse_cli_args()

    if getattr(args, "command", None) != "config" and not getattr(args, "completion_shell", None) and not getattr(args, "show_search_history", False) and not getattr(args, "clear_search_history", False) and not getattr(args, "list_providers", False):
        cfg = load_config()
        db = load_downloads_db()
        runtime_dl_dir = getattr(args, "download_dir", None)
        active_dl_dir = setup_download_directory(cfg, db, runtime_dl_dir)
        args.download_dir = active_dl_dir
        cfg["download_dir"] = active_dl_dir

    if getattr(args, "completion_shell", None):
        globals()["SUPPRESS_FINAL_CURSOR_RESTORE"] = True
        if getattr(args, "completion_install", False):
            path = install_completion(args.completion_shell)
            print(f"Installed {args.completion_shell} completion:")
            print(path)
            if args.completion_shell == "bash":
                print(f"Restart your shell, or run: source {path}")
            elif args.completion_shell == "zsh":
                print("Ensure ~/.zfunc is in fpath, then run: compinit")
            elif args.completion_shell == "fish":
                print("Restart fish, or run: exec fish")
            return
        print(generate_completion(args.completion_shell), end="")
        return

    if getattr(args, "show_search_history", False):
        globals()["SUPPRESS_FINAL_CURSOR_RESTORE"] = True
        for query in load_search_history():
            print(query)
        return

    if getattr(args, "clear_search_history", False):
        globals()["SUPPRESS_FINAL_CURSOR_RESTORE"] = True
        clear_search_history()
        print("Search history cleared.")
        return

    if getattr(args, "command", None) == "config":
        globals()["SUPPRESS_FINAL_CURSOR_RESTORE"] = True
        handle_config_command(args)
        return

    if getattr(args, "list_providers", False):
        globals()["SUPPRESS_FINAL_CURSOR_RESTORE"] = True
        cfg = load_config()
        default_pid = cfg.get("provider", _DEFAULT_PROVIDER_ID)
        CYAN, GREEN, BOLD, RESET, RED, YELLOW, DIM = (
            "\033[36m",
            "\033[32m",
            "\033[1m",
            "\033[0m",
            "\033[31m",
            "\033[33m",
            "\033[2m",
        )
        registry = get_provider_registry()

        print(f"\n{BOLD}Available Streaming Providers:{RESET}\n")
        term_width = shutil.get_terminal_size((80, 20)).columns

        rows = []
        for pid, meta in registry.items():
            name = meta.get("name", pid.title())
            engine = meta.get("engine", "unknown")
            status = meta.get("status", "unknown")
            ptype = meta.get("type", "anime")
            langs = "/".join(meta.get("languages", ["sub"]))
            rows.append(
                {
                    "pid": pid,
                    "name": name,
                    "engine": engine,
                    "status": status,
                    "type": ptype,
                    "langs": langs,
                }
            )

        w_id = max(max((len(r["pid"]) + 2 for r in rows), default=0), len("ID"))
        w_name = max(max((len(r["name"]) for r in rows), default=0), len("Name"))
        w_eng = max(max((len(r["engine"]) for r in rows), default=0), len("Engine"))
        w_stat = max(max((len(r["status"]) for r in rows), default=0), len("Status"))
        w_type = max(max((len(r["type"]) for r in rows), default=0), len("Type"))
        w_lang = max(max((len(r["langs"]) for r in rows), default=0), len("Languages"))

        spacing = 4
        full_table_len = 2 + w_id + spacing + w_name + spacing + w_eng + spacing + w_stat + spacing + w_type + spacing + w_lang

        if term_width < full_table_len:
            for idx, r in enumerate(rows):
                pid = r["pid"]
                engine = r["engine"]
                status = r["status"]
                ptype = r["type"]
                langs = r["langs"]

                status_color = GREEN if status == "active" else RED
                dot = "●" if status == "active" else "○"
                status_str = f"{status_color}{dot} {status:<8}{RESET}"

                if pid == default_pid:
                    prefix = f"{GREEN}▸{RESET}"
                    pid_styled = f"{BOLD}{pid.ljust(w_id - 2)}{RESET}"
                else:
                    prefix = " "
                    pid_styled = f"{CYAN}{pid.ljust(w_id - 2)}{RESET}"

                type_badge = f"{DIM}[{ptype}]{RESET}"

                if engine == "hybrid":
                    eng_str = f"{YELLOW}{engine}{RESET}"
                elif engine == "scraper":
                    eng_str = f"{DIM}{engine}{RESET}"
                else:
                    eng_str = f"{engine}"

                line1 = f"{prefix} {pid_styled}  {status_str}  {type_badge}"
                line2 = f"  {eng_str} {DIM}•{RESET} {langs}"

                if idx > 0:
                    print()
                print(line1)
                print(line2)

            print()
            print(f"  {DIM}▸ = default provider{RESET}")
            print(
                f"  {DIM}Note: Scrapers are brittle and may break often. APIs and Hybrids are recommended.{RESET}\n"
            )
            return

        header = (
            f"    {BOLD}{'ID'.ljust(w_id - 2 + spacing)}"
            f"{'Name'.ljust(w_name + spacing)}"
            f"{'Engine'.ljust(w_eng + spacing)}"
            f"{'Status'.ljust(w_stat + spacing)}"
            f"{'Type'.ljust(w_type + spacing)}"
            f"{'Languages'}{RESET}"
        )

        print(header)
        print(f"  {DIM}{'-'*full_table_len}{RESET}")

        for r in rows:
            pid, name, engine, status, ptype, langs = (
                r["pid"],
                r["name"],
                r["engine"],
                r["status"],
                r["type"],
                r["langs"],
            )
            status_color = GREEN if status == "active" else RED

            if pid == default_pid:
                id_str = f"{GREEN}▸ {pid.ljust(w_id - 2)}{RESET}"
            else:
                id_str = f"  {CYAN}{pid.ljust(w_id - 2)}{RESET}"

            engine_str = (
                f"{YELLOW}{engine.ljust(w_eng)}{RESET}"
                if engine == "hybrid"
                else f"{engine.ljust(w_eng)}"
            )
            if engine == "scraper":
                engine_str = f"{DIM}{engine.ljust(w_eng)}{RESET}"

            row_str = (
                f"  {id_str}{' '*spacing}"
                f"{name.ljust(w_name)}{' '*spacing}"
                f"{engine_str}{' '*spacing}"
                f"{status_color}{status.ljust(w_stat)}{RESET}{' '*spacing}"
                f"{ptype.ljust(w_type)}{' '*spacing}"
                f"{langs}"
            )
            print(row_str)

        print()
        print(
            f"  {DIM}Note: Scrapers are brittle and may break often. APIs and Hybrids are recommended.{RESET}\n"
        )
        return

    app_core.check_deps()
    cfg = load_config()
    if getattr(args, "provider", None) is None:
        args.provider = cfg.get("provider", _DEFAULT_PROVIDER_ID)
    display._configure_spinner_from_config(cfg)

    runtime_flags.debug_mode = args.debug
    runtime_flags.incognito_mode = bool(args.incognito)
    if args.incognito:
        allow_history = bool(
            getattr(args, "history", False)
            or getattr(args, "cont", False)
            or getattr(args, "command", None) in ("history", "continue")
        )
        set_incognito_history_allowed(allow_history)
    else:
        set_incognito_history_allowed(False)
    if args.incognito and (
        args.download or args.downloads or args.login or args.logout
    ):
        pa.error("--incognito cannot be combined with downloads, login, or logout")
    if args.incognito:
        args.no_sync = True
    runtime_flags.sync_force_on = bool(args.sync and not args.no_sync)
    runtime_flags.sync_force_off = bool(args.no_sync)
    runtime_flags.plain_mode = bool(
        getattr(args, "plain", False) or not sys.stdin.isatty()
    )
    if runtime_flags.plain_mode:
        runtime_flags.show_image = False
    elif getattr(args, "cover", None) is not None:
        runtime_flags.show_image = bool(args.cover)
    else:
        runtime_flags.show_image = bool(cfg.get("cover", False))

    if args.json:
        q = " ".join(args.query)
        ttype = "dub" if args.dub else "sub"

        if args.anilist is not None:
            if args.anilist == "search":
                res = search_anilist(cfg.get("anilist_token"), q)
            elif args.anilist and args.anilist != "menu":
                res = fetch_anilist_list(cfg.get("anilist_token"), args.anilist.upper())
            else:
                res = fetch_anilist_list(cfg.get("anilist_token"), "CURRENT")
            print(json.dumps(res, indent=2))
        else:
            if not q:
                print(json.dumps({"error": "No query provided."}, indent=2))
            else:
                res = app_core.search_anime(
                    q,
                    ttype,
                    provider_id=args.provider,
                )
                print(json.dumps(res, indent=2))
        sys.exit(0)

    if args.logout:
        clear_anilist_token(cfg)
        print("\033[32mLogged out of AniList.\033[0m")
        sys.exit(0)

    if getattr(args, "auth_status", False):
        print("\n".join(anilist_auth_status_lines(cfg)))
        sys.exit(0)

    if getattr(args, "auth_token", False):
        lines = anilist_auth_token_lines(
            cfg, raw=getattr(args, "auth_token_raw", False)
        )
        if not lines:
            print("AniList token is not saved.", file=sys.stderr)
            sys.exit(1)
        print("\n".join(lines))
        sys.exit(0)

    if args.login:
        if anilist_token_storage_status(cfg) != "none":
            print("\n".join(anilist_auth_login_existing_lines(cfg)))
            sys.exit(0)
        print("\n\033[33mAniList login\033[0m")
        print("Open this link, sign in, and copy the token:")
        print(
            "\033[4mhttps://anilist.co/api/v2/oauth/authorize?client_id=9857&response_type=token\033[0m"
        )
        tkn = prompt_anilist_token()
        if tkn:
            storage_loc = save_anilist_token(cfg, tkn)
            print(f"\033[32mAniList token saved to {storage_loc}.\033[0m")
        else:
            print("\033[31mNo token provided.\033[0m")
        sys.exit(0)

    runtime_flags.spinner_style = display._spinner_style
    flags = runtime_flags
    ui = UiState()

    def warn_before_tui(message):
        print(f"\n\033[33m{message}\033[0m")
        for remaining in range(3, 0, -1):
            print(f"\rContinuing in {remaining}...", end="", flush=True)
            time.sleep(1)
        print("\rContinuing now.   ")

    if args.sync and not args.no_sync:
        if args.history or args.cont:
            warn_before_tui(
                "--sync only applies to searched titles. History and continue stay local."
            )
            args.sync = False
            flags.sync_force_on = False
        elif not cfg.get("anilist_token") and not args.anilist:
            warn_before_tui(
                "--sync needs AniList login. Sync is disabled for this session."
            )
            args.sync = False
            args.no_sync = True
            flags.sync_force_on = False
            flags.sync_force_off = True

    def resolveTracking(ctx, args, cfg, show=None):
        show = show or app_core.get_ui_show(ui)

        if getattr(args, "no_sync", False) or flags.incognito_mode:
            return False

        sync_flag = getattr(args, "sync", False)
        config_sync = bool(cfg.get("sync") or cfg.get("auto_track"))

        if (sync_flag or config_sync) and ctx == "SEARCH":
            return bool(cfg.get("anilist_token") and show and get_show_anilist_id(show))

        if ctx in ("ANILIST_BROWSE", "ANILIST_SEARCH", "ANILIST_AIRING"):
            return bool(cfg.get("anilist_token") and show and get_show_anilist_id(show))

        return False

    globals()["resolveTracking"] = resolveTracking

    if args.anilist and not args.no_sync and not cfg.get("anilist_token"):
        print("\n\033[33mAniList tracking needs a token.\033[0m")
        print("Open this link, sign in, and copy the token:")
        print(
            "\033[4mhttps://anilist.co/api/v2/oauth/authorize?client_id=9857&response_type=token\033[0m"
        )
        tkn = prompt_anilist_token()
        if tkn:
            storage_loc = save_anilist_token(cfg, tkn)
            print(f"\033[32mAniList token saved to {storage_loc}.\033[0m")
        else:
            print(
                "\033[31mNo token provided. Tracking is disabled for this session.\033[0m"
            )
            args.no_sync = True
            flags.sync_force_on = False
            flags.sync_force_off = True
    if cfg.get("anilist_token") and not args.no_sync:
        retry_queued_anilist_writes(cfg["anilist_token"])

    if args.episode and "-" in args.episode:
        _s, _e = args.episode.split("-")
        args.episode = _s.strip()
        globals()["_download_batch_end"] = int(_e)
    elif args.episode:
        args.episode = str(args.episode).strip()

    if getattr(args, "dub", False):
        ttype = "dub"
    elif getattr(args, "sub", False):
        ttype = "sub"
    else:
        ttype = cfg.get("translation_type", "sub")
    ui.ui_ttype_ctx = ttype
    active_provider = (
        getattr(args, "provider", None)
        or cfg.get("provider")
        or cfg.get("default_provider")
        or _DEFAULT_PROVIDER_ID
    )
    ui.ui_provider_ctx = provider_key(active_provider)
    p_inst = get_provider(ui.ui_provider_ctx)
    if hasattr(p_inst, "validate_environment"):
        try:
            p_inst.validate_environment()
        except ProviderDependencyError as exc:
            from ..ui.display import fatal_terminal_exit

            fatal_terminal_exit(str(exc))
    quality = args.quality or cfg.get("quality", "1080p")

    auto_download_val = getattr(args, "auto_download", None)
    if auto_download_val is None:
        auto_download_val = cfg.get("auto_download_next", False)

    auto_delete_val = getattr(args, "auto_delete", None)
    if auto_delete_val is None:
        auto_delete_val = cfg.get("auto_delete_watched", False)

    ms = MachineState(
        query_str=" ".join(args.query) if args.query else "",
        just_searched=bool(args.query),
        anilist_search_parent="QUIT",
        download_batch_end=globals().get("_download_batch_end"),
        auto_download_next=bool(auto_download_val),
        auto_delete_watched=bool(auto_delete_val),
        auto_delete_buffer=int(cfg.get("auto_delete_buffer", 1)),
    )

    state = "SEARCH"

    if args.anilist is not None:
        if args.anilist == "search":
            state = "ANILIST_SEARCH"
        elif args.anilist == "airing":
            state = "ANILIST_AIRING"
            args.anilist = "menu"
        else:
            if not cfg.get("anilist_token"):
                print("\n\033[33mAniList account is not linked.\033[0m")
                print(f"Run \033[1m{APP_NAME} auth login\033[0m to authenticate.")
                sys.exit(1)
            state = "ANILIST_MENU"
    elif args.cont:
        hist = load_history()
        if hist:
            h = hist[0]
            show = h.get("show", {})
            prepare_show_display_state(show, h.get("translation_type", ttype))
            ms.show_id = show.get("_id")
            ms.show_title = get_show_display_title(show)
            _hist_ttype = h.get("translation_type", ttype)
            ms.total_eps = show.get("availableEpisodes", {}).get(_hist_ttype, 1)
            ms.current_ep = app_core.playback_ep_from_history_entry(h, _hist_ttype)
            episode_ids = app_core.ensure_episode_ids(show, _hist_ttype)
            ms.total_eps = len(episode_ids) or ms.total_eps
            ms.current_ep_index = episode_index_for_id(episode_ids, ms.current_ep)
            app_core.set_ui_context(ui, show, _hist_ttype)
            if not episode_ids:
                print(app_core.episode_catalog_error(show), file=sys.stderr)
                state = "HISTORY"
            elif ms.current_ep_index is None:
                print(
                    "Saved episode is not present in the current provider catalog.",
                    file=sys.stderr,
                )
                state = "HISTORY"
            else:
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
                print(f"Continuing {ms.show_title}: EP {ms.current_ep}/{ms.total_eps}")
                state = "PLAY"
        else:
            print("Watch history is empty.")
    elif args.downloads:
        state = "DOWNLOADS"
    elif args.history:
        state = "HISTORY"

    while state != "QUIT":
        cur_ttype = ui.ui_ttype_ctx if ui.ui_ttype_ctx is not None else ttype
        if state == "DOWNLOADS":
            from media_cli.app.downloads import handle_downloads_state

            state = handle_downloads_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "SEARCH":
            state = handlers.handle_search_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "HISTORY":
            state = handlers.handle_history_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "ANILIST_MENU":
            state = handlers.handle_anilist_menu_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "ANILIST_AIRING":
            state = handlers.handle_anilist_airing_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "ANILIST_BROWSE":
            state = handlers.handle_anilist_browse_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "ANILIST_SEARCH":
            state = handlers.handle_anilist_search_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state in ("DETAILS", "ACTION_MENU"):
            state = handlers.handle_details_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "UPDATE_PROGRESS":
            state = handlers.handle_update_progress_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "UPDATE_STATUS":
            state = handlers.handle_update_status_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "UPDATE_SCORE":
            state = handlers.handle_update_score_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "EPISODE":
            state = handlers.handle_episode_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        elif state == "PLAY":
            state = handlers.handle_play_state(
                flags, ui, ms, cfg, args, cur_ttype, quality, resolveTracking
            )
        elif state == "MIRRORS":
            state = handlers.handle_mirrors_state(
                flags, ui, ms, cfg, args, cur_ttype, resolveTracking
            )
        else:
            print(f"Unknown state: {state}", file=sys.stderr)
            state = "QUIT"

    app_core._ipc_player.quit()
