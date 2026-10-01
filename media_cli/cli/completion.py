"""Static shell completion scripts for anyplay (and dynamic CLI names)."""

import os
from pathlib import Path

from ..constants import APP_NAME
from ..providers import available_providers

BASE_COMMANDS = (
    "search", "downloads", "anilist", "history", "continue",
    "auth", "providers", "completion", "config",
)
ANILIST_LISTS = (
    "airing", "watching", "planning", "completed", "dropped", "paused", "rewatching",
    "search",
)
AUTH_ACTIONS = ("login", "logout", "status", "token")
SHELLS = ("bash", "zsh", "fish")
QUALITIES = ("best", "1080p", "720p", "480p")
PLAYERS = ("mpv", "rex", "mpvrx", "mpvrex", "vlc", "next")
DOWNLOADERS = ("auto", "yt-dlp", "ffmpeg")
TRANSLATION_TYPES = ("sub", "dub", "raw")
SPINNERS = ("braille", "dots", "line", "arc", "simple")
ANILIST_SORTS = ("recent", "title", "score", "progress")
BOOLEANS = ("true", "false")

PROVIDERS = tuple(sorted(available_providers()))
COMMANDS = BASE_COMMANDS + PROVIDERS

ROOT_OPTIONS = ("-h", "--help", "-v", "--version", "--debug", "--plain", "--no-tui")

SEARCH_OPTIONS = (
    "-e", "--episode", "-q", "--quality", "--dub", "--sub", "-b", "--binge",
    "-p", "--player", "-s", "--sources", "--print-url", "-d", "--download",
    "--aniskip", "--no-aniskip", "--auto-skip", "--no-auto-skip",
    "--auto-download", "--no-auto-download", "--auto-delete", "--no-auto-delete",
    "--downloader", "--download-dir", "--dir",
    "--sub-langs", "--audio-langs", "--embed-subs", "--sidecar-subs", "--no-subs",
    "-t", "--sync", "--no-sync",
    "--cover", "--no-cover", "--json", "--history", "--clear-history",
    "-P", "--provider", "--incognito", "--debug", "--plain", "--no-tui",
    "-h", "--help",
)

DOWNLOAD_OPTIONS = SEARCH_OPTIONS

DOWNLOADS_OPTIONS = (
    "--download-dir", "--dir",
    "-p", "--player",
    "--cover", "--no-cover",
    "--debug", "--plain", "--no-tui", "-h", "--help",
)

ANILIST_OPTIONS = (
    "-P", "--provider", "--dub", "--sub",
    "--cover", "--no-cover", "--json",
    "--incognito", "--debug", "--plain", "--no-tui", "-h", "--help",
)

RESUME_OPTIONS = (
    "-q", "--quality", "--dub", "--sub", "-b", "--binge",
    "-p", "--player", "-s", "--sources", "--print-url",
    "--aniskip", "--no-aniskip", "--auto-skip", "--no-auto-skip",
    "-t", "--sync", "--no-sync",
    "--cover", "--no-cover", "--incognito",
    "--debug", "--plain", "--no-tui", "-h", "--help",
)

AUTH_OPTIONS = ("--raw", "--debug", "--plain", "--no-tui", "-h", "--help")

PROVIDERS_OPTIONS = ("--debug", "--plain", "--no-tui", "-h", "--help")

COMPLETION_OPTIONS = ("install", "--debug", "--plain", "--no-tui", "-h", "--help")

CONFIG_ACTIONS = ("set", "get")
CONFIG_OPTIONS = ("set", "get", "-e", "--editor", "--debug", "--plain", "--no-tui", "-h", "--help")
CONFIG_KEYS = (
    "quality", "translation_type", "player", "provider", "title_language",
    "download_dir", "auto_download_next", "auto_delete_watched", "auto_delete_buffer",
    "binge", "sync", "anilist_token", "anilist_sort", "anilist_sort_reverse",
    "cover", "spinner", "aniskip", "auto_skip",
    "download_sub_langs", "download_audio_langs", "subtitle_mode",
)


def _words(values):
    return " ".join(values)


def _case_words(values):
    return "|".join(values) or "__none__"


