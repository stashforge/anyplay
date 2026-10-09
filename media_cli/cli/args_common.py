"""Common argument parsing helpers, formatters, and reusable option groups."""

import argparse
import importlib.metadata
import os
import re
import sys

from ..constants import APP_NAME

try:
    from .. import __version__
except Exception:
    try:
        __version__ = importlib.metadata.version(APP_NAME)
    except Exception:
        __version__ = "0.8.8"

from ..providers import available_providers

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

COMMAND_NAMES = {
    "search", "download", "downloads", "anilist", "history", "continue",
    "auth", "providers", "completion", "config",
}

COMPLETION_SHELLS = ("bash", "zsh", "fish")


def _provider_help():
    providers = ", ".join(sorted(available_providers()))
    return f"Streaming provider: {providers}" if providers else "Streaming provider"

def _provider_command_names():
    return set(available_providers())

def _help_color_enabled():
    return (
        os.environ.get("NO_COLOR") is None
        and os.environ.get("TERM", "") != "dumb"
        and hasattr(sys.stdout, "isatty")
        and sys.stdout.isatty()
    )

class MinimalHelpFormatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_help_position", 22)
        super().__init__(*args, **kwargs)
        self._minimal_color = _help_color_enabled()

    def _set_color(self, color):
        super()._set_color(False)
        self._decolor = lambda value: _ANSI_RE.sub("", value)

    def start_section(self, heading):
        if self._minimal_color:
            heading = f"\033[1;34m{heading}\033[0m"
        super().start_section(heading)

    def _format_action_invocation(self, action):
        if not action.option_strings:
            return super()._format_action_invocation(action)
        return ", ".join(action.option_strings)

    def _fill_text(self, text, width, indent):
        lines = []
        for line in text.splitlines(keepends=True):
            plain = line.rstrip("\r\n")
            ending = line[len(plain):]
            if (
                self._minimal_color
                and plain in ("Lists:", "Actions:", "Examples:", "Commands:")
            ):
                line = f"\033[1;34m{plain}\033[0m{ending}"
            lines.append(indent + line)
        return "".join(lines)

class CommandHelpFormatter(MinimalHelpFormatter):
    def _format_action(self, action):
        text = super()._format_action(action)
        if isinstance(action, argparse._SubParsersAction):
            lines = text.splitlines(keepends=True)[1:]
            return "".join(
                line[2:] if line.startswith("    ") else line
                for line in lines
                if "==SUPPRESS==" not in line
            )
        return text

def _configure_help_parser(parser):
    if hasattr(parser, "color"):
        parser.color = False
    return parser

class CLIParser(argparse.ArgumentParser):
    def error(self, message):
        import difflib
        import re
        import sys
        if "invalid choice: " in message and "(choose from" in message:
            m = re.search(r"invalid choice: '([^']+)'", message)
            if m:
                bad_cmd = m.group(1)
                try:
                    choices_str = message.split("(choose from ")[1].rstrip(")")
                    choices = [c.strip("' ") for c in choices_str.split(",")]
                    matches = difflib.get_close_matches(bad_cmd, choices, n=3, cutoff=0.2)
                except Exception:
                    matches = []

                print(f"\n\033[31mUnknown command:\033[0m {bad_cmd}\n")
                if matches:
                    print("Did you mean:")
                    for match in matches:
                        print(f"  {match}")
                    print()

                print(f"Run '{APP_NAME} -h' to see all commands.")
                print(f"Run '{APP_NAME} providers' to see available providers.\n")
                sys.exit(2)
        elif "the following arguments are required: <action>" in message or "the following arguments are required: provider_action" in message or "the following arguments are required: <command>" in message:
            provider = self.prog.split()[-1]
            if provider == APP_NAME or provider not in _provider_command_names():
                super().error(message)
            print("\n\033[31mNo action specified.\033[0m\n")
            print("Usage:")
            print(f"  {APP_NAME} {provider} search <query>\n")
            print("Run:")
            print(f"  {APP_NAME} {provider} -h\n")
            sys.exit(2)
        super().error(message)

