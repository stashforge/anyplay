"""Episode download execution with multi-audio and subtitle preservation."""

import os
import shutil
import subprocess

from .languages import filter_tracks_by_language, normalize_lang_code
from .subtitles import download_sidecar_subtitles, remux_subtitles_into_mkv
from .urls import validate_optional_referer, validate_stream_url

CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _error(message):
    print(f"{RED}[ERR]{RESET} {message}")


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
):
    extra_args = extra_args or []

    # Read configuration defaults if not explicitly passed
    from media_cli.core.storage import load_config
    cfg = load_config()

    if download_sub_langs is None:
        download_sub_langs = cfg.get("download_sub_langs", "multi")
    if download_audio_langs is None:
        download_audio_langs = cfg.get("download_audio_langs", "multi")
    if subtitle_mode is None:
        subtitle_mode = cfg.get("subtitle_mode", "embed")

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

    has_multi_audio = len(audio_tracks) > 1 or (audio_url and audio_url != raw_url)
    embed_subs = (subtitle_mode == "embed") and bool(subtitles) and (download_sub_langs not in ("none", "no", False))

    if downloader == "auto":
        if has_multi_audio:
            downloader = "ffmpeg" if shutil.which("ffmpeg") else ("yt-dlp" if shutil.which("yt-dlp") else "ffmpeg")
        else:
            downloader = "yt-dlp" if shutil.which("yt-dlp") else "ffmpeg"

    if downloader == "yt-dlp":
        if embed_subs and not shutil.which("ffmpeg"):
            if not quiet:
                print(f"{YELLOW}⚠ ffmpeg not found; cannot embed subtitles into container. Saving as sidecar subtitles.{RESET}")
            embed_subs = False
        if has_multi_audio and not quiet:
            print(f"{YELLOW}ℹ yt-dlp uses --audio-multistreams for multi-audio (ffmpeg is recommended for separate stream inputs).{RESET}")

    if not shutil.which(downloader):
        if not quiet:
            if downloader == "ffmpeg":
                _error("ffmpeg is not installed or not in PATH.")
            else:
                _error("yt-dlp is not installed or not in PATH.")
        return False

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
    ext = ".mkv" if embed_subs else ".mp4"
    final_filename = f"{base_filename}{ext}"

    # If using yt-dlp to download video before remuxing into mkv with ffmpeg
    yt_dlp_remux = (downloader == "yt-dlp" and embed_subs)
    download_filename = f"{base_filename}.temp.mp4" if yt_dlp_remux else final_filename
    ffmpeg_partial_filename = _partial_output_path(final_filename) if downloader == "ffmpeg" else ""

    if not quiet:
        print(f"\n{CYAN}[Download]{RESET} {final_filename}")

    # Extract all headers
    headers = stream.get("headers", {})
    if referer and "Referer" not in headers:
        headers["Referer"] = referer

    # Proxy handling for obfuscated CDNs
    proxy_server = None
    if (
        stream.get("requires_proxy")
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
            )
        except Exception:
            proxy_server = None

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
                print(f"{RED}⚠ Could not download subtitles: {sub_exc}{RESET}")

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
        command.extend(["-i", url])

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
                command.extend(["-i", v_url])
                a_lang = normalize_lang_code(t.get("language") or t.get("label"), target="alpha3")
                a_title = t.get("label") or "Audio"
                audio_maps.append((input_idx, a_lang, a_title))
                input_idx += 1
        elif audio_url and audio_url != url:
            if header_str:
                command.extend(["-headers", header_str])
            command.extend(["-i", audio_url])
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

        popen_kwargs = {}
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
                    print(f"{CYAN}[Remux]{RESET} Embedding {len(downloaded_sidecars)} subtitle track(s) into {os.path.basename(final_filename)}...")
                remux_ok = remux_subtitles_into_mkv(
                    actual_download_path,
                    downloaded_sidecars,
                    final_filename,
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
                    fallback_mp4 = f"{base_filename}.mp4"
                    try:
                        os.replace(actual_download_path, fallback_mp4)
                    except Exception:
                        pass
                    if not quiet:
                        print(f"{YELLOW}⚠ Subtitle embedding failed; video saved as {os.path.basename(fallback_mp4)} with sidecars.{RESET}")
            elif os.path.exists(actual_download_path):
                fallback_mp4 = f"{base_filename}.mp4"
                try:
                    os.replace(actual_download_path, fallback_mp4)
                except Exception:
                    pass

        if downloader == "ffmpeg" and embed_subs and downloaded_sidecars:
            for s_path in downloaded_sidecars:
                try:
                    if os.path.exists(s_path):
                        os.remove(s_path)
                except Exception:
                    pass

        if not quiet:
            print(f"\n{GREEN}[Success]{RESET} Download complete.")
        return True
    except Exception as exc:
        if downloader == "ffmpeg":
            _discard_partial_output(ffmpeg_partial_filename)
        if not quiet:
            print(f"\n{RED}[Error]{RESET} Download failed: {exc}")
        return False
    finally:
        if proxy_server is not None:
            try:
                from media_cli.media.local_proxy import stop_local_proxy

                stop_local_proxy(proxy_server)
            except Exception:
                pass
