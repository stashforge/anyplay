import subprocess

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

PLAYERS = {
    "mpv": ("is.xyz.mpv", "is.xyz.mpv.MPVActivity"),
    "mpvrex": ("xyz.mpv.rex", None),
    "vlc": ("org.videolan.vlc", None),
    "next": (
        "dev.anilbeesetti.nextplayer",
        ".feature.player.PlayerActivity",
    ),
}

_packages = None
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
        aniskip_enabled=True):
    del fetch_callback, total_eps, show_id, is_binge
    try:
        url = validate_stream_url(stream["link"])
        referer = validate_optional_referer(stream.get("referer", ""))
    except ValueError:
        _error("Player rejected an unsafe stream URL.")
        return False

    # Write AniSkip chapters for MPV on Android (always overwrites / clears if no skips)
    chapters_path = "/storage/emulated/0/Mpv/chapters.txt"
    try:
        from ..domain.episodes import episode_progress_number
        from ..media.aniskip import fetch_skip_times, generate_chapters_file
        skip_intervals = []
        if aniskip_enabled and mal_id:
            ep_num = episode_progress_number(episode)
            skip_intervals = fetch_skip_times(mal_id, ep_num)
        generate_chapters_file(skip_intervals, chapters_path)
    except Exception:
        pass

    headers = proxy_filtered_headers(stream.get("headers", {}))
    package, activity = PLAYERS.get(player, PLAYERS["mpv"])
    from allmanga_cli.domain.episodes import clean_episode_identifier, episode_label
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
    proxy_server = None
    intent_type = "video/*"

    subtitles = stream.get("subtitles") or stream.get("vtt") or []
    if not subtitles and stream.get("subtitle_url"):
        subtitles = [{"label": "English", "url": stream["subtitle_url"], "default": True}]
    if subtitles:
        try:
            from ..media.subtitle_scorer import filter_subtitles
            subtitles = filter_subtitles(
                subtitles,
                headers=headers,
                referer=referer,
                video_duration_s=float(stream.get("duration") or 0),
            )
        except Exception:
            pass

    cleanup_active_local_proxy()
    if stream.get("dash_video") and stream.get("dash_audio"):
        _info(f"{player}: preparing DASH video and audio...")
        try:
            manifest = generate_dash_mpd(
                stream["dash_video"],
                stream["dash_audio"],
                stream.get("dash_duration"),
            )
            url, proxy_server = start_local_content_server(
                manifest,
                "stream.mpd",
                "application/dash+xml",
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "application/dash+xml"
        except Exception as exc:
            _error(f"Could not prepare DASH stream: {exc}")
            return False
    elif (
            stream.get("split_video_url")
            and stream.get("split_audio_url")
            and stream.get("type") == "hls"):
        _info(f"{player}: preparing video and audio...")
        try:
            bw = float(stream.get("split_bandwidth") or 2400)
            if bw < 100_000:
                bw *= 1000
            url, proxy_server = start_local_dual_proxy(
                stream["split_video_url"],
                stream["split_audio_url"],
                referer,
                headers,
                width=stream.get("split_width") or 1280,
                height=stream.get("split_height") or 720,
                bandwidth=int(bw),
                title=media_title,
                subtitles=subtitles,
                audio_tracks=stream.get("audio_tracks"),
                target_player=player,
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "video/*"
        except Exception as exc:
            _error(f"Could not prepare split audio stream: {exc}")
            return False
    elif stream.get("dailymotion_video") and stream.get("dailymotion_audio"):
        _info(f"{player}: preparing Dailymotion video and audio...")
        try:
            bw = float(stream.get("dailymotion_bandwidth") or 2400)
            if bw < 100_000:
                bw *= 1000
            url, proxy_server = start_local_dual_proxy(
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
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "video/*"
        except Exception as exc:
            _error(f"Could not prepare Dailymotion stream: {exc}")
            return False
    elif not (
            stream.get("dash_video")
            or (stream.get("split_video_url") and stream.get("split_audio_url"))
            or (stream.get("dailymotion_video") and stream.get("dailymotion_audio"))):
        _info(f"{player}: starting local HTTP proxy...")
        try:
            url, proxy_server = start_local_proxy(
                url,
                referer,
                headers,
                stream_type=stream.get("type", "mp4"),
                title=media_title,
                subtitles=subtitles,
                target_player=player,
            )
            replace_active_local_proxy(proxy_server)
            intent_type = "video/*"
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
    hdr_list = []
    if referer:
        hdr_list.extend(["Referer", referer])
    ua = headers.get("User-Agent")
    if ua:
        hdr_list.extend(["User-Agent", ua])
    if hdr_list:
        command.extend(["--esa", "headers", ",".join(hdr_list)])

    launched = False
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
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
            if hdr_list:
                fallback_cmd.extend(["--esa", "headers", ",".join(hdr_list)])
            r2 = subprocess.run(
                fallback_cmd,
                capture_output=True,
                text=True,
            )
            if r2.returncode == 0 or "Starting: Intent" in (r2.stdout or ""):
                _ok(f"{player} opened.")
                launched = True

        if not launched:
            _error(f"Could not open {player}.")
    except Exception as exc:
        _error(f"Could not open {player}: {exc}")
    if not launched and proxy_server is not None:
        cleanup_active_local_proxy()
    return launched