ANILIST_COMMAND_TARGETS = {
    "menu": "menu",
    "airing": "airing",
    "search": "search",
    "watching": "CURRENT",
    "current": "CURRENT",
    "planning": "PLANNING",
    "plan-to-watch": "PLANNING",
    "paused": "PAUSED",
    "dropped": "DROPPED",
    "completed": "COMPLETED",
    "rewatching": "REPEATING",
    "repeating": "REPEATING",
}

def _anilist_target(value):
    target = str(value or "").lower()
    if target not in ANILIST_COMMAND_TARGETS:
        raise argparse.ArgumentTypeError(
            "use airing, watching, planning, completed, paused, dropped, or rewatching"
        )
    return target

def _auth_action(value):
    action = str(value or "").lower()
    if action not in ("login", "logout", "status", "token"):
        raise argparse.ArgumentTypeError("use login, logout, status, or token")
    return action

def _set_cli_defaults(parser):
    parser.set_defaults(
        query=[],
        episode=None,
        quality=None,
        dub=False,
        sub=False,
        binge=False,
        player=None,
        sources=False,
        download=False,
        downloads=False,
        sync=False,
        no_sync=False,
        login=False,
        logout=False,
        print_url=False,
        history=False,
        anilist=None,
        cont=False,
        cover=None,
        incognito=False,
        debug=False,
        json=False,
        completion_shell=None,
        completion_install=False,
        auth_status=False,
        auth_token=False,
        auth_token_raw=False,
        provider="allanime",
        auto_download=None,
        auto_delete=None,
    )


def _add_provider_option(group, suppress=False):
    group.add_argument(
        "-P", "--provider",
        metavar="PROVIDER",
        help=argparse.SUPPRESS if suppress else _provider_help(),
    )


def _add_debug_option(parser, *, suppress_default=False):
    kwargs = {
        "action": "store_true",
        "help": "Show full tracebacks on crash",
    }
    if suppress_default:
        kwargs["default"] = argparse.SUPPRESS
    parser.add_argument("--debug", **kwargs)


def _add_plain_option(parser, *, suppress_default=False):
    kwargs = {
        "dest": "plain",
        "action": "store_true",
        "help": "Run in plain text mode (disable full-screen TUI)",
    }
    if suppress_default:
        kwargs["default"] = argparse.SUPPRESS
    parser.add_argument("--plain", "--no-tui", **kwargs)


