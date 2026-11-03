import logging
import subprocess

from ..core.api import ProviderDependencyError
from ..media.dash import generate_dash_mpd
from ..media.local_proxy import (
    cleanup_active_local_proxy,
    replace_active_local_proxy,
    start_local_content_server,
    start_local_dual_proxy,
    start_local_proxy,
)
from ..media.proxy_rules import proxy_filtered_headers
from ..media.urls import validate_optional_referer, validate_stream_url

log = logging.getLogger(__name__)

PLAYERS = {
    "mpv": ("is.xyz.mpv", "is.xyz.mpv.MPVActivity"),
    "rex": ("xyz.mpv.rex", None),
    "mpvrex": ("xyz.mpv.rex", None),
    "mpvrx": ("app.gyrolet.mpvrx", ".ui.player.PlayerActivity"),
    "vlc": ("org.videolan.vlc", None),
    "next": (
        "dev.anilbeesetti.nextplayer",
        ".feature.player.PlayerActivity",
    ),
}

_packages = None


class AndroidPreparationCancelled(Exception):
    """Preparation was cancelled before the Android player was launched."""


def _info(message):
    return None
def _ok(message):
    return None
def _warn(message):
    return None
def _error(message):
    return None


def configure_reporters(info, ok, warn, error):
    global _info, _ok, _warn, _error
    _info = info
    _ok = ok
    _warn = warn
    _error = error


