"""Common argument parsing helpers, formatters, and reusable option groups."""

import argparse
import os
import re
import sys

from ..constants import APP_NAME

try:
    from .. import __version__
except Exception:
    try:
        import importlib.metadata

        __version__ = importlib.metadata.version(APP_NAME)
    except Exception:
        __version__ = "0.10.0"

from ..provider_manifest import available_provider_metadata

_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

COMMAND_NAMES = {
    "search", "download", "downloads", "anilist", "history", "continue",
    "auth", "companion", "providers", "completion", "config", "health", "theme",
}

COMPLETION_SHELLS = ("bash", "zsh", "fish")


def _provider_help():
    providers = ", ".join(sorted(available_provider_metadata()))
    return f"Streaming provider: {providers}" if providers else "Streaming provider"

def _provider_command_names():
    return set(available_provider_metadata())

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

    def _paint(self, text, role):
        if not self._minimal_color:
            return text
        colors = {
            "help.heading": "\033[1;34m",
            "help.option": "\033[1;32m",
            "help.example": "\033[1;36m",
            "help.program": "\033[1;35m",
        }
        return colors[role] + text + "\033[0m"

    def start_section(self, heading):
        heading = {"positional arguments": "Arguments", "arguments": "Arguments", "options": "Options", "Main commands": "Commands"}.get(heading, heading)
        if heading.endswith(" options"):
            heading = heading[:-8]
        super().start_section(self._paint(heading, "help.heading"))

    def _format_action_invocation(self, action):
        if action.option_strings:
            return ', '.join(action.option_strings)
        return super()._format_action_invocation(action)

    def _format_action(self, action):
        import textwrap
        # Use the same description column for every section on this page.
        position = 20 + max(0, self._current_indent - 2)
        stacked = self._width - position < 32
        if stacked:
            position = self._current_indent + 4
        width = max(8, self._width - position)
        name = self._format_action_invocation(action)
        available = max(8, (self._width - self._current_indent) if stacked else position - self._current_indent - 2)
        names = textwrap.wrap(name, available, break_long_words=False, break_on_hyphens=False) or ['']
        result = ''.join(' ' * self._current_indent + self._paint(part, 'help.option') + '\n' for part in names[:-1])
        last = names[-1]
        result += ' ' * self._current_indent + self._paint(last, 'help.option')
        if not action.help:
            result += '\n'
        else:
            description = self._expand_help(action)
            if action.option_strings and action.nargs != 0:
                if description and description[-1] not in '.!?':
                    description += '.'
                if action.choices is not None:
                    description += " Values: " + ', '.join(str(value) for value in action.choices) + '.'
                else:
                    value = action.metavar or action.dest.upper()
                    description += f" {'Optional value' if action.nargs == '?' else 'Value'}: {value}."
            lines = self._split_lines(description, width)
            if stacked or len(last) > available:
                result += '\n'
                result += ''.join(' ' * position + line + '\n' for line in lines)
            elif lines:
                result += ' ' * max(2, position - self._current_indent - len(last)) + lines[0] + '\n'
                result += ''.join(' ' * position + line + '\n' for line in lines[1:])
            else:
                result += '\n'
        for subaction in self._iter_indented_subactions(action):
            result += self._format_action(subaction)
        return result

    def _format_usage(self, usage, actions, groups, prefix):
        text = super()._format_usage(usage, actions, groups, prefix)
        if self._minimal_color:
            text = re.sub(r'^usage:', lambda _: self._paint('Usage:', 'help.heading'), text)
            text = text.replace(self._prog, self._paint(self._prog, 'help.program'))
        return text

    def _fill_text(self, text, width, indent):
        import textwrap
        lines = []
        for line in text.splitlines():
            plain = line.rstrip()
            if plain in ("Lists:", "Actions:", "Examples:", "Commands:", "Quick start:"):
                lines.append(indent + self._paint(plain, "help.heading"))
            elif plain.lstrip().startswith(APP_NAME + ' '):
                wrapped = textwrap.fill(plain, max(8, width), initial_indent=indent, subsequent_indent=indent + '    ')
                lines.append(self._paint(wrapped, "help.example"))
            elif plain.startswith('  '):
                match = re.match(r'(\s*\S+)(\s{2,})(.*)', plain)
                if match:
                    label = match[1].strip()
                    position = 20
                    stacked = width - position < 32
                    if stacked:
                        lines.append(indent + '  ' + self._paint(label, 'help.option'))
                        lines.append(textwrap.fill(match[3], max(8, width), initial_indent=indent + '      ', subsequent_indent=indent + '      '))
                    else:
                        parts = textwrap.wrap(match[3], max(8, width - position))
                        lines.append(indent + '  ' + self._paint(label, 'help.option') + ' ' * max(2, position - 2 - len(label)) + (parts[0] if parts else ''))
                        lines.extend(indent + ' ' * position + part for part in parts[1:])
                else:
                    lines.append(indent + plain)
            elif plain:
                lines.append(textwrap.fill(plain, max(8, width), initial_indent=indent, subsequent_indent=indent))
            else:
                lines.append('')
        return '\n'.join(lines)

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
    parser._positionals.title = "Arguments"
    parser._optionals.title = "Options"
    if hasattr(parser, "color"):
        parser.color = False
    return parser

class CLIParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def _parse_optional(self, arg_string):
        # argparse otherwise treats -expanded as -e with value "xpanded".
        # Reject misspelled long-option syntax before short-option expansion.
        if (arg_string.startswith('-') and not arg_string.startswith('--')
                and arg_string not in self._option_string_actions):
            flag = arg_string.partition('=')[0]
            short = self._option_string_actions.get(arg_string[:2])
            attached_text = (short is not None and short.nargs != 0
                             and len(arg_string) > 2 and arg_string[2] != '='
                             and not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', arg_string[2:]))
            if '-' + flag in self._option_string_actions or attached_text:
                self.error('unrecognized arguments: ' + arg_string)
        return super()._parse_optional(arg_string)

    def error(self, message):
        if message.startswith("unrecognized arguments: "):
            import shlex
            from .suggestions import suggest

            arguments = message.removeprefix("unrecognized arguments: ")
            try:
                unknown = shlex.split(arguments)
            except ValueError:
                unknown = arguments.split()
            options = [word for word in unknown if word.startswith("-")]
            if options:
                for option in options:
                    flag, separator, value = option.partition("=")
                    print(f"\n\033[31mUnknown option:\033[0m {flag}\n")
                    matches = suggest(flag, self._option_string_actions)
                    if matches:
                        print("Did you mean:")
                        for match in matches:
                            print(f"  {match}{separator}{value}")
                        print()
                print(f"Run '{self.prog} -h' for help.\n")
                self.exit(2)
        super().error(message)

    def _check_value(self, action, value):
        if action.choices is not None and value not in action.choices:
            from .suggestions import suggest
            import shlex

            if action.dest == "command":
                label = "Unknown command"
            elif action.option_strings:
                label = "Invalid " + action.dest.replace("_", " ")
            else:
                label = "Unknown " + ({"target": "AniList list", "action": "action"}.get(action.dest, action.dest))
            print(f"\n\033[31m{label}:\033[0m {value}\n")
            exact = getattr(self, "_command_suggestion", None)
            matches = suggest(str(value), action.choices)
            if exact or matches:
                print("Did you mean:")
                if exact:
                    print(f"  {exact}")
                else:
                    for match in matches:
                        correction = match if action.option_strings else f"{self.prog} {shlex.quote(match)}"
                        print(f"  {correction}")
                print()
            print(f"Run '{self.prog} -h' for help.\n")
            self.exit(2)
        return super()._check_value(action, value)

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
    return str(value or "").lower()

def _auth_action(value):
    return str(value or "").lower()

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
        provider=None,
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


def _add_mpv_window_option(parser, *, suppress_default=False):
    parser.add_argument(
        "--mpv-window", choices=("ready", "immediate"),
        default=argparse.SUPPRESS if suppress_default else "ready",
        help="Desktop mpv window: ready (default) or immediate while loading",
    )


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
    _add_mpv_window_option(global_options, suppress_default=True)
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
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    parser.set_defaults(download=True)

def _add_anilist_options(parser, *, search=False):
    provider_group = parser.add_argument_group("Provider options")
    provider_group.add_argument(
        "-P", "--provider",
        choices=sorted(available_provider_metadata()),
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
    _add_mpv_window_option(global_options, suppress_default=True)
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
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
