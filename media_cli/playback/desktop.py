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
        custom_skip_intervals=None,
        start_time_override=None,
        torrent_progress_callback=None,
        cancel_check=None):
    prepared_torrent = None
    torrent_ready_message = ""
    suppress_embedded_chapters = stream.get("type") == "torrent"
    if suppress_embedded_chapters:
        from ..torrents.runtime import TorrentCancelled, prepare_torrent_stream
        try:
            prepared_torrent = prepare_torrent_stream(
                stream,
                progress_callback=torrent_progress_callback,
                cancel_check=cancel_check,
            )
        except TorrentCancelled as exc:
            import logging
            logging.getLogger(__name__).debug("Torrent playback cancelled during preparation")
            if torrent_progress_callback:
                try:
                    torrent_progress_callback(str(exc))
                except TorrentCancelled:
                    pass
            return ("CANCELLED", 0, 0, 0, 0)
        except RuntimeError as exc:
            if torrent_progress_callback:
                torrent_progress_callback(str(exc))
            return ("ERROR", 0, 0, 0, 0)
        torrent_ready_message = "Torrent ready"
        stream = dict(stream)
        stream["link"] = prepared_torrent.path
        stream["type"] = "mp4"

    if cancel_check and cancel_check():
        import logging
        logging.getLogger(__name__).debug("Playback cancelled before mpv load")
        if prepared_torrent is not None:
            prepared_torrent.close()
        return ("CANCELLED", 0, 0, 0, 0)

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
    from media_cli.domain.episodes import clean_episode_identifier, episode_label, episode_progress_number
    raw_ep = str(episode_label(episode)).strip()
    ep_str = clean_episode_identifier(raw_ep) or raw_ep

    ep_key = ep_str.strip().lower()
    if suppress_embedded_chapters and ep_key in ("file", "episode file", "movie", "episode movie", "full", "episode full"):
        media_title = f"{title} ({resolution})" if resolution and str(resolution).lower() != "unknown" else title
    elif ep_key in ("movie", "full"):
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
        f"Resume {int(start_time // 60):02d}:"
        f"{int(start_time % 60):02d}"
    )
    osd_msg = (
        f"{osd_msg}\n{resume_message}" if (osd_msg and start_time > 0) else (resume_message if start_time > 0 else osd_msg)
    )
    if torrent_ready_message:
        osd_msg = f"{osd_msg}\n{torrent_ready_message}" if osd_msg else torrent_ready_message


    skip_intervals = []
    if custom_skip_intervals is not None:
        skip_intervals = list(custom_skip_intervals or [])
    elif aniskip_enabled and mal_id:
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
                flixcloud_w_payload=stream.get("flixcloud_w_payload"),
                flixcloud_xor_mask=stream.get("flixcloud_xor_mask"),
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
        suppress_chapters=suppress_embedded_chapters and not skip_intervals,
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
        if prepared_torrent is not None:
            prepared_torrent.close()
    return (
        result,
        ipc_player.props.get("percent-pos", 0) or 0,
        ipc_player.props.get("playback-time", 0) or 0,
        ipc_player.props.get("duration", 0) or 0,
        played_seconds,
    )
