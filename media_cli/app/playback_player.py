"""Playback handler coordinating stream resolution, player runtime, and sync."""

from __future__ import annotations

import os
import re
import sys
import time
from typing import Any

from .. import app_core
from ..context import CliFlags, MachineState, UiState
from ..core import streams as streams_mod
from ..domain.episodes import (
    clean_episode_identifier,
    episode_id_at,
    episode_index_for_id,
    episode_progress_number,
)
from ..domain.titles import get_show_display_title
from ..domain.tracking import tracking_status_for_progress
from ..playback.rules import (
    marked_watched_osd,
    pending_completion_osd,
    playback_looks_complete,
    playback_updates_history,
    sync_queued_osd,
)
from ..providers import title_provider_key
from ..ui.display import suppress_terminal_echo
from . import playback as playback_mod

GREEN = "\033[1;32m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
RESET = "\033[0m"




def trigger_background_auto_download(show_ctx, current_ep_index, episode_ids, ttype, cfg, ms):
    if not (cfg.get("auto_download_next", False) or getattr(ms, "auto_download_next", False)):
        return
    if not app_core.is_online():
        return
    if current_ep_index is None or not episode_ids:
        return
    next_idx = current_ep_index + 1
    if next_idx >= len(episode_ids):
        return
    next_ep = episode_id_at(episode_ids, next_idx)
    if not next_ep:
        return

    from . import playback_episodes as playback_mod
    lbl_next = playback_mod._display_episode_label(show_ctx, next_ep, ttype)
    show_title = getattr(ms, "show_title", "") or (show_ctx.get("name") if show_ctx else "")
    if not show_title:
        return

    dl_files = getattr(ms, "_download_files", {})
    if str(next_ep) in dl_files or str(lbl_next) in dl_files:
        return
    if app_core.find_offline_file_for_episode(show_title, lbl_next, cfg) or app_core.find_offline_file_for_episode(show_title, str(next_ep), cfg):
        return

    bg_tasks = getattr(ms, "_bg_download_tasks", None)
    if bg_tasks is None:
        ms._bg_download_tasks = set()
        bg_tasks = ms._bg_download_tasks
    task_key = (show_title, str(next_ep))
    if task_key in bg_tasks:
        return
    bg_tasks.add(task_key)

    import threading
    def _bg_worker():
        try:
            p_id = app_core.title_provider_id(show_ctx)
            ep_d = (show_ctx.get("_provider_data") or {}).get(str(next_ep))
            res = app_core.fetch_episode_stream(
                show_ctx.get("_id") or p_id,
                next_ep,
                ttype,
                cfg.get("quality", "best"),
                provider_id=p_id,
                ep_data=ep_d,
            )
            if not res:
                return
            selected_stream, first_source_name, _, streams = res
            if not selected_stream:
                return

            downloader_choice = cfg.get("downloader", "auto")
            download_dir = cfg.get("download_dir", "")
            download_ok = app_core.download_episode(
                show_title,
                lbl_next,
                selected_stream,
                download_dir=download_dir,
                downloader=downloader_choice,
                quiet=True,
            )
            if download_ok:
                try:
                    from media_cli.core.storage import load_downloads_db, save_downloads_db
                    from media_cli.media.download import sanitize_filename
                    db = load_downloads_db()
                    folder_key = sanitize_filename(show_title) or show_title
                    if folder_key not in db["shows"]:
                        db["shows"][folder_key] = {"episodes": []}
                    def build_offline_metadata(s):
                        meta = {}
                        keys_to_keep = [
                            "id", "_id", "provider_id", "name", "englishName", "nativeName", "altNames",
                            "thumbnail", "description", "type", "format",
                            "status", "season", "airedStart", "airedEnd", "startDate",
                            "endDate", "score", "genres", "tags", "aniListId", "malId",
                            "_display_name", "_display_english_name", "_anilist_list",
                            "_anilist_progress", "_anilist_score", "anilistMatch",
                            "originalEpisodeCount", "episodeCount", "_provider", "provider",
                            "_provider_name", "_provider_data", "_episode_ids", "_episode_labels",
                            "_episode_ids_by_ttype", "availableEpisodes", "availableEpisodesDetail",
                            "_local_progress", "_local_episode_label", "watched_episodes"
                        ]
                        for k in keys_to_keep:
                            if k in s:
                                meta[k] = s[k]
                        if "originalEpisodeCount" not in meta and "episodeCount" in s:
                            meta["originalEpisodeCount"] = s["episodeCount"]
                        return meta
                    db["shows"][folder_key]["metadata"] = build_offline_metadata(show_ctx)
                    ep_str = str(next_ep)
                    if ep_str not in db["shows"][folder_key]["episodes"]:
                        db["shows"][folder_key]["episodes"].append(ep_str)
                    save_downloads_db(db)

                    new_f = app_core.find_offline_file_for_episode(show_title, lbl_next, cfg)
                    if new_f:
                        if hasattr(ms, "_download_files") and isinstance(ms._download_files, dict):
                            ms._download_files[str(next_ep)] = new_f
                            ms._download_files[str(lbl_next)] = new_f
                    app_core.set_action_feedback(show_ctx, f"✔ Auto-downloaded EP {lbl_next}")
                except Exception:
                    pass
        except Exception:
            pass
        finally:
            bg_tasks.discard(task_key)

    th = threading.Thread(target=_bg_worker, daemon=True)
    th.start()


