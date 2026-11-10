"""Episode download execution with multi-audio and subtitle preservation."""

import logging
import os
import shutil
import subprocess

from ..core.processes import diagnostic_subprocess_output
from ..core.reporting import console_status
from ..services.network_affinity import bound_proxy_options
from .download_requirements import plan_download
from .hls import ffmpeg_hls_input_args
from .languages import filter_tracks_by_language, normalize_lang_code
from .subtitles import download_sidecar_subtitles, remux_subtitles_into_mkv
from .urls import validate_optional_referer, validate_stream_url

CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _error(message):
    console_status(message, kind='error')


def _partial_output_path(final_path: str) -> str:
    final_abs = os.path.abspath(os.path.expanduser(final_path))
    final_dir = os.path.dirname(final_abs)
    return os.path.join(final_dir, ".anyplay-partials", os.path.basename(final_abs))


def _commit_partial_output(partial_path: str, final_path: str) -> None:
    if not os.path.isfile(partial_path) or os.path.getsize(partial_path) <= 0:
        raise RuntimeError("ffmpeg did not produce a complete output file")
    os.makedirs(os.path.dirname(os.path.abspath(final_path)), exist_ok=True)
    os.replace(partial_path, final_path)
    try:
        partial_dir = os.path.dirname(partial_path)
        if not os.listdir(partial_dir):
            os.rmdir(partial_dir)
    except OSError:
        pass


def _discard_partial_output(partial_path: str) -> None:
    try:
        if partial_path and os.path.exists(partial_path):
            os.remove(partial_path)
    except OSError:
        pass


def sanitize_filename(title: str) -> str:
    return "".join(
        char for char in str(title or "") if char.isalnum() or char in " -_"
    ).strip()


def resolve_download_skips(stream, mal_id, episode):
    from ..domain.episodes import episode_progress_number
    from .aniskip import fetch_skip_times
    from .provider_skips import normalize_provider_skips

    native = normalize_provider_skips(stream.get("skip_intervals"))
    if native:
        return native
    episode_number = episode_progress_number(episode)
    if mal_id and episode_number:
        try:
            return fetch_skip_times(mal_id, episode_number)
        except (OSError, ValueError, TypeError, AttributeError):
            logging.getLogger(__name__).debug("Could not fetch skip timings for download", exc_info=True)
    return []


def anyplay_metadata_args(title: str = "", episode: str = "", title_id: str = "", episode_id: str = "", *, skip_intervals=None) -> list[str]:
    tags = {
        "anyplay_title": str(title or "").strip(),
        "anyplay_episode": str(episode or "").strip(),
        "anyplay_title_id": str(title_id or "").strip(),
        "anyplay_episode_id": str(episode_id or "").strip(),
    }
    if skip_intervals:
        import json

        tags["anyplay_skip_intervals"] = json.dumps(skip_intervals, separators=(",", ":"))
    args: list[str] = []
    for key, value in tags.items():
        if value:
            args.extend(["-metadata", f"{key}={value}"])
    return args