def _add_search_options(parser):
    playback = parser.add_argument_group("Playback options")
    playback.add_argument("-e", "--episode", type=str, help="Episode number or range")
    playback.add_argument(
        "-q", "--quality",
        choices=["best", "1080p", "720p", "480p"],
        metavar="QUALITY",
        help="Preferred quality: best, 1080p, 720p, 480p",
    )
    playback.add_argument("--dub", action="store_true", help="Prefer dubbed episodes")
    playback.add_argument("--sub", action="store_true", help="Prefer subtitled episodes")
    playback.add_argument("-b", "--binge", action="store_true", help="Continue through episodes")
    playback.add_argument(
        "-p", "--player",
        choices=["mpv", "rex", "mpvrx", "mpvrex", "vlc", "next"],
        metavar="PLAYER",
        help="Player: mpv, rex, mpvrx, vlc, next",
    )
    playback.add_argument(
        "-s", "--sources", action="store_true",
        help="Choose a mirror before playback",
    )
    playback.add_argument("--print-url", action="store_true", help="Print selected stream URL")
    playback.add_argument(
        "-d", "--download",
        action="store_true",
        help="Download episode(s) locally instead of playing",
    )
    playback.add_argument("--aniskip", dest="aniskip", action="store_true", default=None, help="Enable AniSkip")
    playback.add_argument("--no-aniskip", dest="aniskip", action="store_false", help="Disable AniSkip")
    playback.add_argument("--auto-skip", dest="auto_skip", action="store_true", default=None, help="Auto-skip OP/ED")
    playback.add_argument("--no-auto-skip", dest="auto_skip", action="store_false", help="Prompt before skipping OP/ED")
    playback.add_argument("--auto-download", dest="auto_download", action="store_true", default=None, help="Auto-download next episode")
    playback.add_argument("--no-auto-download", dest="auto_download", action="store_false", help="Do not auto-download next episode")
    playback.add_argument("--auto-delete", dest="auto_delete", action="store_true", default=None, help="Auto-delete watched downloaded episodes")
    playback.add_argument("--no-auto-delete", dest="auto_delete", action="store_false", help="Do not auto-delete watched downloaded episodes")

    download = parser.add_argument_group("Download options")
    download.add_argument(
        "--downloader",
        choices=["auto", "yt-dlp", "ffmpeg"],
        default=argparse.SUPPRESS,
        help="Downloader backend to use: auto (default), yt-dlp, ffmpeg",
    )
    download.add_argument(
        "--download-dir", "--dir",
        dest="download_dir",
        type=str,
        help="Custom download directory for this session",
    )
    download.add_argument(
        "--sub-langs",
        dest="download_sub_langs",
        type=str,
        default=argparse.SUPPRESS,
        help="Subtitle languages to download (e.g. 'eng,jpn' or 'multi')",
    )
    download.add_argument(
        "--audio-langs",
        dest="download_audio_langs",
        type=str,
        default=argparse.SUPPRESS,
        help="Audio languages to download (e.g. 'jpn,eng' or 'multi')",
    )
    download.add_argument(
        "--embed-subs",
        dest="subtitle_mode",
        action="store_const",
        const="embed",
        default=argparse.SUPPRESS,
        help="Embed subtitles directly into .mkv container",
    )
    download.add_argument(
        "--sidecar-subs",
        dest="subtitle_mode",
        action="store_const",
        const="sidecar",
        default=argparse.SUPPRESS,
        help="Save subtitles as sidecar files (.vtt/.srt) alongside video",
    )
    download.add_argument(
        "--no-subs",
        dest="download_sub_langs",
        action="store_const",
        const="none",
        default=argparse.SUPPRESS,
        help="Do not download subtitles",
    )

    tracking = parser.add_argument_group("Tracking options")
    tracking.add_argument(
        "-t", "--sync", dest="sync", action="store_true",
        help="Enable AniList sync",
    )
    tracking.add_argument(
        "--no-sync", dest="no_sync", action="store_true",
        help="Disable AniList sync",
    )

    output = parser.add_argument_group("Output options")
    output.add_argument("--cover", dest="cover", action="store_true", default=None, help="Show cover images")
    output.add_argument("--no-cover", dest="cover", action="store_false", help="Do not show cover images")
    output.add_argument("--json", action="store_true", help="Print search results as JSON")
    output.add_argument("--history", dest="show_search_history", action="store_true", help="Print search history and exit")
    output.add_argument("--clear-history", dest="clear_search_history", action="store_true", help="Clear search history and exit")
    if not getattr(parser, "_is_provider_parser", False):
        _add_provider_option(output)
    else:
        _add_provider_option(output, suppress=True)

    global_options = parser.add_argument_group("Global options")
    global_options.add_argument(
        "--incognito", action="store_true",
        help="Do not save history or update AniList",
    )
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )

def _add_download_options(parser):
    download = parser.add_argument_group("Download options")
    download.add_argument("-e", "--episode", type=str, help="Episode number or range")
    download.add_argument(
        "-q", "--quality",
        choices=["best", "1080p", "720p", "480p"],
        metavar="QUALITY",
        help="Preferred quality: best, 1080p, 720p, 480p",
    )
    download.add_argument("--dub", action="store_true", help="Prefer dubbed episodes")
    download.add_argument("--sub", action="store_true", help="Prefer subtitled episodes")
    download.add_argument(
        "-s", "--sources", action="store_true",
        help="Choose a mirror before downloading",
    )
    download.add_argument(
        "--downloader",
        choices=["auto", "yt-dlp", "ffmpeg"],
        default=argparse.SUPPRESS,
        help="Downloader to use: auto (default), yt-dlp, ffmpeg",
    )
    download.add_argument(
        "-d", "--download-dir", "--dir",
        dest="download_dir",
        type=str,
        help="Custom download directory for this session",
    )
    download.add_argument(
        "--sub-langs",
        dest="download_sub_langs",
        type=str,
        default=argparse.SUPPRESS,
        help="Subtitle languages to download (e.g. 'eng,jpn' or 'multi')",
    )
    download.add_argument(
        "--audio-langs",
        dest="download_audio_langs",
        type=str,
        default=argparse.SUPPRESS,
        help="Audio languages to download (e.g. 'jpn,eng' or 'multi')",
    )
    download.add_argument(
        "--embed-subs",
        dest="subtitle_mode",
        action="store_const",
        const="embed",
        default=argparse.SUPPRESS,
        help="Embed subtitles directly into .mkv container",
    )
    download.add_argument(
        "--sidecar-subs",
        dest="subtitle_mode",
        action="store_const",
        const="sidecar",
        default=argparse.SUPPRESS,
        help="Save subtitles as sidecar files (.vtt/.srt) alongside video",
    )
    download.add_argument(
        "--no-subs",
        dest="download_sub_langs",
        action="store_const",
        const="none",
        default=argparse.SUPPRESS,
        help="Do not download subtitles",
    )
    output = parser.add_argument_group("Output options")
    output.add_argument("--cover", dest="cover", action="store_true", default=None, help="Show cover images")
    output.add_argument("--no-cover", dest="cover", action="store_false", help="Do not show cover images")
    _add_provider_option(output)
    global_options = parser.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    parser.set_defaults(download=True)

