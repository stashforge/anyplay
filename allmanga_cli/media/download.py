"""Episode download execution."""

import os
import shutil
import subprocess

from .urls import validate_optional_referer, validate_stream_url

CYAN = "\033[1;36m"
GREEN = "\033[1;32m"
RED = "\033[1;31m"
RESET = "\033[0m"


def _error(message):
    print(f"{RED}[ERR]{RESET} {message}")


def sanitize_filename(title: str) -> str:
    return "".join(
        char for char in str(title or "") if char.isalnum() or char in " -_"
    ).strip()


def download_episode(title, episode, stream, download_dir="", downloader="auto", extra_args=None, quiet=False):
    extra_args = extra_args or []
    audio_url = stream.get("audio_url", "")
    if downloader == "auto":
        downloader = "ffmpeg" if audio_url else "yt-dlp"

    if not shutil.which(downloader):
        if not quiet:
            if downloader == "ffmpeg":
                _error("ffmpeg is not installed or not in PATH.")
            else:
                _error("yt-dlp is not installed or not in PATH.")
        return False

    raw_url = stream.get("link") or stream.get("streamUrl") or stream.get("sourceUrl") or ""
    if not raw_url:
        _error("No valid stream URL found in stream object.")
        return False

    try:
        url = validate_stream_url(raw_url)
        if audio_url:
            audio_url = validate_stream_url(audio_url)
        referer = validate_optional_referer(stream.get("referer", ""))
    except ValueError:
        _error("Download rejected an unsafe stream URL.")
        return False

    safe_title = sanitize_filename(title)
    filename = f"{safe_title} - Episode {episode}.mp4"


    # Always create an anime-specific folder, even if download_dir is empty
    if not download_dir:
        from allmanga_cli.core.storage import get_default_download_dir
        download_dir = get_default_download_dir()
    else:
        download_dir = os.path.expanduser(str(download_dir).strip())

    target_dir = os.path.join(download_dir, safe_title)
    try:
        os.makedirs(target_dir, exist_ok=True)
    except Exception as exc:
        _error(f"Could not create download folder: {exc}")
        return False
    filename = os.path.join(target_dir, filename)

    if not quiet:
        print(f"\n{CYAN}[Download]{RESET} {filename}")

    # Extract all headers
    headers = stream.get("headers", {})
    if referer and "Referer" not in headers:
        headers["Referer"] = referer

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
            from allmanga_cli.media.local_proxy import start_local_proxy

            url, proxy_server = start_local_proxy(
                url,
                referer,
                headers,
                stream_type=stream.get("type", "mp4"),
                title=safe_title,
            )
        except Exception:
            proxy_server = None

    if downloader == "ffmpeg":
        command = [
            "ffmpeg",
            "-nostdin",
            "-y"
        ]
        if headers and not proxy_server:
            header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
            command.extend(["-headers", header_str])

        command.extend(["-i", url])
        if audio_url:
            command.extend(["-i", audio_url, "-map", "0:v:0", "-map", "1:a:0"])

        command.extend(["-c", "copy", filename])
    else:
        command = ["yt-dlp", url, "-o", filename]

        # Auto-inject aria2c if available and user didn't manually override it
        if shutil.which("aria2c") and not any(arg in extra_args for arg in ("--downloader", "--external-downloader")):
            command.extend([
                "--downloader", "aria2c",
                "--downloader-args", "aria2c:-x 16 -s 16 -k 1M"
            ])

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
        from allmanga_cli.core.processes import register_subprocess, unregister_subprocess

        popen_kwargs = {}
        if quiet:
            popen_kwargs["stdout"] = subprocess.DEVNULL
            popen_kwargs["stderr"] = subprocess.DEVNULL

        if os.name == 'posix':
            proc = subprocess.Popen(command, preexec_fn=os.setsid, **popen_kwargs)
        else:
            # CREATE_NEW_PROCESS_GROUP is 0x00000200
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
        if not quiet:
            print(f"\n{GREEN}[Success]{RESET} Download complete.")
        return True
    except Exception as exc:
        if not quiet:
            print(f"\n{RED}[Error]{RESET} Download failed: {exc}")
        return False
    finally:
        if proxy_server is not None:
            try:
                from allmanga_cli.media.local_proxy import stop_local_proxy

                stop_local_proxy(proxy_server)
            except Exception:
                pass