def embed_download_metadata(path, metadata_args, *, cancel_check=None):
    """Best-effort stream-copy remux for engines that cannot write Anyplay tags."""
    import tempfile

    if (cancel_check and cancel_check()) or not shutil.which("ffmpeg"):
        return False
    try:
        with tempfile.TemporaryDirectory(prefix=".anyplay-remux-", dir=os.path.dirname(os.path.abspath(path))) as work:
            output = os.path.join(work, "episode.mkv")
            result = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                 "-i", os.fspath(path), "-map", "0", "-c", "copy", *metadata_args, output],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
            )
            if result.returncode or not os.path.getsize(output) or (cancel_check and cancel_check()):
                return False
            os.replace(output, path)
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def download_episode(
    title,
    episode,
    stream,
    download_dir="",
    downloader="auto",
    extra_args=None,
    quiet=False,
    download_sub_langs=None,
    download_audio_langs=None,
    subtitle_mode=None,
    mal_id=None,
):
    extra_args = extra_args or []

    # Read configuration defaults if not explicitly passed
    from media_cli.core.storage import load_config
    cfg = load_config()

    if download_sub_langs is None:
        download_sub_langs = cfg.get("download_sub_langs", "multi")
    if download_audio_langs is None:
        download_audio_langs = cfg.get("download_audio_langs", "multi")

    raw_url = stream.get("link") or stream.get("streamUrl") or stream.get("sourceUrl") or ""
    if not raw_url:
        _error("No valid stream URL found in stream object.")
        return False

    # Extract audio tracks
    audio_tracks = list(stream.get("audio_tracks") or [])
    audio_url = stream.get("audio_url", "")
    if not audio_tracks and audio_url:
        audio_tracks = [{"url": audio_url, "label": "Audio", "language": "und", "default": True}]

    # Filter audio tracks by user preference
    if audio_tracks:
        audio_tracks = filter_tracks_by_language(audio_tracks, download_audio_langs, is_audio=True)

    # Extract subtitle tracks
    subtitles = list(stream.get("subtitles") or [])
    if not subtitles and stream.get("subtitle"):
        subtitles = [{"url": stream.get("subtitle"), "label": "Default", "language": "en", "default": True}]
    subtitles = filter_tracks_by_language(subtitles, download_sub_langs, is_audio=False)

    has_multi_audio = any(track.get("url") and track["url"] != raw_url for track in audio_tracks) or bool(audio_url and audio_url != raw_url)
    embed_subs = bool(subtitles) and (download_sub_langs not in ("none", "no", False))

    plan = plan_download(downloader, raw_url, external_audio=has_multi_audio, embed_subtitles=embed_subs)
    downloader = plan.engine
    for hint in plan.hints:
        if quiet:
            from ..core import reporting

            reporting.warn(hint)
        else:
            console_status(hint, kind='warning')

    try:
        url = validate_stream_url(raw_url)
        referer = validate_optional_referer(stream.get("referer", ""))
    except ValueError:
        _error("Download rejected an unsafe stream URL.")
        return False

    safe_title = sanitize_filename(title)
    if not download_dir:
        from media_cli.core.storage import get_default_download_dir
        download_dir = get_default_download_dir()
    else:
        download_dir = os.path.expanduser(str(download_dir).strip())

    target_dir = os.path.join(download_dir, safe_title)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as exc:
        _error(f"Could not create download folder: {exc}")
        return False

    # Base path without extension
    base_filename = os.path.join(target_dir, f"{safe_title} - Episode {episode}")
    ext = ".mkv"
    final_filename = f"{base_filename}{ext}"

    yt_dlp_remux = downloader == "yt-dlp" and embed_subs
    download_filename = f"{base_filename}.temp.mp4" if yt_dlp_remux else final_filename
    ffmpeg_partial_filename = _partial_output_path(final_filename) if downloader == "ffmpeg" else ""

    if not quiet:
        console_status(f"Downloading {final_filename}")

    # Extract all headers
    headers = stream.get("headers", {})
    if referer and "Referer" not in headers:
        headers["Referer"] = referer

    # Proxy handling for obfuscated CDNs
    proxy_server = None
    if (
        stream.get("requires_proxy")
        or stream.get("ip_version") == 4
        or "uwucdn.top" in url
        or "owocdn.top" in url
        or "kwik." in referer
        or "megap." in url
        or "akirax.buzz" in url
    ):
        try:
            from media_cli.media.local_proxy import start_local_proxy

            url, proxy_server = start_local_proxy(
                url,
                referer,
                headers,
                stream_type=stream.get("type", "mp4"),
                title=safe_title,
                flixcloud_w_payload=stream.get("flixcloud_w_payload"),
                flixcloud_xor_mask=stream.get("flixcloud_xor_mask"),
                **bound_proxy_options({**stream, "audio_url": audio_url, "audio_tracks": audio_tracks}),
            )
            if stream.get("ip_version") == 4:
                audio_url, audio_tracks = "", []
        except Exception:
            proxy_server = None
            if stream.get("ip_version") == 4:
                _error("Could not prepare the stream network proxy.")
                return False

    # Subtitles download: sidecars, or fetched locally for embedding into mkv
    downloaded_sidecars: list[str] = []
    if subtitles and download_sub_langs not in ("none", "no", False):
        try:
            downloaded_sidecars = download_sidecar_subtitles(
                subtitles,
                base_filename,
                headers=headers,
                referer=referer,
                requested_langs=download_sub_langs,
                quiet=quiet,
            )
        except Exception as sub_exc:
            if not quiet:
                console_status(f"Could not download subtitles: {sub_exc}", kind='warning')

    skip_intervals = resolve_download_skips(stream, mal_id, episode)
    metadata_args = anyplay_metadata_args(title=title, episode=str(episode), skip_intervals=skip_intervals)

    if downloader == "ffmpeg":
        command = [
            "ffmpeg",
            "-nostdin",
            "-y"
        ]
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items()) if (headers and not proxy_server) else ""
        if header_str:
            command.extend(["-headers", header_str])

        # Input 0: Primary video stream
        command.extend([*ffmpeg_hls_input_args(url), "-i", url])

        # Additional audio inputs
        input_idx = 1
        audio_maps = []
        if audio_tracks:
            for t in audio_tracks:
                t_url = t.get("url")
                if not t_url or t_url == url:
                    continue
                try:
                    v_url = validate_stream_url(t_url)
                except ValueError:
                    continue
                if header_str:
                    command.extend(["-headers", header_str])
                command.extend([*ffmpeg_hls_input_args(v_url), "-i", v_url])
                a_lang = normalize_lang_code(t.get("language") or t.get("label"), target="alpha3")
                a_title = t.get("label") or "Audio"
                audio_maps.append((input_idx, a_lang, a_title))
                input_idx += 1
        elif audio_url and audio_url != url:
            if header_str:
                command.extend(["-headers", header_str])
            command.extend([*ffmpeg_hls_input_args(audio_url), "-i", audio_url])
            audio_maps.append((input_idx, "und", "Audio"))
            input_idx += 1

        # Embedded subtitle inputs (uses locally downloaded subtitle files to prevent 403 CDN errors)
        sub_maps = []
        if embed_subs:
            for s_path in downloaded_sidecars:
                command.extend(["-i", s_path])
                stem = os.path.splitext(os.path.basename(s_path))[0]
                parts = stem.split(".")
                raw_lang = parts[-1].split("-")[0] if len(parts) >= 2 else "und"
                s_lang = normalize_lang_code(raw_lang, target="alpha3")
                s_title = raw_lang.upper() if raw_lang != "und" else "Subtitles"
                sub_maps.append((input_idx, s_lang, s_title))
                input_idx += 1

        # Video stream map
        command.extend(["-map", "0:v:0"])

        # Audio stream maps
        if audio_maps:
            for a_stream_idx, (in_idx, a_lang, a_title) in enumerate(audio_maps):
                command.extend(["-map", f"{in_idx}:a:0"])
                command.extend([f"-metadata:s:a:{a_stream_idx}", f"language={a_lang}"])
                command.extend([f"-metadata:s:a:{a_stream_idx}", f"title={a_title}"])
        else:
            command.extend(["-map", "0:a?"])

        # Subtitle stream maps
        if sub_maps:
            for s_stream_idx, (in_idx, s_lang, s_title) in enumerate(sub_maps):
                command.extend(["-map", f"{in_idx}:s:0"])
                command.extend([f"-metadata:s:s:{s_stream_idx}", f"language={s_lang}"])
                command.extend([f"-metadata:s:s:{s_stream_idx}", f"title={s_title}"])

        os.makedirs(os.path.dirname(ffmpeg_partial_filename), exist_ok=True)

        command.extend(metadata_args)

        # Stream copy without re-encoding
        command.extend(["-c", "copy", ffmpeg_partial_filename])
    else:
        command = ["yt-dlp", url, "-o", download_filename]

        if shutil.which("aria2c") and not any(arg in extra_args for arg in ("--downloader", "--external-downloader")):
            command.extend([
                "--downloader", "aria2c",
                "--downloader-args", "aria2c:-x 16 -s 16 -k 1M"
            ])

        command.extend(["--audio-multistreams"])

        if not proxy_server:
            for k, v in headers.items():
                command.extend(["--add-header", f"{k}:{v}"])

            if "--extractor-args" not in extra_args and not any(
                isinstance(a, str) and a.startswith("--extractor-args") for a in extra_args
            ):
                command.extend(["--extractor-args", "generic:impersonate"])

    if extra_args:
        command.extend(extra_args)

    proc = None
    try:
        from media_cli.core.processes import register_subprocess, unregister_subprocess

        popen_kwargs = diagnostic_subprocess_output()
        if quiet:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL

        if os.name == 'posix':
            proc = subprocess.Popen(command, preexec_fn=os.setsid, **popen_kwargs)
        else:
            proc = subprocess.Popen(
                command,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200),
                **popen_kwargs,
            )

        register_subprocess(proc)
        try:
            proc.wait()
            if proc.returncode != 0:
                raise subprocess.CalledProcessError(proc.returncode, command)
        finally:
            unregister_subprocess(proc)

        if downloader == "ffmpeg":
            _commit_partial_output(ffmpeg_partial_filename, final_filename)

        if yt_dlp_remux:
            actual_download_path = download_filename
            if not os.path.exists(actual_download_path):
                candidates = [
                    os.path.join(target_dir, p) for p in os.listdir(target_dir)
                    if p.startswith(os.path.basename(base_filename) + ".temp.") and not p.endswith(".part")
                ]
                if candidates:
                    actual_download_path = candidates[0]

            if downloaded_sidecars and os.path.exists(actual_download_path):
                if not quiet:
                    console_status(f"Embedding {len(downloaded_sidecars)} subtitle track(s) into {os.path.basename(final_filename)}...")
                remux_ok = remux_subtitles_into_mkv(
                    actual_download_path,
                    downloaded_sidecars,
                    final_filename,
                    metadata_args=metadata_args,
                    quiet=quiet,
                )
                if remux_ok:
                    try:
                        os.remove(actual_download_path)
                        for s_path in downloaded_sidecars:
                            if os.path.exists(s_path):
                                os.remove(s_path)
                    except Exception:
                        pass
                else:
                    try:
                        if os.path.exists(actual_download_path):
                            os.remove(actual_download_path)
                        for s_path in downloaded_sidecars:
                            if os.path.exists(s_path):
                                os.remove(s_path)
                    except OSError:
                        pass
                    if not quiet:
                        _error("Subtitle embedding failed; no sidecar fallback was kept.")
                    return False
            elif os.path.exists(actual_download_path):
                try:
                    os.remove(actual_download_path)
                except OSError:
                    pass
                if not quiet:
                    _error("yt-dlp did not produce a remuxable subtitle download.")
                return False

        if downloader == "yt-dlp" and not yt_dlp_remux and skip_intervals:
            if not embed_download_metadata(final_filename, metadata_args) and not quiet:
                console_status("Download saved without skip timings. FFmpeg remux unavailable.", kind="warning")

        if downloader == "ffmpeg" and downloaded_sidecars:
            for s_path in downloaded_sidecars:
                try:
                    if os.path.exists(s_path):
                        os.remove(s_path)
                except Exception:
                    pass

        if not quiet:
            console_status("Download complete.", kind='success')
        return True
    except Exception as exc:
        if downloader == "ffmpeg":
            _discard_partial_output(ffmpeg_partial_filename)
        if not quiet:
            console_status(f"Download failed: {exc}", kind='error')
        return False
    finally:
        if proxy_server is not None:
            try:
                from media_cli.media.local_proxy import stop_local_proxy

                stop_local_proxy(proxy_server)
            except Exception:
                pass