def _add_anilist_options(parser, *, search=False):
    provider_group = parser.add_argument_group("Provider options")
    provider_group.add_argument(
        "-P", "--provider",
        choices=sorted(available_providers()),
        metavar="PROVIDER",
        help=_provider_help(),
    )
    provider_group.add_argument("--dub", action="store_true", help="Prefer dubbed episodes")
    provider_group.add_argument("--sub", action="store_true", help="Prefer subtitled episodes")

    output = parser.add_argument_group("Output options")
    output.add_argument("--cover", dest="cover", action="store_true", default=None, help="Show cover images")
    output.add_argument("--no-cover", dest="cover", action="store_false", help="Do not show cover images")
    output.add_argument("--json", action="store_true", help="Print results as JSON")
    global_options = parser.add_argument_group("Global options")
    global_options.add_argument(
        "--incognito", action="store_true",
        help="Do not save activity or update AniList",
    )
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )

def _add_resume_options(parser):
    playback = parser.add_argument_group("Playback options")
    playback.add_argument(
        "-q", "--quality",
        choices=["best", "1080p", "720p", "480p"],
        metavar="QUALITY",
        help="Preferred quality: best, 1080p, 720p, 480p",
    )
    playback.add_argument("--dub", action="store_true", help="Prefer dubbed episodes")
    playback.add_argument("--sub", action="store_true", help="Prefer subtitled episodes")
    playback.add_argument("-b", "--binge", action="store_true", help="Continue through episodes")
    playback.add_argument(
        "-p", "--player",
        choices=["mpv", "rex", "mpvrx", "mpvrex", "vlc", "next"],
        metavar="PLAYER",
        help="Player: mpv, rex, mpvrx, vlc, next",
    )
    playback.add_argument(
        "-s", "--sources", action="store_true",
        help="Choose a mirror before playback",
    )
    playback.add_argument("--print-url", action="store_true", help="Print selected stream URL")
    playback.add_argument("--aniskip", dest="aniskip", action="store_true", default=None, help="Enable AniSkip")
    playback.add_argument("--no-aniskip", dest="aniskip", action="store_false", help="Disable AniSkip")
    playback.add_argument("--auto-skip", dest="auto_skip", action="store_true", default=None, help="Auto-skip OP/ED")
    playback.add_argument("--no-auto-skip", dest="auto_skip", action="store_false", help="Prompt before skipping OP/ED")

    tracking = parser.add_argument_group("Tracking options")
    tracking.add_argument(
        "-t", "--sync", dest="sync", action="store_true",
        help="Enable AniList sync",
    )
    tracking.add_argument(
        "--no-sync", dest="no_sync", action="store_true",
        help="Disable AniList sync",
    )

    output = parser.add_argument_group("Output options")
    output.add_argument("--cover", dest="cover", action="store_true", default=None, help="Show cover images")
    output.add_argument("--no-cover", dest="cover", action="store_false", help="Do not show cover images")

    global_options = parser.add_argument_group("Global options")
    global_options.add_argument(
        "--incognito", action="store_true",
        help="Do not save history or update AniList",
    )
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