def bash_completion():
    fn_name = f"_{APP_NAME.replace('-', '_')}_completion"
    return f"""# {APP_NAME} bash completion
{fn_name}()
{{
    local cur prev cmd
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    cmd="${{COMP_WORDS[1]}}"

    if [[ $COMP_CWORD -eq 1 ]]; then
        COMPREPLY=( $(compgen -W "{_words(COMMANDS)} {_words(ROOT_OPTIONS)}" -- "$cur") )
        return 0
    fi

    case "$prev" in
        -q|--quality)
            COMPREPLY=( $(compgen -W "{_words(QUALITIES)}" -- "$cur") )
            return 0
            ;;
        -p|--player)
            COMPREPLY=( $(compgen -W "{_words(PLAYERS)}" -- "$cur") )
            return 0
            ;;
        -P|--provider)
            COMPREPLY=( $(compgen -W "{_words(PROVIDERS)}" -- "$cur") )
            return 0
            ;;
        --downloader)
            COMPREPLY=( $(compgen -W "{_words(DOWNLOADERS)}" -- "$cur") )
            return 0
            ;;
        --download-dir|--dir)
            COMPREPLY=( $(compgen -d -- "$cur") )
            return 0
            ;;
        -e|--editor)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -c -- "$cur") )
                return 0
            fi
            ;;
        install)
            if [[ "$cmd" == "completion" ]]; then
                COMPREPLY=( $(compgen -W "{_words(SHELLS)}" -- "$cur") )
                return 0
            fi
            ;;
        token)
            if [[ "$cmd" == "auth" ]]; then
                COMPREPLY=( $(compgen -W "{_words(AUTH_OPTIONS)}" -- "$cur") )
                return 0
            fi
            ;;
        set|get)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(CONFIG_KEYS)}" -- "$cur") )
                return 0
            fi
            ;;
        quality)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(QUALITIES)}" -- "$cur") )
                return 0
            fi
            ;;
        translation_type)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(TRANSLATION_TYPES)}" -- "$cur") )
                return 0
            fi
            ;;
        player)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(PLAYERS)}" -- "$cur") )
                return 0
            fi
            ;;
        provider)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(PROVIDERS)}" -- "$cur") )
                return 0
            fi
            ;;
        download_dir)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -d -- "$cur") )
                return 0
            fi
            ;;
        binge|sync|cover|aniskip|auto_skip|auto_download_next|auto_delete_watched|anilist_sort_reverse)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(BOOLEANS)}" -- "$cur") )
                return 0
            fi
            ;;
        spinner)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(SPINNERS)}" -- "$cur") )
                return 0
            fi
            ;;
        anilist_sort)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "{_words(ANILIST_SORTS)}" -- "$cur") )
                return 0
            fi
            ;;
        title_language)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "romaji english native" -- "$cur") )
                return 0
            fi
            ;;
        subtitle_mode)
            if [[ "$cmd" == "config" ]]; then
                COMPREPLY=( $(compgen -W "sidecar embed none" -- "$cur") )
                return 0
            fi
            ;;
    esac

    case "$cmd" in
        {_case_words(PROVIDERS)})
            if [[ $COMP_CWORD -eq 2 ]]; then
                COMPREPLY=( $(compgen -W "search" -- "$cur") )
            else
                COMPREPLY=( $(compgen -W "{_words(SEARCH_OPTIONS)}" -- "$cur") )
            fi
            ;;
        search)
            COMPREPLY=( $(compgen -W "{_words(SEARCH_OPTIONS)}" -- "$cur") )
            ;;
        downloads)
            COMPREPLY=( $(compgen -W "{_words(DOWNLOADS_OPTIONS)}" -- "$cur") )
            ;;
        anilist)
            COMPREPLY=( $(compgen -W "{_words(ANILIST_LISTS)} {_words(ANILIST_OPTIONS)}" -- "$cur") )
            ;;
        history|continue)
            COMPREPLY=( $(compgen -W "{_words(RESUME_OPTIONS)}" -- "$cur") )
            ;;
        auth)
            COMPREPLY=( $(compgen -W "{_words(AUTH_ACTIONS)} {_words(AUTH_OPTIONS)}" -- "$cur") )
            ;;
        providers)
            COMPREPLY=( $(compgen -W "{_words(PROVIDERS_OPTIONS)}" -- "$cur") )
            ;;
        completion)
            COMPREPLY=( $(compgen -W "{_words(SHELLS)} {_words(COMPLETION_OPTIONS)}" -- "$cur") )
            ;;
        config)
            COMPREPLY=( $(compgen -W "{_words(CONFIG_OPTIONS)}" -- "$cur") )
            ;;
    esac
}}
complete -F {fn_name} {APP_NAME}
"""


def zsh_completion():
    newline = "\n"
    return f"""#compdef {APP_NAME}

local -a commands
commands=(
  'search:Search and watch anime'
  'downloads:Browse downloaded episodes'
  'anilist:Browse or search AniList'
  'history:Browse watch history'
  'continue:Continue the last watched title'
  'auth:Login or logout from AniList'
  'providers:List available providers'
  'completion:Generate shell completion'
  'config:Manage or open configuration'
    {"".join(f"  '{provider}:Search {provider}'{newline}" for provider in PROVIDERS)}
)

case $words[2] in
  {'|'.join(PROVIDERS)})
    if [[ $CURRENT -eq 3 ]]; then
      _arguments '*:: :(search)'
    else
      _arguments '*:: :(({_words(SEARCH_OPTIONS)}))'
    fi
    ;;
  anilist)
    _arguments '*:: :(({_words(ANILIST_LISTS)} {_words(ANILIST_OPTIONS)}))'
    ;;
  auth)
    _arguments '*:: :(({_words(AUTH_ACTIONS)} {_words(AUTH_OPTIONS)}))'
    ;;
  providers)
    _arguments '*:: :(({_words(PROVIDERS_OPTIONS)}))'
    ;;
  completion)
    _arguments '*:: :(({_words(SHELLS)} {_words(COMPLETION_OPTIONS)}))'
    ;;
  config)
    _arguments '*:: :(({_words(CONFIG_OPTIONS)}))'
    ;;
  search)
    _arguments '*:: :(({_words(SEARCH_OPTIONS)}))'
    ;;
  downloads)
    _arguments '*:: :(({_words(DOWNLOADS_OPTIONS)}))'
    ;;
  history|continue)
    _arguments '*:: :(({_words(RESUME_OPTIONS)}))'
    ;;
  *)
    _describe 'command' commands
    ;;
esac
"""


