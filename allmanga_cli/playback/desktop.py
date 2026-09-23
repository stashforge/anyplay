"""Desktop mpv playback orchestration."""

from ..media.proxy_rules import proxy_filtered_headers
from ..media.urls import validate_optional_referer, validate_stream_url


def play_desktop(
        ipc_player,
        title,
        episode,
        stream,
        *,
        get_resume_time,
        get_preferred_mirror,
        update_stream_info,
        fetch_callback=None,
        total_eps=1,
        is_binge=False,
        show_id=None,
        osd_msg="",
        episode_index=0,
        next_episode=None,
        mal_id=None,
        aniskip_enabled=True,
        aniskip_auto=True,
        start_time_override=None):
    url = validate_stream_url(stream["link"])
    audio_url = (
        validate_stream_url(stream["audio_url"])
        if stream.get("audio_url")
        else ""
    )
    referer = validate_optional_referer(stream.get("referer", ""))
    headers = proxy_filtered_headers(stream.get("headers", {}))
    audio_tracks = stream.get("audio_tracks") or []
    subtitles = stream.get("subtitles") or stream.get("vtt") or []
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

    subtitle_url = ""
    if subtitles:
        default_sub = next((s.get("url") or s.get("file") for s in subtitles if s.get("default")), None)
        subtitle_url = default_sub or (subtitles[0].get("url") or subtitles[0].get("file") or "")
    resolution = stream.get("resolution", "Adaptive")
    from allmanga_cli.domain.episodes import clean_episode_identifier, episode_label, episode_progress_number
    raw_ep = str(episode_label(episode)).strip()
    ep_str = clean_episode_identifier(raw_ep) or raw_ep

    if ep_str.lower() in ("movie", "full"):
        media_title = f"{title} ({resolution})"
    else:
        if ep_str and ep_str[0].isdigit():
            ep_str = f"EP {ep_str}"
        elif ep_str.lower() == "ova":
            ep_str = "OVA"
        elif ep_str.lower().startswith("ova "):
            ep_str = "OVA " + ep_str[4:]
        else:
            ep_str = ep_str.title()
        media_title = f"{title} - {ep_str} ({resolution})"

    if start_time_override is not None and float(start_time_override) > 0:
        start_time = float(start_time_override)
    else:
        start_time = (
            get_resume_time(show_id, episode)
            or get_resume_time(show_id, ep_str)
            or get_resume_time(show_id, raw_ep)
        ) if show_id else 0
        if start_time > 0:
            start_time = max(0, start_time - 30)
    resume_message = (
        f"Resuming at {int(start_time // 60):02d}:"
        f"{int(start_time % 60):02d}"
    )
    osd_msg = (
        f"{osd_msg}\n{resume_message}" if (osd_msg and start_time > 0) else (resume_message if start_time > 0 else osd_msg)
    )


    skip_intervals = []
    if aniskip_enabled and mal_id:
        try:
            from ..media.aniskip import fetch_skip_times
            ep_num = episode_progress_number(episode)
            skip_intervals = fetch_skip_times(mal_id, ep_num)
        except Exception:
            skip_intervals = []

    proxy_server = None
    if (
        stream.get("requires_proxy")
        or "uwucdn.top" in url
        or "kwik." in referer
        or "megap." in url
        or "akirax.buzz" in url
    ):
        try:
            from ..media.local_proxy import replace_active_local_proxy, start_local_proxy
            url, proxy_server = start_local_proxy(
                url,
                referer,
                headers,
                stream_type=stream.get("type", "mp4"),
                title=media_title,
                subtitles=subtitles,
                target_player="mpv",
            )
            replace_active_local_proxy(proxy_server)
        except Exception:
            proxy_server = None

    ipc_player.load(
        url,
        media_title,
        headers,
        referer,
        start_time,
        osd_msg,
        audio_url,
        subtitle_url,
        subtitles=subtitles,
        skip_intervals=skip_intervals,
        aniskip_auto=aniskip_auto,
        audio_tracks=audio_tracks,
    )


    mirror_name = stream.get("source_name", "Unknown")
    preference = get_preferred_mirror(show_id) if show_id else {}
    stream_info = {
        "title": title,
        "mirror": mirror_name,
        "quality": resolution,
        "is_pref": (
            preference.get("source_name") == mirror_name
            and preference.get("resolution") == resolution
        ),
        "episode_index": episode_index,
        "next_episode": next_episode,
    }
    update_stream_info(stream_info)
    try:
        result, played_seconds = ipc_player.wait_for_playback(
            stream_info,
            episode,
            total_eps,
            fetch_callback,
            is_binge,
        )
    finally:
        if proxy_server is not None:
            from ..media.local_proxy import cleanup_active_local_proxy
            cleanup_active_local_proxy()
    return (
        result,
        ipc_player.props.get("percent-pos", 0) or 0,
        ipc_player.props.get("playback-time", 0) or 0,
        ipc_player.props.get("duration", 0) or 0,
        played_seconds,
    )