def handle_play_state(
    flags: CliFlags,
    ui: UiState,
    ms: MachineState,
    cfg: dict,
    args: Any,
    ttype: str,
    quality: str,
    resolve_tracking_fn,
) -> str:
    s_ctx = ui.ui_show_ctx
    sync_enabled = resolve_tracking_fn(ui.search_prev_state, args, cfg, s_ctx)
    app_core.prepare_show_display_state(s_ctx, ttype, sync_enabled)
    ms.show_title = get_show_display_title(s_ctx, sync_enabled=sync_enabled)
    ms.show_id = s_ctx.get("_id")
    al_id = app_core.get_show_anilist_id(s_ctx)
    episode_ids = app_core.ensure_episode_ids(s_ctx, ttype)

    if not episode_ids:
        app_core._exit_player_screen()
        app_core.err(app_core.episode_catalog_error(s_ctx))
        return "DETAILS"

    ms.total_eps = len(episode_ids) or ms.total_eps
    ms.current_ep_index = episode_index_for_id(
        episode_ids, ms.current_ep, labels=ui.ui_show_ctx.get("_episode_labels")
    )

    if ms.current_ep_index is None:
        app_core._exit_player_screen()
        app_core.err(f"EP {clean_episode_identifier(str(ms.current_ep)) or ms.current_ep} is not present in the provider catalog.")
        return "EPISODE"

    ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
    current_ep_label = playback_mod._display_episode_label(s_ctx, ms.current_ep, ttype)

    is_download_mode = bool(getattr(args, 'download', False))

    if is_download_mode:
        app_core.restore_terminal()
        print(
            f"\n\033[1;36mDownloading episode {playback_mod._fmt_ep(current_ep_label)} for '{ms.show_title}' ({ttype.upper()})…\033[0m\n"
        )
        print(
            f"\033[1;97mResolving stream for EP {playback_mod._fmt_ep(current_ep_label)}…\033[0m"
        )
    else:
        app_core.enter_alt_screen()
        from ..ui.player_screen import _player_ui_state
        _player_ui_state.update({
            "active": True,
            "show": ui.ui_show_ctx,
            "ttype": ttype,
            "current_ep": ms.current_ep,
            "current_ep_label": current_ep_label,
            "total_eps": ms.total_eps,
            "status_lines": [],
            "stream_info": {},
            "mpv_props": None,
            "_cleared_terminal_image": False,
            "_last_poster_key": None,
        })

    with suppress_terminal_echo():
        provider_id = title_provider_key(ui.ui_show_ctx)
        p_name = (ui.ui_show_ctx.get("_provider_name") or ui.ui_show_ctx.get("provider_name") or provider_id).title()
        _ipc_player = app_core._ipc_player
        is_prefetched = (
            _ipc_player is not None
            and getattr(_ipc_player, "prefetched_res", None) is not None
            and (
                str(getattr(_ipc_player, "prefetched_ep", "")) == str(ms.current_ep)
                or getattr(_ipc_player, "prefetched_ep_id", None) == str(ms.current_ep)
                or (isinstance(getattr(_ipc_player, "prefetched_ep", None), int) and ms.current_ep_index is not None and _ipc_player.prefetched_ep == ms.current_ep_index + 1)
                or (getattr(_ipc_player, "prefetched_ep", None) is not None and episode_progress_number(_ipc_player.prefetched_ep) == episode_progress_number(ms.current_ep))
            )
        )

        first_source_name = None
        ep_data = None
        aniskip_future = None
        _skip_pool = None
        _cache_key = (ms.show_id, ms.current_ep, ttype, provider_id)

        if is_prefetched:
            res = _ipc_player.prefetched_res
            _ipc_player.prefetched_ep = None
            _ipc_player.prefetched_stream = None
            _ipc_player.prefetched_res = None
            _ipc_player.prefetched_ep_id = None
            ms.selected_stream, first_source_name, ep_data, resolved_streams = res
            ms.ep_cache_key = _cache_key
            ms.ep_cache_data = ep_data
            app_core._extend_streams(resolved_streams, key=_cache_key)
        else:
            if not is_download_mode:
                _player_ui_state["loading_provider_name"] = p_name
                _player_ui_state["loading_start_time"] = time.time()

                try:
                    from ..ui.player_screen import start_loading_ticker
                    start_loading_ticker(
                        poster_manager=getattr(app_core, "_poster_manager", None),
                        ui=ui,
                    )
                except ImportError:
                    pass

            aniskip_enabled = getattr(args, "aniskip", None)
            if aniskip_enabled is None:
                aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

            if aniskip_enabled and not is_download_mode:
                action_show = ui.ui_show_ctx or {}
                mal_id = app_core.get_show_mal_id(action_show)
                ep_num = episode_progress_number(ms.current_ep)
                if mal_id:
                    from concurrent.futures import ThreadPoolExecutor

                    from ..media.aniskip import fetch_skip_times
                    _skip_pool = ThreadPoolExecutor(max_workers=1)
                    aniskip_future = _skip_pool.submit(fetch_skip_times, mal_id, ep_num)

            if getattr(ms, "_is_downloads", False) and not is_download_mode:
                ep_data = {"is_local": True}
            elif _cache_key == ms.ep_cache_key and ms.ep_cache_data:
                ep_data = ms.ep_cache_data
            else:
                app_core.info(f"[{p_name}] Requesting {playback_mod._fmt_ep(current_ep_label)} metadata...")
                ep_data = app_core.get_episode_data(
                    ms.show_id,
                    ms.current_ep,
                    ttype,
                    provider_id=provider_id,
                )
                ms.ep_cache_key  = _cache_key
                ms.ep_cache_data = ep_data

            if not ep_data:
                if _skip_pool:
                    try:
                        _skip_pool.shutdown(wait=False)
                    except Exception:
                        pass
                app_core._exit_player_screen()
                playback_mod._clear_episode_source_state(ms)
                p_name = (ui.ui_show_ctx.get("_provider_name") or (ui.ui_show_ctx.get("_provider") or "").title() or "this provider") if ui.ui_show_ctx else "this provider"
                if ttype == "dub":
                    msg = f"No DUB stream available for {playback_mod._fmt_ep(current_ep_label)} on {p_name}."
                else:
                    msg = f"No stream available for {playback_mod._fmt_ep(current_ep_label)} on {p_name}."
                app_core.set_action_feedback(
                    ui.ui_show_ctx,
                    msg,
                )
                return "DETAILS"

        if ms.selected_stream is None:
            _ipc_player = app_core._ipc_player
            target_quality = getattr(args, "quality", None) or cfg.get("quality", "best")
            if getattr(ms, "_is_downloads", False) and not is_download_mode:
                filepath = ms._download_files.get(str(ms.current_ep))
                if filepath:
                    res = ({"link": filepath, "resolution": "Local", "is_local": True}, "Local File", {"is_local": True, "filepath": filepath}, [])
                    ms.selected_stream = res[0]
                    first_source_name = "Local File"
                else:
                    res = None
            else:
                local_file = app_core.find_offline_file_for_episode(ms.show_title, ms.current_ep, cfg) if not getattr(args, 'download', False) and not getattr(args, 'sources', False) else None
                if local_file and os.path.exists(local_file):
                    res = ({"link": local_file, "resolution": "Offline (Local)", "is_local": True}, "Local File", {"is_local": True, "filepath": local_file}, [])
                elif str(_ipc_player.prefetched_ep) == str(ms.current_ep) and _ipc_player.prefetched_res:
                    res = _ipc_player.prefetched_res
                    _ipc_player.prefetched_ep = None
                    _ipc_player.prefetched_stream = None
                    _ipc_player.prefetched_res = None
                else:
                    cur_key = (ms.show_id, ms.current_ep, ttype, provider_id, target_quality)
                    cached_ep_data = app_core._get_cached_ep_data(cur_key)

                    # Start background resolver to populate cache if not already running
                    if not cached_ep_data:
                        cached_ep_data = app_core.get_episode_data(ms.show_id, ms.current_ep, ttype, provider_id=provider_id)

                    # Start background resolver to populate cache
                    streams_mod.start_bg_resolve(cached_ep_data, set(), ms.show_id, ms.current_ep, ttype, provider_id, target_quality)

                    # Wait for background resolver to populate some streams
                    import time as _time
                    wait_start = _time.time()
                    while _time.time() - wait_start < 5:  # Max 5 seconds wait
                        cached_streams = app_core._stream_snapshot(ms.show_id, ms.current_ep, ttype, provider_id, target_quality, cached_ep_data)
                        if cached_streams:
                            break
                        _time.sleep(0.15)

                    res = None

                    if cached_streams:
                        pref = app_core.get_preferred_mirror(ms.show_id)
                        pref_name = pref.get("source_name", "")
                        pref_res = pref.get("resolution", "")
                        pref_name_lower = pref_name.lower()
                        pref_res_lower = pref_res.lower() if pref_res else ""

                        # 1. Check if preferred mirror is available - use it immediately
                        preferred_stream = None
                        if pref_name:
                            for s in cached_streams:
                                src_name = s.get("source_name") or ""
                                src_res = (s.get("resolution") or "?").lower()
                                if src_name.lower().startswith(pref_name_lower) and (not pref_res_lower or src_res == pref_res_lower):
                                    preferred_stream = s
                                    break

                        if preferred_stream:
                            # Preferred mirror found - use it directly
                            src_name = preferred_stream.get("source_name", "Stream")
                            res = (preferred_stream, src_name, None, cached_streams)
                            if not ep_data:
                                ep_data = app_core._get_cached_ep_data(cur_key)
                            # Log the connection (same as fetch_episode_stream does)
                            from ..core import reporting
                            reporting.ok(f"Connected: {preferred_stream.get('source_name', src_name)} ({preferred_stream.get('resolution', '?')})")
                        else:
                            # 2. No preferred mirror or not available - fall back to normal logic
                            from ..media.sources import quality_preference_key
                            def _cached_sort_key(s):
                                sname = (s.get("source_name") or "").lower()
                                res = s.get("resolution") or ""
                                prio = s.get("source_priority", 4)
                                is_sub = "sub" in sname
                                is_dub = ("dub" in sname) or ((" eng" in sname or "english" in sname) and not is_sub)
                                audio_penalty = 1 if (ttype == "sub" and is_dub) or (ttype == "dub" and not is_dub) else 0
                                is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
                                is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
                                is_donghua = str(provider_id or "").lower() in {"animexin", "lucifer", "animekhor"}
                                if is_donghua:
                                    sub_rank = 0 if is_hard else (2 if is_soft else 1)
                                else:
                                    sub_rank = 0 if is_soft else (2 if is_hard else 1)

                                # Preferred mirror gets highest priority (for sorting fallback candidates)
                                pref_boost = 0
                                if pref_name:
                                    src_name = s.get("source_name") or ""
                                    if src_name.lower().startswith(pref_name_lower) and (not pref_res_lower or res.lower() == pref_res_lower):
                                        pref_boost = -100

                                q_key = quality_preference_key(res, target_quality)
                                return (pref_boost, sub_rank, audio_penalty, q_key, prio)

                            sorted_cached = sorted(cached_streams, key=_cached_sort_key)
                            candidates = []
                            for s in sorted_cached:
                                if s not in candidates:
                                    candidates.append(s)

                            # Bounded pre-flight check (Max 3 candidate probes)
                            selected = None
                            now = time.time()
                            for cand in candidates[:3]:
                                validated_at = float(cand.get("validated_at") or 0)
                                if (now - validated_at) < 30:
                                    selected = cand
                                    break
                                if app_core.ping_stream_liveness(cand, timeout=0.8):
                                    cand["validated_at"] = now
                                    selected = cand
                                    break
                                else:
                                    app_core._prune_dead_stream(cur_key, cand.get("link") or cand.get("streamUrl"))

                            if not selected and candidates:
                                selected = candidates[0]

                            if selected:
                                src_name = selected.get("source_name", "Stream")
                                res = (selected, src_name, None, cached_streams)
                                if not ep_data:
                                    ep_data = app_core._get_cached_ep_data(cur_key)
                                with streams_mod._bg_lock:
                                    bg_alive = bool(streams_mod._bg_thread and streams_mod._bg_thread.is_alive())
                                if not bg_alive and ep_data:
                                    exclude_names = set()
                                    for s in cached_streams:
                                        if s.get("source_parent_name"):
                                            exclude_names.add(s["source_parent_name"])
                                        if s.get("source_name"):
                                            raw_sname = s["source_name"]
                                            base_name = re.sub(r"\s*(?:\(\s*)?\b(?:\d+p|\d+k|adaptive|source)\b.*$", "", raw_sname, flags=re.I).strip()
                                            if base_name:
                                                exclude_names.add(base_name)
                                            exclude_names.add(raw_sname.split(" (")[0].strip())
                                        sources_total = len(ep_data.get("episode", {}).get("sourceUrls", []))
                                        if len(exclude_names) < sources_total:
                                            app_core.start_bg_resolve(ep_data, exclude_names, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=target_quality)
                                        else:
                                            app_core.info("Finding a playable stream...")

                    # Fallback: fetch episode stream if no cached stream was selected or no cached streams available
                    if res is None:
                        res = app_core.fetch_episode_stream(
                            ms.show_id,
                            ms.current_ep,
                            ttype,
                            cfg.get("quality", "best"),
                            provider_id=provider_id,
                            ep_data=ep_data,
                        )
                        if ep_data:
                            app_core._set_cached_ep_data(ep_data, (ms.show_id, ms.current_ep, ttype, provider_id, target_quality))

                if res:
                    ms.selected_stream, first_source_name, _, resolved_streams = res
                    app_core._extend_streams(resolved_streams, key=(ms.show_id, ms.current_ep, ttype, provider_id, target_quality))
        if not getattr(args, 'download', False) and not getattr(args, 'print_url', False) and not (getattr(args, 'sources', False) and not ui.initial_sources_prompted):
            stream_name = ms.selected_stream.get("source_name", first_source_name or "Stream") if ms.selected_stream else (first_source_name or "Stream")
            resolution = ms.selected_stream.get("resolution", "") if ms.selected_stream else ""
            res_str = f" ({resolution})" if resolution else ""

            aniskip_enabled = getattr(args, "aniskip", None)
            if aniskip_enabled is None:
                aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

            if aniskip_enabled:
                action_show = ui.ui_show_ctx or {}
                mal_id = app_core.get_show_mal_id(action_show)
                ep_num = episode_progress_number(ms.current_ep)
                if mal_id:
                    if aniskip_future is not None:
                        try:
                            skips = aniskip_future.result(timeout=4.0)
                        except Exception:
                            skips = []
                        if _skip_pool:
                            try:
                                _skip_pool.shutdown(wait=False)
                            except Exception:
                                pass
                    else:
                        from ..media.aniskip import fetch_skip_times
                        skips = fetch_skip_times(mal_id, ep_num)
                    if skips:
                        from ..ui.player_screen import _fmt_time
                        skip_parts = [f"{s['label']} ({_fmt_time(s['start'])} → {_fmt_time(s['end'])})" for s in skips]
                        skip_summary = " · ".join(skip_parts)
                        skip_msg = f"\033[38;5;120m✔ AniSkip: {skip_summary}\033[0m"
                    else:
                        skip_msg = f"\033[38;5;244m• AniSkip: No skip times found for EP {ep_num}\033[0m"
                else:
                    skip_msg = "\033[38;5;244m• AniSkip: No MAL ID available for this title\033[0m"

                if skip_msg not in _player_ui_state["status_lines"]:
                    if _player_ui_state["status_lines"] and _player_ui_state["status_lines"][-1] != "":
                        _player_ui_state["status_lines"].append("")
                    _player_ui_state["status_lines"].append(skip_msg)
                    try:
                        from ..ui.player_screen import render as _render
                        _render(poster_manager=getattr(app_core, "_poster_manager", None), ui=ui)
                    except Exception:
                        pass

            if app_core.is_termux() and aniskip_enabled:
                if not cfg.get("aniskip_android_hint_dismissed", False) and not getattr(ui, "aniskip_android_hint_shown", False):
                    ui.aniskip_android_hint_shown = True
                    try:
                        from ..ui.player_screen import stop_loading_ticker
                        stop_loading_ticker()
                    except Exception:
                        pass

                    if not getattr(flags, "plain_mode", False) and sys.stdin.isatty():
                        sys.stdout.write("\033[?1049h\033[2J\033[H\033[?25h")
                        sys.stdout.flush()

                    orig_termios = None
                    try:
                        if sys.stdin.isatty():
                            import termios
                            orig_termios = termios.tcgetattr(sys.stdin.fileno())
                            from ..ui.display import _INITIAL_TERMIOS_ATTRS
                            if _INITIAL_TERMIOS_ATTRS is not None:
                                termios.tcflush(sys.stdin.fileno(), termios.TCIFLUSH)
                                termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, _INITIAL_TERMIOS_ATTRS)
                    except Exception:
                        pass

                    print("\033[1;36mAniSkip on Android (MPV)\033[0m\n")
                    print("  \033[38;5;250m• Auto-skip is not supported via Android intents.\033[0m")
                    print("  \033[38;5;250m• To view chapter marks on the seekbar, add this line to /storage/emulated/0/Mpv/mpv.conf:\033[0m")
                    print("    \033[1;33mchapters-file=/storage/emulated/0/Mpv/chapters.txt\033[0m\n")

                    try:
                        choice = input("\033[1;97m[y = Don't show again, n = Disable AniSkip, s = Skip for now] (s): \033[0m").strip().lower()
                    except (EOFError, KeyboardInterrupt):
                        choice = "s"

                    if choice in ("y", "yes"):
                        cfg["aniskip_android_hint_dismissed"] = True
                        app_core.save_config(cfg)
                    elif choice in ("n", "no"):
                        cfg["aniskip"] = False
                        cfg["aniskip_enabled"] = False
                        aniskip_enabled = False
                        app_core.save_config(cfg)

                    try:
                        if sys.stdin.isatty() and orig_termios is not None:
                            import termios
                            termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, orig_termios)
                    except Exception:
                        pass

                    if not getattr(flags, "plain_mode", False) and sys.stdin.isatty():
                        sys.stdout.write("\033[2J\033[H\033[?25l")
                        sys.stdout.flush()
                    try:
                        from ..ui.player_screen import render as _render
                        _render(poster_manager=getattr(app_core, "_poster_manager", None), ui=ui)
                    except Exception:
                        pass

                    if not is_download_mode:
                        for sec in (3, 2, 1):
                            _player_ui_state["countdown_message"] = f"\033[1;92m▶ Starting '{stream_name}'{res_str} in {sec}s ...\033[0m"
                            try:
                                from ..ui.player_screen import render as _render
                                _render(poster_manager=getattr(app_core, "_poster_manager", None), ui=ui)
                            except Exception:
                                pass
                            time.sleep(1)
                        _player_ui_state["countdown_message"] = ""


        if getattr(args, "sources", False) and not ui.initial_sources_prompted:
            ui.initial_sources_prompted = True
            if first_source_name is not None and first_source_name != "Local File" and not getattr(ms, "_is_downloads", False):
                app_core.start_bg_resolve(ep_data, {first_source_name}, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=quality)
            app_core._exit_player_screen()
            return "MIRRORS"

        if ms.selected_stream is None:
            app_core._exit_player_screen()
            playback_mod._clear_episode_source_state(ms)
            p_name = (ui.ui_show_ctx.get("_provider_name") or (ui.ui_show_ctx.get("_provider") or "").title() or "this provider") if ui.ui_show_ctx else "this provider"
            ep_label = playback_mod._display_episode_label(ui.ui_show_ctx, ms.current_ep, ttype)
            if getattr(ms, "_is_downloads", False) and not is_download_mode:
                msg = f"✘ Episode {playback_mod._fmt_ep(ep_label)} is not downloaded locally."
            elif ttype == "dub":
                msg = f"No DUB stream available for {playback_mod._fmt_ep(ep_label)} on {p_name}."
            else:
                msg = f"No stream available for {playback_mod._fmt_ep(ep_label)} on {p_name}."
            app_core.set_action_feedback(
                ui.ui_show_ctx,
                msg,
            )
            if is_download_mode:
                args.download = False
            return "DETAILS"

    if args.print_url:
        app_core._exit_player_screen(close_alt=True)
        print(ms.selected_stream["link"])
        audio_url = ms.selected_stream.get("audio_url", "")
        if audio_url:
            print(f"Audio: {audio_url}")
        sub_url = ms.selected_stream.get("subtitle_url", "")
        if sub_url:
            print(f"Subtitle: {sub_url}")
        subtitles = ms.selected_stream.get("subtitles") or ms.selected_stream.get("vtt") or []
        if isinstance(subtitles, list) and len(subtitles) > 1:
            for sub in subtitles:
                s_url = sub.get("url") or sub.get("file")
                s_lbl = sub.get("label", "Unknown")
                if s_url and s_url != sub_url:
                    print(f"Subtitle ({s_lbl}): {s_url}")
        ref = ms.selected_stream.get("referer", "")
        if ref:
            print(f"Referer: {ref}")
        return "QUIT"


    if getattr(args, 'download', False):
        app_core._exit_player_screen(close_alt=True)

        exclude_sources = set()
        download_ok = False
        downloader_choice = getattr(args, "downloader", cfg.get("downloader", "auto"))

        extra_args = getattr(args, "extra_args", [])
        if extra_args and extra_args[0] == "--":
            extra_args = extra_args[1:]

        while True:
            download_ok = app_core.download_episode(
                ms.show_title, current_ep_label, ms.selected_stream,
                cfg.get("download_dir", ""), downloader=downloader_choice,
                extra_args=extra_args,
                download_sub_langs=getattr(args, "download_sub_langs", None),
                download_audio_langs=getattr(args, "download_audio_langs", None),
                subtitle_mode=getattr(args, "subtitle_mode", None),
            )
            if download_ok:
                try:
                    from media_cli.core.storage import load_downloads_db, save_downloads_db
                    from media_cli.media.download import sanitize_filename
                    db = load_downloads_db()
                    title = ms.show_title
                    folder_key = sanitize_filename(title) or title
                    if folder_key not in db["shows"]:
                        db["shows"][folder_key] = {"episodes": []}

                    def build_offline_metadata(s):
                        meta = {}
                        keys_to_keep = [
                            "id", "_id", "provider_id", "name", "englishName", "nativeName", "altNames",
                            "thumbnail", "description", "type", "format",
                            "status", "season", "airedStart", "airedEnd", "startDate",
                            "endDate", "score", "genres", "tags", "aniListId", "malId",
                            "_display_name", "_display_english_name", "_anilist_list",
                            "_anilist_progress", "_anilist_score", "anilistMatch",
                            "originalEpisodeCount", "episodeCount", "_provider", "provider",
                            "_provider_name", "_provider_data", "_episode_ids", "_episode_labels",
                            "_episode_ids_by_ttype", "availableEpisodes", "availableEpisodesDetail",
                            "_local_progress", "_local_episode_label", "watched_episodes"
                        ]
                        for k in keys_to_keep:
                            if k in s:
                                meta[k] = s[k]
                        if "originalEpisodeCount" not in meta and "episodeCount" in s:
                            meta["originalEpisodeCount"] = s["episodeCount"]
                        return meta

                    db["shows"][folder_key]["metadata"] = build_offline_metadata(s_ctx)
                    ep_str = str(ms.current_ep)
                    if ep_str not in db["shows"][folder_key]["episodes"]:
                        db["shows"][folder_key]["episodes"].append(ep_str)
                    save_downloads_db(db)
                except Exception:
                    pass
                break

            exclude_sources.add(first_source_name)
            print(f"{YELLOW}Download failed on mirror '{first_source_name}'. Trying next mirror...{RESET}")

            res = app_core.fetch_episode_stream(
                ms.show_id,
                ms.current_ep,
                ttype,
                cfg.get("quality", "best"),
                provider_id=provider_id,
                exclude_sources=exclude_sources,
                ep_data=ep_data,
            )
            if not res:
                break
            ms.selected_stream, first_source_name, _, streams = res
            app_core._extend_streams(streams)

        if not download_ok:
            print(f"\n{RED}All mirrors failed for EP {current_ep_label}. Skipping to next episode...{RESET}")

        download_batch_end = ms.download_batch_end if ms.download_batch_end is not None else current_ep_label
        try:
            is_less = int(float(str(download_batch_end))) > int(float(str(current_ep_label)))
        except ValueError:
            is_less = ms.current_ep_index < (ms.total_eps - 1)

        if is_less and ms.current_ep_index + 1 < ms.total_eps:
            ms.current_ep_index += 1
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"
        else:
            print(f"\n{GREEN}Downloads complete.{RESET}")
            if download_ok:
                app_core.set_action_feedback(ui.ui_show_ctx, f"✔ Downloaded {playback_mod._fmt_ep(current_ep_label)}")
                if getattr(ms, "_is_downloads", False):
                    new_f = app_core.find_offline_file_for_episode(ms.show_title, current_ep_label, cfg)
                    if new_f:
                        ms._download_files[str(ms.current_ep)] = new_f
                        ms._download_files[str(current_ep_label)] = new_f
            else:
                app_core.set_action_feedback(ui.ui_show_ctx, f"✘ Download failed for {playback_mod._fmt_ep(current_ep_label)}")
            args.download = False
            if hasattr(args, "_from_action_menu_download"):
                args._from_action_menu_download = False
            ms.selected_stream = None
            app_core._clear_streams()
            if getattr(ms, "_binge_auto_downloading", False):
                ms._binge_auto_downloading = False
                if download_ok:
                    return "PLAY"
            if getattr(flags, "plain_mode", False) or not sys.stdin.isatty() or getattr(args, "_cli_episode_passed", False):
                return "QUIT"
            time.sleep(1.0)
            return "DETAILS"



    # ── Launch player ─────────────────────────────────────────────
    def fetch_cb(ep_num):
        if isinstance(ep_num, int) and 1 <= ep_num <= len(episode_ids):
            ep_idx = ep_num - 1
        else:
            ep_idx = episode_index_for_id(
                episode_ids, ep_num, labels=ui.ui_show_ctx.get("_episode_labels")
            )
        if ep_idx is None:
            return None

        target_ep = episode_id_at(episode_ids, ep_idx)
        try:
            app_core._ipc_player.prefetched_ep_id = str(target_ep)
        except Exception:
            pass
        if getattr(ms, "_is_downloads", False):
            filepath = ms._download_files.get(str(target_ep))
            if filepath:
                return ({"link": filepath, "resolution": "Local", "is_local": True}, "Local File", {"is_local": True, "filepath": filepath}, [])
            return None

        if aniskip_enabled and mal_id:
            from concurrent.futures import ThreadPoolExecutor

            from ..media.aniskip import fetch_skip_times
            with ThreadPoolExecutor(max_workers=2) as pool:
                pool.submit(fetch_skip_times, mal_id, episode_progress_number(target_ep))
                fut_stream = pool.submit(
                    app_core.fetch_episode_stream,
                    ms.show_id,
                    target_ep,
                    ttype,
                    cfg.get("quality", "best"),
                    provider_id=provider_id,
                )
                return fut_stream.result()

        return app_core.fetch_episode_stream(
            ms.show_id,
            target_ep,
            ttype,
            cfg.get("quality", "best"),
            provider_id=provider_id,
        )

    is_binge = args.binge or cfg.get("binge", False)

    if app_core.is_termux():
        player = args.player or cfg.get("player")
        if player == "mpvrex":
            player = "rex"
        if not player:
            if app_core.pkg_installed("app.gyrolet.mpvrx") and not app_core.pkg_installed("is.xyz.mpv"):
                player = "mpvrx"
            elif app_core.pkg_installed("xyz.mpv.rex") and not app_core.pkg_installed("is.xyz.mpv"):
                player = "rex"
            else:
                player = "mpv"
        elif player == "mpv" and not args.player and not cfg.get("player"):
            if not app_core.pkg_installed("is.xyz.mpv"):
                if app_core.pkg_installed("app.gyrolet.mpvrx"):
                    player = "mpvrx"
                elif app_core.pkg_installed("xyz.mpv.rex"):
                    player = "rex"

        aniskip_enabled = getattr(args, "aniskip", None)
        if aniskip_enabled is None:
            aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

        action_show = ui.ui_show_ctx or {}
        mal_id = app_core.get_show_mal_id(action_show)

        trigger_background_auto_download(action_show, ms.current_ep_index, episode_ids, ttype, cfg, ms)

        result = app_core.play_android(
            ms.show_title,
            current_ep_label,
            ms.selected_stream,
            fetch_cb,
            player,
            ms.total_eps,
            ms.show_id,
            is_binge,
            mal_id=mal_id,
            aniskip_enabled=aniskip_enabled,
            action_show=action_show,
            ttype=ttype,
            cfg=cfg,
            ep_index=ms.current_ep_index,
        )

        if first_source_name is not None and first_source_name != "Local File" and not getattr(ms, "_is_downloads", False):
            exclude = {first_source_name}
            app_core.start_bg_resolve(ep_data, exclude, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=quality)

        if result == "NEXT" and ms.current_ep_index + 1 < ms.total_eps:
            ms.current_ep_index += 1
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"

        elif result == "PREV" and ms.current_ep_index > 0:
            ms.current_ep_index -= 1
            ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
            ms.selected_stream = None
            app_core._clear_streams()
            return "PLAY"

        if result:
            app_core.touch_history(action_show, ttype)
            cur_resume = app_core.get_resume_time(ms.show_id, ms.current_ep) or 0
            if cur_resume <= 0:
                app_core.save_resume_time(ms.show_id, ms.current_ep, 1)

        app_core._exit_player_screen()
        return "DETAILS"

    else:
        if first_source_name is not None and first_source_name != "Local File" and not getattr(ms, "_is_downloads", False):
            exclude = {first_source_name}
            app_core.start_bg_resolve(ep_data, exclude, show_id=ms.show_id, ep=ms.current_ep, ttype=ttype, provider_id=provider_id, quality=quality)

        next_episode = episode_id_at(episode_ids, ms.current_ep_index + 1) if ms.current_ep_index + 1 < ms.total_eps else None

        aniskip_enabled = getattr(args, "aniskip", None)
        if aniskip_enabled is None:
            aniskip_enabled = cfg.get("aniskip", cfg.get("aniskip_enabled", True))

        aniskip_auto = getattr(args, "auto_skip", None)
        if aniskip_auto is None:
            aniskip_auto = cfg.get("auto_skip", cfg.get("aniskip_auto", True))


        action_show = ui.ui_show_ctx or {}
        mal_id = app_core.get_show_mal_id(action_show)

        trigger_background_auto_download(action_show, ms.current_ep_index, episode_ids, ttype, cfg, ms)

        start_time_override = getattr(ms, "pending_resume_time", None)
        ms.pending_resume_time = None

        result, percent, time_pos, duration, played_seconds = app_core.play_desktop(
            ms.show_title, current_ep_label, ms.selected_stream, fetch_cb,
            ms.total_eps, is_binge, ms.show_id, ms.pending_osd_msg,
            ms.current_ep_index, next_episode,
            mal_id=mal_id, aniskip_enabled=aniskip_enabled, aniskip_auto=aniskip_auto,
            start_time_override=start_time_override,
        )

        ms.pending_osd_msg = ""
        app_core._exit_player_screen()

        if result in ("ERROR", "NEXT_MIRROR"):
            is_manual_switch = (result == "NEXT_MIRROR")
            failed_stream = ms.selected_stream
            failed_name = (failed_stream.get("source_name") or "selected mirror") if failed_stream else "selected mirror"
            failed_name.split(" (")[0].strip() if failed_name else ""

            if is_manual_switch:
                app_core.info(f"Switching from '{failed_name}' to next mirror...")
                app_core._ipc_player.send_cmd("show-text", "Switching to next mirror...", 3000)
            else:
                app_core.warn(f"Playback failed on '{failed_name}'. Attempting fallback to next mirror...")
                app_core._ipc_player.send_cmd("show-text", f"⚠ {failed_name} failed\nFinding alternate mirror...", 10000)

            # Preserve playback position so fallback mirror resumes right where the stream stalled/switched
            if time_pos > 2:
                ms.pending_resume_time = max(0.0, time_pos - 1.0)
                app_core.save_resume_time(ms.show_id, ms.current_ep, int(time_pos))
                app_core.save_resume_time(ms.show_id, current_ep_label, int(time_pos))

            cur_key = (ms.show_id, ms.current_ep, ttype, provider_id, quality)
            if failed_stream and not is_manual_switch:
                app_core._prune_dead_stream(cur_key, failed_stream.get("link") or failed_stream.get("streamUrl"))

            # Track all failed/skipped mirrors across fallback attempts for this episode
            failed_mirrors = getattr(ms, "_failed_mirrors", None)
            if failed_mirrors is None:
                failed_mirrors = set()
            if failed_name:
                failed_mirrors.add(failed_name)
            if failed_stream and (failed_stream.get("link") or failed_stream.get("streamUrl")):
                failed_mirrors.add(failed_stream.get("link") or failed_stream.get("streamUrl"))
            ms._failed_mirrors = failed_mirrors

            cached_ep_data = app_core._get_cached_ep_data(cur_key)
            cached_streams = app_core._stream_snapshot(ms.show_id, ms.current_ep, ttype, provider_id, quality, cached_ep_data)
            remaining = [
                s for s in cached_streams
                if (s.get("link") or s.get("streamUrl")) not in failed_mirrors
                and (s.get("source_name") or "") not in failed_mirrors
                and (s.get("source_parent_name") or "") not in failed_mirrors
            ]

            next_stream = None
            if remaining:
                from ..media.sources import quality_preference_key
                target_quality = getattr(args, "quality", None) or cfg.get("quality", "best")

                def _failover_sort_key(s):
                    sname = (s.get("source_name") or "").lower()
                    res = s.get("resolution") or sname
                    is_hard = "hardsub" in sname or "hard-sub" in sname or "hard sub" in sname
                    is_soft = "softsub" in sname or "all sub" in sname or "multi sub" in sname
                    is_donghua = str(provider_id or "").lower() in {"animexin", "lucifer", "animekhor"}
                    if is_donghua:
                        sub_rank = 0 if is_hard else (2 if is_soft else 1)
                    else:
                        sub_rank = 0 if is_soft else (2 if is_hard else 1)
                    q_key = quality_preference_key(res, target_quality)
                    return (sub_rank, q_key, s.get("source_priority", 4))

                sorted_remaining = sorted(remaining, key=_failover_sort_key)
                for cand in sorted_remaining:
                    if app_core.ping_stream_liveness(cand, timeout=0.8):
                        next_stream = cand
                        break
                if not next_stream and sorted_remaining:
                    next_stream = sorted_remaining[0]

            # If no cached stream is viable, dynamically resolve the next mirror from the provider
            if not next_stream:
                if not ep_data:
                    ep_data = app_core._get_cached_ep_data(cur_key)
                fallback_res = app_core.fetch_episode_stream(
                    ms.show_id,
                    ms.current_ep,
                    ttype,
                    cfg.get("quality", "best"),
                    provider_id=provider_id,
                    exclude_sources=failed_mirrors,
                    ep_data=ep_data,
                )
                if fallback_res:
                    next_stream, fb_src_name, _, fb_streams = fallback_res
                    app_core._extend_streams(fb_streams, key=cur_key)

            if next_stream:
                next_name = next_stream.get("source_name", "alternate mirror")
                if is_manual_switch:
                    switch_msg = f"Switched from {failed_name} • Loading mirror '{next_name}'..."
                    ms.pending_osd_msg = f"Switched to {next_name}"
                    app_core._ipc_player.send_cmd("show-text", f"Loading {next_name}...", 3000)
                else:
                    switch_msg = f"Playback failed on {failed_name} • Trying fallback mirror '{next_name}'..."
                    ms.pending_osd_msg = f"⚠ Switched to {next_name} ({failed_name} failed)"
                    app_core._ipc_player.send_cmd("show-text", f"⚠ {failed_name} failed\nLoading {next_name}...", 5000)
                app_core.info(switch_msg)
                ms.selected_stream = next_stream
                app_core.set_action_feedback(ui.ui_show_ctx, switch_msg)
                return "PLAY"

            # All mirrors exhausted
            app_core._ipc_player.quit()
            ms._failed_mirrors = set()
            if is_manual_switch:
                app_core.set_action_feedback(
                    ui.ui_show_ctx,
                    f"No other alternate mirrors available after {failed_name}."
                )
            else:
                app_core.set_action_feedback(
                    ui.ui_show_ctx,
                    f"Playback failed on {failed_name} and no alternate mirrors were available."
                )
            return "DETAILS"

        # Smart Auto-Scrobble & Timestamping (80% OR <150s remaining)
        start_time = getattr(app_core._ipc_player, "resume_time", 0) or 0
        auto_scrobbled = playback_looks_complete(
            result, percent, time_pos, duration, played_seconds, start_time=start_time
        )
        pending_completion = app_core.get_pending_completion(ms.show_id)

        if (pending_completion
                and str(pending_completion.get("next_episode")) == str(ms.current_ep)
                and str(pending_completion.get("episode")) != str(ms.current_ep)
                and time_pos >= 120):
            pending_ep = pending_completion.get("episode")
            pending_progress = int(pending_completion.get("progress") or 0)
            sync_pending = resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx)

            if sync_pending:
                tkn = cfg.get("anilist_token")
                if tkn and pending_progress:
                    show_ctx = ui.ui_show_ctx
                    new_status = tracking_status_for_progress(show_ctx, pending_progress)

                    def _pending_sync_success(ep=pending_progress, ctx=show_ctx):
                        app_core.set_action_feedback(ctx, f"✔ Synced EP {ep} to AniList")
                    def _pending_sync_failure(ctx=show_ctx):
                        app_core.set_action_feedback(ctx, "Saved offline • AniList sync pending")

                    queued = app_core.queue_anilist_progress(
                        tkn, ms.show_title, pending_progress, al_id,
                        show_ctx, ttype, new_status,
                        on_success=_pending_sync_success,
                        on_failure=_pending_sync_failure,
                        pending_completion={
                            "show_id": str(ms.show_id),
                            "episode": str(pending_ep),
                        },
                    )
                    if queued:
                        app_core.set_action_feedback(show_ctx, f"Sync queued: EP {pending_progress}")
                else:
                    sync_pending = False

            if not sync_pending:
                app_core.save_resume_time(ms.show_id, pending_ep, 0)
                app_core.clear_pending_completion(ms.show_id)

        should_update_history = (
            not getattr(ms, "_is_downloads", False)
            and playback_updates_history(
                result, percent, time_pos, duration, played_seconds, start_time=start_time
            )
        )
        if should_update_history:
            app_core.save_history(ui.ui_show_ctx, ms.current_ep, ttype)
        elif not getattr(ms, "_is_downloads", False) and played_seconds >= 5:
            app_core.touch_history(ui.ui_show_ctx, ttype)

        if getattr(ms, "_is_downloads", False) and playback_updates_history(
            result, percent, time_pos, duration, played_seconds, start_time=start_time
        ):
            try:
                from media_cli.core.storage import update_offline_watch_status
                folder_name = ui.ui_show_ctx.get("_folder_name", ms.show_title)
                if update_offline_watch_status(folder_name, ms.current_ep):
                    watched = ui.ui_show_ctx.get("watched_episodes", [])
                    if str(ms.current_ep) not in watched:
                        watched.append(str(ms.current_ep))
                    ui.ui_show_ctx["watched_episodes"] = watched

                if getattr(ms, "auto_delete_watched", False):
                    buf = int(getattr(ms, "auto_delete_buffer", 1))
                    watched_list = ui.ui_show_ctx.get("watched_episodes", [])
                    dl_files = getattr(ms, "_download_files", {})
                    watched_dl = []
                    for eid in episode_ids:
                        eid_lbl = playback_mod._display_episode_label(ui.ui_show_ctx, eid, ttype)
                        if (str(eid) in watched_list or str(eid_lbl) in watched_list) and (str(eid) in dl_files or str(eid_lbl) in dl_files):
                            watched_dl.append((eid, eid_lbl))

                    if buf >= 0 and len(watched_dl) > buf:
                        to_del = watched_dl[:-buf] if buf > 0 else watched_dl
                        deleted_labels = []
                        for deid, dlbl in to_del:
                            if app_core.delete_offline_episode(folder_name, dlbl, cfg) or app_core.delete_offline_episode(folder_name, str(deid), cfg):
                                dl_files.pop(str(deid), None)
                                dl_files.pop(str(dlbl), None)
                                dl_files.pop(deid, None)
                                dl_files.pop(dlbl, None)
                                dl_eps = ui.ui_show_ctx.get("_downloaded_episodes", [])
                                if str(dlbl) in dl_eps:
                                    dl_eps.remove(str(dlbl))
                                if str(deid) in dl_eps:
                                    dl_eps.remove(str(deid))
                                deleted_labels.append(str(dlbl))
                        if deleted_labels:
                            app_core.set_action_feedback(
                                ui.ui_show_ctx,
                                f"Auto-deleted watched EP {', '.join(deleted_labels)}"
                            )
            except Exception:
                pass

        if auto_scrobbled or (duration > 0 and time_pos >= duration - 30):
            app_core.save_resume_time(ms.show_id, ms.current_ep, 0)
        elif time_pos > 30:
            app_core.save_resume_time(ms.show_id, ms.current_ep, time_pos)
        else:
            app_core.save_resume_time(ms.show_id, ms.current_ep, 0)

        if result == "QUIT" or result == "EOF":
            if auto_scrobbled and resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx):
                tkn = cfg.get("anilist_token")
                if tkn:
                    show_ctx = ui.ui_show_ctx
                    progress_ep = episode_progress_number(ms.current_ep, ms.current_ep_index + 1)
                    new_status = tracking_status_for_progress(show_ctx, progress_ep)
                    queued = app_core.queue_anilist_progress(
                        tkn, ms.show_title, progress_ep, al_id,
                        show_ctx, ttype, new_status,
                        on_success=lambda ep=progress_ep, ctx=show_ctx:
                            app_core.set_action_feedback(ctx, f"✔ Synced EP {ep} to AniList"),
                        on_failure=lambda ctx=show_ctx:
                            app_core.set_action_feedback(ctx, "Saved offline • AniList sync pending"),
                    )
                    if queued:
                        app_core.set_action_feedback(show_ctx, f"Sync queued: EP {progress_ep}")

            if result == "EOF" and (args.binge or cfg.get("binge")) and auto_scrobbled:
                if ms.current_ep_index + 1 < ms.total_eps:
                    next_ep = episode_id_at(episode_ids, ms.current_ep_index + 1)
                    if getattr(ms, "_is_downloads", False):
                        lbl_next = playback_mod._display_episode_label(ui.ui_show_ctx, next_ep, ttype)
                        dl_files = getattr(ms, "_download_files", {})
                        is_next_dl = str(next_ep) in dl_files or str(lbl_next) in dl_files or bool(app_core.find_offline_file_for_episode(ms.show_title, lbl_next, cfg))
                        if not is_next_dl:
                            if getattr(ms, "auto_download_next", False):
                                if app_core.is_online():
                                    ms.current_ep_index += 1
                                    ms.current_ep = next_ep
                                    ms.selected_stream = None
                                    ms._failed_mirrors = set()
                                    app_core._clear_streams()
                                    ms._binge_auto_downloading = True
                                    args.download = True
                                    app_core._ipc_player.quit()
                                    print(f"\n\033[1;36mAuto-downloading next episode ({lbl_next}) for binge playback…\033[0m")
                                    return "PLAY"
                                else:
                                    app_core._ipc_player.quit()
                                    app_core.set_action_feedback(
                                        ui.ui_show_ctx,
                                        f"Offline: cannot download EP {lbl_next}. Reached end of downloaded episodes."
                                    )
                                    return "DETAILS"
                            else:
                                app_core._ipc_player.quit()
                                app_core.set_action_feedback(
                                    ui.ui_show_ctx,
                                    "Reached end of downloaded episodes."
                                )
                                return "DETAILS"

                    if flags.incognito_mode:
                        ms.pending_osd_msg = None
                    else:
                        ms.pending_osd_msg = marked_watched_osd(
                            ms.current_ep,
                            resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx)
                        )

                    ms.current_ep_index += 1
                    ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
                    ms.selected_stream = None
                    ms._failed_mirrors = set()
                    app_core._clear_streams()
                    return "PLAY"
                else:
                    app_core._ipc_player.quit()
                    print(f"\n{GREEN}Finished the last episode.{RESET}")
                    return "QUIT"
            else:
                app_core._ipc_player.quit()
                if not auto_scrobbled:
                    if played_seconds < 5.0 and duration <= 0:
                        ep_fmt = playback_mod._fmt_ep(
                            current_ep_label or clean_episode_identifier(str(ms.current_ep)) or ms.current_ep
                        )
                        app_core.set_action_feedback(
                            ui.ui_show_ctx,
                            f"Playback failed or was interrupted on {ep_fmt}."
                        )
                    elif time_pos > 0:
                        next_ep = episode_id_at(episode_ids, ms.current_ep_index + 1) if (ms.current_ep_index is not None and ms.current_ep_index + 1 < ms.total_eps) else ms.current_ep
                        progress_ep = episode_progress_number(ms.current_ep, (ms.current_ep_index or 0) + 1)
                        app_core.save_pending_completion(
                            ms.show_id, ms.current_ep, progress_ep,
                            next_ep, time_pos, duration
                        )
                        app_core.save_resume_time(ms.show_id, ms.current_ep, time_pos)
                if auto_scrobbled:
                    ms.current_ep_index = None
                    ms.current_ep = None
                return "DETAILS"

        elif result == "NEXT":
            if ms.current_ep_index + 1 < ms.total_eps:
                next_ep = episode_id_at(episode_ids, ms.current_ep_index + 1)
                if flags.incognito_mode:
                    ms.pending_osd_msg = None
                elif auto_scrobbled and resolve_tracking_fn(ui.search_prev_state, args, cfg, ui.ui_show_ctx):
                    tkn = cfg.get("anilist_token")
                    if tkn:
                        show_ctx = ui.ui_show_ctx
                        progress_ep = episode_progress_number(ms.current_ep, ms.current_ep_index + 1)
                        new_status = tracking_status_for_progress(show_ctx, progress_ep)
                        queued = app_core.queue_anilist_progress(
                            tkn, ms.show_title, progress_ep, al_id,
                            show_ctx, ttype, new_status,
                            on_success=lambda ep=progress_ep, ctx=show_ctx:
                                app_core.set_action_feedback(ctx, f"✔ Synced EP {ep} to AniList"),
                            on_failure=lambda ctx=show_ctx:
                                app_core.set_action_feedback(ctx, "Saved offline • AniList sync pending"),
                        )
                        ms.pending_osd_msg = (
                            sync_queued_osd(ms.current_ep)
                            if queued else marked_watched_osd(ms.current_ep, False)
                        )
                elif not auto_scrobbled:
                    progress_ep = episode_progress_number(ms.current_ep, ms.current_ep_index + 1)
                    app_core.save_pending_completion(ms.show_id, ms.current_ep, progress_ep, next_ep, time_pos, duration)
                    if time_pos > 0:
                        app_core.save_resume_time(ms.show_id, ms.current_ep, time_pos)
                    ms.pending_osd_msg = pending_completion_osd(ms.current_ep, next_ep)

                ms.current_ep_index += 1
                ms.current_ep = next_ep
                ms.selected_stream = None
                app_core._clear_streams()
                return "PLAY"
            else:
                app_core._ipc_player.quit()
                print(f"\n{GREEN}Finished the last episode.{RESET}")
                return "QUIT"

        elif result == "PREV":
            if ms.current_ep_index > 0:
                ms.current_ep_index -= 1
                ms.current_ep = episode_id_at(episode_ids, ms.current_ep_index)
                ms.selected_stream = None
                app_core._clear_streams()
                return "PLAY"
            else:
                app_core._ipc_player.quit()
                return "DETAILS"
        else:
            app_core._ipc_player.quit()
            return "DETAILS"