def fish_completion():
    lines = [f"# {APP_NAME} fish completion"]
    for command in COMMANDS:
        lines.append(
            f"complete -c {APP_NAME} -n '__fish_use_subcommand' -a {command}"
        )
    for option in ROOT_OPTIONS:
        lines.append(f"complete -c {APP_NAME} -n '__fish_use_subcommand' -a {option}")
    for item in ANILIST_LISTS:
        lines.append(
            f"complete -c {APP_NAME} -n "
            "'__fish_seen_subcommand_from anilist' -a "
            f"{item}"
        )
    for item in AUTH_ACTIONS:
        lines.append(
            f"complete -c {APP_NAME} -n "
            "'__fish_seen_subcommand_from auth' -a "
            f"{item}"
        )
    for item in SHELLS:
        lines.append(
            f"complete -c {APP_NAME} -n "
            "'__fish_seen_subcommand_from completion' -a "
            f"{item}"
        )
    for item in CONFIG_ACTIONS:
        lines.append(
            f"complete -c {APP_NAME} -n "
            "'__fish_seen_subcommand_from config' -a "
            f"{item}"
        )
    for item in PROVIDERS:
        lines.append(
            f"complete -c {APP_NAME} -n "
            "'__fish_seen_argument -l provider' -a "
            f"{item}"
        )

    command_options = {
        "search": SEARCH_OPTIONS,
        "downloads": DOWNLOADS_OPTIONS,
        "anilist": ANILIST_OPTIONS,
        "history": RESUME_OPTIONS,
        "continue": RESUME_OPTIONS,
        "auth": AUTH_OPTIONS,
        "providers": PROVIDERS_OPTIONS,
        "completion": COMPLETION_OPTIONS,
        "config": CONFIG_OPTIONS,
    }
    for provider in PROVIDERS:
        lines.append(
            f"complete -c {APP_NAME} -n "
            f"'__fish_seen_subcommand_from {provider}' -a 'search'"
        )
        command_options[provider] = SEARCH_OPTIONS
    for command, options in command_options.items():
        for option in options:
            lines.append(
                f"complete -c {APP_NAME} -n "
                f"'__fish_seen_subcommand_from {command}' -a {option}"
            )
    return "\n".join(lines) + "\n"


def generate_completion(shell):
    shell = str(shell or "").lower()
    if shell == "bash":
        return bash_completion()
    if shell == "zsh":
        return zsh_completion()
    if shell == "fish":
        return fish_completion()
    raise ValueError(f"Unsupported shell: {shell}")


def completion_install_path(shell, home=None):
    shell = str(shell or "").lower()
    root = Path(home).expanduser() if home is not None else Path.home()
    prefix = os.environ.get("PREFIX", "")

    if shell == "bash":
        if prefix and os.path.isdir(os.path.join(prefix, "share/bash-completion/completions")):
            return Path(prefix) / f"share/bash-completion/completions/{APP_NAME}"
        return root / f".local/share/bash-completion/completions/{APP_NAME}"
    if shell == "zsh":
        if prefix and os.path.isdir(os.path.join(prefix, "share/zsh/site-functions")):
            return Path(prefix) / f"share/zsh/site-functions/_{APP_NAME}"
        return root / f".zfunc/_{APP_NAME}"
    if shell == "fish":
        return root / f".config/fish/completions/{APP_NAME}.fish"
    raise ValueError(f"Unsupported shell: {shell}")


def install_completion(shell, home=None):
    shell = str(shell or "").lower()
    path = completion_install_path(shell, home=home)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_completion(shell)
    path.write_text(content, encoding="utf-8")

    # If on Termux or multi-location environment, ensure fallback paths are also populated
    root = Path(home).expanduser() if home is not None else Path.home()
    if shell == "bash":
        alt_path = root / f".local/share/bash-completion/completions/{APP_NAME}"
        if alt_path != path:
            try:
                alt_path.parent.mkdir(parents=True, exist_ok=True)
                alt_path.write_text(content, encoding="utf-8")
            except Exception:
                pass
    elif shell == "zsh":
        alt_path = root / f".zfunc/_{APP_NAME}"
        if alt_path != path:
            try:
                alt_path.parent.mkdir(parents=True, exist_ok=True)
                alt_path.write_text(content, encoding="utf-8")
            except Exception:
                pass

    return path