def package_installed(package):
    global _packages
    if _packages is None:
        try:
            _packages = subprocess.run(
                ["pm", "list", "packages"],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            _packages = ""
    return f"package:{package}" in _packages


def play_android(
        title,
        episode,
        stream,
        fetch_callback,
        player="mpv",
        total_eps=1,
        show_id=None,
        is_binge=False,
        mal_id=None,
        aniskip_enabled=True,
        action_show=None,
        ttype="sub",
        cfg=None,
        ep_index=None,
        *,
        fetch_skip_times_fn=None,
        prepare_subtitles_fn=None,
        prepare_proxy_fn=None,
        launch_command_fn=None):
    del fetch_callback, total_eps, is_binge
    try:
        url = validate_stream_url(stream["link"])
        referer = validate_optional_referer(stream.get("referer", ""))
    except ValueError:
        _error("Player rejected an unsafe stream URL.")
        return False

    if launch_command_fn is None:
        from ..services.requirements import require_executable

        require_executable("am", "Android playback")

    from ..domain.titles import title_provider_key
    resume_provider_id = title_provider_key(action_show, default="")

    start_time = 0
    if show_id and player != "vlc":
        try:
            from ..core.storage import get_resume_time
            from ..domain.episodes import clean_episode_identifier
            cleaned_ep = clean_episode_identifier(str(episode))
            start_time = (
                get_resume_time(show_id, episode, resume_provider_id)
                or (get_resume_time(show_id, cleaned_ep, resume_provider_id) if cleaned_ep else 0)
            ) or 0
            if start_time > 35:
                start_time = max(0, start_time - 30)
            elif start_time > 10:
                start_time = max(0, start_time - 10)
            else:
                start_time = 0
        except Exception:
            start_time = 0

    duration_val = float(stream.get("duration") or stream.get("dash_duration") or 0.0)

    def handle_progress(sid, ep, t_pos, total_dur, is_complete):
        try:
            from ..core.storage import save_resume_time
            if is_complete:
                save_resume_time(sid, ep, 0, resume_provider_id)
                if action_show:
                    from ..domain.episodes import episode_progress_number
                    try:
                        prog_num = int(float(episode_progress_number(ep, (ep_index or 0) + 1)))
                    except Exception:
                        prog_num = None
                    try:
                        from ..core.storage import save_history
                        save_history(action_show, ep, ttype)
                    except Exception:
                        pass
                    if prog_num is not None:
                        try:
                            from ..domain.metadata import prepare_show_display_state
                            prepare_show_display_state(action_show, ttype)
                        except Exception:
                            pass
                        action_show["_local_progress"] = prog_num
                        action_show["_local_episode_label"] = str(ep)
                        if action_show.get("_anilist_progress") is not None:
                            action_show["_anilist_progress"] = max(
                                int(action_show.get("_anilist_progress") or 0), prog_num
                            )
                        action_show["_progress_authority"] = "LOCAL"
                    tkn = (cfg or {}).get("anilist_token")
                    if tkn:
                        try:
                            from ..app_core import get_show_anilist_id, queue_anilist_progress
                            al_id = get_show_anilist_id(action_show)
                            if al_id and prog_num is not None:
                                queue_anilist_progress(
                                    tkn, title, prog_num, al_id,
                                    action_show, ttype, "CURRENT",
                                )
                        except Exception:
                            pass

                    # Update offline watch status and handle auto-delete / auto-download
                    try:
                        from ..core.storage import update_offline_watch_status
                        from ..app_core import delete_offline_episode
                        folder_name = action_show.get("_folder_name", title)
                        if update_offline_watch_status(folder_name, ep):
                            watched = action_show.get("watched_episodes", [])
                            if str(ep) not in watched:
                                watched.append(str(ep))
                            action_show["watched_episodes"] = watched
                        if (cfg or {}).get("auto_delete_watched", False):
                            buf = int((cfg or {}).get("auto_delete_buffer", 1))
                            watched_list = action_show.get("watched_episodes", [])
                            eids = action_show.get("_episode_ids") or []
                            from ..app import playback_episodes as pmod
                            watched_dl = []
                            for eid in eids:
                                elbl = pmod._display_episode_label(action_show, eid, ttype)
                                if (str(eid) in watched_list or str(elbl) in watched_list):
                                    watched_dl.append((eid, elbl))
                            if buf >= 0 and len(watched_dl) > buf:
                                to_del = watched_dl[:-buf] if buf > 0 else watched_dl
                                for deid, dlbl in to_del:
                                    delete_offline_episode(folder_name, dlbl, cfg)
                                    delete_offline_episode(folder_name, str(deid), cfg)
                    except Exception:
                        pass

                    try:
                        from ..app.playback_player import auto_download_mode
                        auto_dl_enabled = auto_download_mode(cfg or {}, None) == "adaptive"
                    except Exception:
                        auto_dl_enabled = False
                    if auto_dl_enabled and action_show and ep_index is not None:
                        try:
                            from ..app.playback_player import trigger_background_auto_download
                            eids = action_show.get("_episode_ids") or []
                            from ..context import MachineState
                            dummy_ms = MachineState(show_title=title, auto_download_next="adaptive")
                            trigger_background_auto_download(action_show, ep_index, eids, ttype, cfg, dummy_ms, current_stream=stream)
                        except Exception:
                            pass
            else:
                save_resume_time(sid, ep, t_pos, resume_provider_id)
        except Exception:
            pass

    # Write AniSkip chapters for MPV on Android (always overwrites / clears if no skips)
    chapters_path = "/storage/emulated/0/Mpv/chapters.txt"
    try:
        from ..domain.episodes import episode_progress_number
        from ..media.aniskip import fetch_skip_times, generate_chapters_file
        from ..media.provider_skips import merge_skip_intervals, needs_skip_fallback, normalize_provider_skips
        skip_intervals = []
        if aniskip_enabled:
            skip_intervals = normalize_provider_skips(stream.get("skip_intervals"))
        if aniskip_enabled and mal_id and needs_skip_fallback(skip_intervals):
            ep_num = episode_progress_number(episode)
            fetch = fetch_skip_times_fn or fetch_skip_times
            skip_intervals = merge_skip_intervals(skip_intervals, fetch(mal_id, ep_num, episode_length=0.0))
            log.debug(
                "AniSkip Android chapter candidates: MAL=%s EP=%s intervals=%d duration_matching=False",
                mal_id, ep_num, len(skip_intervals),
            )
        generate_chapters_file(skip_intervals, chapters_path)
    except AndroidPreparationCancelled:
        return False
    except Exception:
        pass

    headers = proxy_filtered_headers(stream.get("headers", {}))
    package, activity = PLAYERS.get(player, PLAYERS["mpv"])
    from media_cli.domain.episodes import clean_episode_identifier, episode_label
    raw_ep = str(episode_label(episode)).strip()
    ep_str = clean_episode_identifier(raw_ep) or raw_ep

    if ep_str.lower() in ("movie", "full"):
        media_title = f"{title}"
    else:
        if ep_str and ep_str[0].isdigit():
            ep_str = f"EP {ep_str}"
        elif ep_str.lower() == "ova":
            ep_str = "OVA"
        elif ep_str.lower().startswith("ova "):
            ep_str = "OVA " + ep_str[4:]
        else:
            ep_str = ep_str.title()
        media_title = f"{title} - {ep_str}"
    if isinstance(stream.get("media_title"), str) and stream["media_title"].strip():
        from ..core.terminal import sanitize_terminal_text
        media_title = sanitize_terminal_text(stream["media_title"])
    proxy_server = None
    intent_type = "video/*"

    subtitles = stream.get("subtitles") or stream.get("vtt") or []
    if not subtitles and stream.get("subtitle_url"):
        subtitles = [{"label": "English", "url": stream["subtitle_url"], "default": True}]
    if subtitles:
        try:
            from ..media.subtitle_scorer import filter_subtitles
            prepare_subtitles = prepare_subtitles_fn or filter_subtitles
            subtitles = prepare_subtitles(
                subtitles,
                headers=headers,
                referer=referer,
                video_duration_s=float(stream.get("duration") or 0),
            )
        except AndroidPreparationCancelled:
            return False
        except Exception:
            pass

    def prepare_proxy(start, *args, **kwargs):
        if prepare_proxy_fn is not None:
            return prepare_proxy_fn(start, *args, **kwargs)
        return start(*args, **kwargs)

    hls_video_url = url
    hls_audio_url = stream.get("audio_url")
    if stream.get("split_video_url") and stream.get("split_audio_url"):
        hls_video_url = stream["split_video_url"]
        hls_audio_url = stream["split_audio_url"]

    cleanup_active_local_proxy()
    if stream.get("dash_video") and stream.get("dash_audio"):
        _info(f"{player}: preparing DASH video and audio...")
        try:
            manifest = generate_dash_mpd(
                stream["dash_video"],
                stream["dash_audio"],
                stream.get("dash_duration"),
            )
            url, proxy_server = prepare_proxy(
                start_local_content_server,
                manifest,
                "stream.mpd",
                "application/dash+xml",
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "application/dash+xml"
        except AndroidPreparationCancelled:
            return False
        except Exception as exc:
            _error(f"Could not prepare DASH stream: {exc}")
            return False
    elif hls_audio_url and stream.get("type") == "hls":
        _info(f"{player}: preparing video and audio...")
        try:
            bw = float(stream.get("split_bandwidth") or 2400)
            if bw < 100_000:
                bw *= 1000
            url, proxy_server = prepare_proxy(
                start_local_dual_proxy,
                hls_video_url,
                hls_audio_url,
                referer,
                headers,
                width=stream.get("split_width") or 1280,
                height=stream.get("split_height") or 720,
                bandwidth=int(bw),
                title=media_title,
                subtitles=subtitles,
                audio_tracks=stream.get("audio_tracks"),
                target_player=player,
                show_id=show_id,
                episode=episode,
                duration=duration_val,
                on_progress=handle_progress,
                flixcloud_w_payload=stream.get("flixcloud_w_payload"),
                flixcloud_xor_mask=stream.get("flixcloud_xor_mask"),
                **({"ip_version": 4} if stream.get("ip_version") == 4 else {}),
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "video/*"
        except AndroidPreparationCancelled:
            return False
        except Exception as exc:
            _error(f"Could not prepare split audio stream: {exc}")
            return False
    elif stream.get("dailymotion_video") and stream.get("dailymotion_audio"):
        _info(f"{player}: preparing Dailymotion video and audio...")
        try:
            bw = float(stream.get("dailymotion_bandwidth") or 2400)
            if bw < 100_000:
                bw *= 1000
            url, proxy_server = prepare_proxy(
                start_local_dual_proxy,
                stream["dailymotion_video"],
                stream["dailymotion_audio"],
                referer,
                headers,
                width=stream.get("dailymotion_width") or 1280,
                height=stream.get("dailymotion_height") or 720,
                bandwidth=int(bw),
                title=media_title,
                subtitles=subtitles,
                audio_tracks=stream.get("audio_tracks"),
                target_player=player,
                show_id=show_id,
                episode=episode,
                duration=duration_val,
                on_progress=handle_progress,
                **({"ip_version": 4} if stream.get("ip_version") == 4 else {}),
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "video/*"
        except AndroidPreparationCancelled:
            return False
        except Exception as exc:
            _error(f"Could not prepare Dailymotion stream: {exc}")
            return False
    elif not (
            stream.get("dash_video")
            or (hls_audio_url and stream.get("type") == "hls")
            or (stream.get("dailymotion_video") and stream.get("dailymotion_audio"))):
        _info(f"{player}: starting local HTTP proxy...")
        try:
            url, proxy_server = prepare_proxy(
                start_local_proxy,
                url,
                referer,
                headers,
                stream_type=stream.get("type", "mp4"),
                title=media_title,
                subtitles=subtitles,
                target_player=player,
                show_id=show_id,
                episode=episode,
                duration=duration_val,
                on_progress=handle_progress,
                **({"ip_version": 4} if stream.get("ip_version") == 4 else {}),
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "video/*"
        except AndroidPreparationCancelled:
            return False
        except Exception as exc:
            _error(f"Could not start local stream proxy: {exc}")
            return False

    _info(f"Opening {media_title} in {player}...")
    command = [
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        url,
        "-t",
        intent_type,
    ]
    if activity:
        command.extend(["-n", f"{package}/{activity}"])
    else:
        command.extend(["-p", package])
    command.extend([
        "--es",
        "title",
        media_title,
    ])
    if start_time > 0 and player in ("next", "mpv", "rex", "mpvrx", "mpvrex"):
        _info(f"Resuming {player} at {int(start_time // 60):02d}:{int(start_time % 60):02d}...")
        target_ms = int(start_time * 1000)
        command.extend([
            "--el", "position", str(target_ms),
            "--ei", "position", str(target_ms),
        ])
    hdr_list = []
    if referer:
        hdr_list.extend(["Referer", referer])
    ua = headers.get("User-Agent")
    if ua:
        hdr_list.extend(["User-Agent", ua])
    if hdr_list:
        command.extend(["--esa", "headers", ",".join(hdr_list)])

    launched = False
    launch_attempt = 0

    def launch(command):
        nonlocal launch_attempt
        launch_attempt += 1
        if launch_command_fn is not None:
            result = launch_command_fn(command)
        else:
            result = subprocess.run(command, capture_output=True, text=True)
        log.debug(
            "Android player launch result: player=%s attempt=%d returncode=%s starting_intent=%s stderr_present=%s",
            player, launch_attempt, getattr(result, "returncode", None),
            "Starting: Intent" in (getattr(result, "stdout", None) or ""),
            bool(getattr(result, "stderr", None)),
        )
        return result

    try:
        result = launch(command)
        # On Android 14+, termux-am outputs "Starting: Intent" to stdout upon
        # successfully dispatching the intent, but can return exit code 1
        # due to an AppOps null-pointer reflection exception in reading the return.
        stdout_str = result.stdout or ""
        if result.returncode == 0 or "Starting: Intent" in stdout_str:
            _ok(f"{player} opened.")
            launched = True
        elif activity:
            fallback_cmd = [
                "am",
                "start",
                "-a",
                "android.intent.action.VIEW",
                "-d",
                url,
                "-t",
                intent_type,
                "-p",
                package,
                "--es",
                "title",
                media_title,
            ]
            if start_time > 0 and player in ("next", "mpv", "rex", "mpvrx", "mpvrex"):
                target_ms = int(start_time * 1000)
                fallback_cmd.extend([
                    "--el", "position", str(target_ms),
                    "--ei", "position", str(target_ms),
                ])
            if hdr_list:
                fallback_cmd.extend(["--esa", "headers", ",".join(hdr_list)])
            r2 = launch(fallback_cmd)
            if r2.returncode == 0 or "Starting: Intent" in (r2.stdout or ""):
                _ok(f"{player} opened.")
                launched = True

        if not launched:
            _error(f"Could not open {player}.")
    except AndroidPreparationCancelled:
        return False
    except ProviderDependencyError:
        raise
    except Exception as exc:
        _error(f"Could not open {player}: {exc}")
    finally:
        if not launched and proxy_server is not None:
            cleanup_active_local_proxy()
    return launched
