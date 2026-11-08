"""Desktop mpv playback orchestration."""

import logging
import time
from contextlib import nullcontext
from urllib.parse import urlsplit

from ..media.proxy_rules import proxy_filtered_headers
from ..media.urls import validate_optional_referer, validate_stream_url
from ..services.network_affinity import bound_proxy_options

log = logging.getLogger(__name__)
# Central switch for the mpv HLS subtitle seek-duplication workaround.
_HLS_LOCAL_SUBTITLES = True


class PlaybackPreparationCancelled(Exception):
    """A preparation adapter cancelled before the player could start."""


def _prepare_with_timing(stage, operation, *args, **kwargs):
    """Log stage boundaries without logging URLs, headers or exception text."""
    started = time.perf_counter()
    outcome = 'ok'
    tracks_in = len(args[0]) if args and isinstance(args[0], (list, tuple)) else 0
    tracks_out = 0
    log.debug('Desktop preparation started: stage=%s tracks_in=%d', stage, tracks_in)
    try:
        result = operation(*args, **kwargs)
        tracks = result if isinstance(result, (list, tuple)) else getattr(result, 'subtitles', ())
        tracks_out = len(tracks or ())
        return result
    except BaseException as exc:
        outcome = type(exc).__name__
        raise
    finally:
        log.debug('Desktop preparation finished: stage=%s outcome=%s elapsed=%.3fs tracks_in=%d tracks_out=%d',
                  stage, outcome, time.perf_counter() - started, tracks_in, tracks_out)


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
        cancel_check=None,
        prepare_torrent_fn=None,
        prepare_subtitles_fn=None,
        prepare_subtitle_files_fn=None,
        prepare_proxy_fn=None,
        start_player_fn=None,
        background_subtitles=False,
        playback_input_scope=None):
    prepared_torrent = None
    torrent_ready_message = ""
    suppress_embedded_chapters = stream.get("type") == "torrent"
    if suppress_embedded_chapters:
        from ..torrents.runtime import TorrentCancelled, prepare_torrent_stream
        try:
            prepared_torrent = (prepare_torrent_fn or prepare_torrent_stream)(
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
    deferred_subtitles = subtitles if background_subtitles else []
    if background_subtitles:
        subtitles = []
    if subtitles:
        try:
            from ..media.subtitle_scorer import filter_subtitles
            subtitles = _prepare_with_timing(
                'subtitle-filter', prepare_subtitles_fn or filter_subtitles,
                subtitles,
                headers=headers,
                referer=referer,
                video_duration_s=float(stream.get("duration") or 0),
            )
        except (PlaybackPreparationCancelled, KeyboardInterrupt) as exc:
            if prepared_torrent is not None:
                prepared_torrent.close()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return ("CANCELLED", 0, 0, 0, 0)
        except Exception:
            pass

    prepared_subtitles = None
    is_hls = str(stream.get("type") or "").lower() == "hls" or urlsplit(url).path.lower().endswith(".m3u8")
    if _HLS_LOCAL_SUBTITLES and is_hls and subtitles:
        from ..media.mpv_subtitle_files import SubtitleFilesCancelled, prepare_subtitle_files
        try:
            prepared_subtitles = _prepare_with_timing(
                'subtitle-files', prepare_subtitle_files_fn or prepare_subtitle_files,
                subtitles, headers=headers, referer=referer, cancel_check=cancel_check,
            )
            subtitles = prepared_subtitles.subtitles
        except (SubtitleFilesCancelled, PlaybackPreparationCancelled, KeyboardInterrupt) as exc:
            if prepared_torrent is not None:
                prepared_torrent.close()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return ("CANCELLED", 0, 0, 0, 0)
        except Exception as exc:
            log.warning("Subtitle file preparation unavailable (%s); retaining remote tracks", type(exc).__name__)

    from ..core import reporting
    from ..services.requirements import dependency_hint_messages

    hints = list(dependency_hint_messages(stream))
    hints.extend(getattr(prepared_subtitles, "dependency_hints", ()))
    for hint in dict.fromkeys(hints):
        reporting.warn(hint)

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

    if isinstance(stream.get("media_title"), str) and stream["media_title"].strip():
        from ..core.terminal import sanitize_terminal_text
        media_title = sanitize_terminal_text(stream["media_title"])

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
    skip_lookup = None
    if custom_skip_intervals is not None:
        skip_intervals = list(custom_skip_intervals or [])
    elif aniskip_enabled and not url.startswith(("https://", "http://")):
        from ..media.episode_identity import read_episode_skips

        skip_intervals = read_episode_skips(url)
    elif aniskip_enabled:
        from ..media.aniskip import cached_skip_times
        from ..media.provider_skips import normalize_provider_skips
        ep_num = episode_progress_number(episode)
        native = normalize_provider_skips(stream.get("skip_intervals"))
        if native or mal_id:
            skip_lookup = (mal_id, ep_num)
        cached = []
        if mal_id:
            try:
                cached = cached_skip_times(mal_id, ep_num)
            except Exception:
                log.exception("AniSkip cached lookup failed")
        # Keep native candidates separate until actual-duration validation; an
        # invalid native range must not hide a usable cached fallback.
        skip_intervals = native + cached

    proxy_server = None
    if (
        stream.get("requires_proxy")
        or stream.get("ip_version") == 4
        or "uwucdn.top" in url
        or "kwik." in referer
        or "megap." in url
        or "akirax.buzz" in url
    ):
        try:
            from ..media.local_proxy import replace_active_local_proxy, start_local_proxy
            url, proxy_server = _prepare_with_timing(
                'local-proxy', prepare_proxy_fn or start_local_proxy,
                url,
                referer,
                headers,
                stream_type=stream.get("type", "mp4"),
                title=media_title,
                subtitles=[track for track in subtitles if not str(track.get("url") or track.get("file") or "").startswith("/")],
                target_player="mpv",
                flixcloud_w_payload=stream.get("flixcloud_w_payload"),
                flixcloud_xor_mask=stream.get("flixcloud_xor_mask"),
                **bound_proxy_options(stream),
            )
            if stream.get("ip_version") == 4:
                audio_url, audio_tracks = "", []
            replace_active_local_proxy(proxy_server)
        except (PlaybackPreparationCancelled, KeyboardInterrupt) as exc:
            if prepared_subtitles is not None:
                prepared_subtitles.close()
            if prepared_torrent is not None:
                prepared_torrent.close()
            if isinstance(exc, KeyboardInterrupt):
                raise
            return ("CANCELLED", 0, 0, 0, 0)
        except Exception:
            proxy_server = None
            if stream.get("ip_version") == 4:
                if prepared_subtitles is not None:
                    prepared_subtitles.close()
                if prepared_torrent is not None:
                    prepared_torrent.close()
                log.warning("Stream network proxy unavailable; refusing a direct connection")
                return ("ERROR", 0, 0, 0, 0)

    log.debug(
        "Playback selection: show_id=%r MAL=%r episode=%r source=%r resolution=%r remote_skips=%s",
        show_id, mal_id, episode, stream.get("source_name"), resolution,
        skip_lookup is not None,
    )
    subtitle_job = None
    try:
        if deferred_subtitles:
            from .subtitle_job import SubtitleJob
            subtitle_job = SubtitleJob(
                deferred_subtitles, headers, referer, float(stream.get('duration') or 0),
                _HLS_LOCAL_SUBTITLES and is_hls,
            )
        if start_player_fn is not None:
            _prepare_with_timing('mpv-start', start_player_fn, ipc_player)
        if subtitle_job is not None:
            subtitles = subtitle_job.poll() or []
            subtitle_url = next(
                (s.get('url') or s.get('file') for s in subtitles if s.get('default')),
                (subtitles[0].get('url') or subtitles[0].get('file') or '') if subtitles else '',
            )
        with playback_input_scope() if playback_input_scope else nullcontext(False) as terminal_managed:
            _prepare_with_timing(
                'mpv-load', ipc_player.load,
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
                suppress_chapters=suppress_embedded_chapters,
                **({"skip_lookup": skip_lookup} if skip_lookup is not None else {}),
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
            load_serial = getattr(ipc_player, '_load_serial', None)

            def poll_subtitles():
                tracks = subtitle_job.poll()
                if tracks:
                    ipc_player.queue_subtitles(tracks, load_serial=load_serial)

            result, played_seconds = ipc_player.wait_for_playback(
                stream_info,
                episode,
                total_eps,
                fetch_callback,
                is_binge,
                **({"terminal_managed": True} if terminal_managed else {}),
                **({'poll_subtitles': poll_subtitles} if subtitle_job is not None else {}),
            )
    except PlaybackPreparationCancelled:
        return ("CANCELLED", 0, 0, 0, 0)
    finally:
        if subtitle_job is not None:
            subtitle_job.close()
        if prepared_subtitles is not None:
            prepared_subtitles.close()
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
