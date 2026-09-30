"""Subcommand builders for parser."""

import argparse

from ..constants import APP_NAME
from ..providers import available_providers

from .args_common import (
    MinimalHelpFormatter,
    _add_anilist_options,
    _add_debug_option,
    _add_download_options,
    _add_plain_option,
    _add_resume_options,
    _add_search_options,
    _anilist_target,
    _auth_action,
    _configure_help_parser,
)


def add_search_subcommand(commands):
    search = commands.add_parser(
        "search",
        help="Search and watch anime",
        usage=f"{APP_NAME} search <query> [options]",
        description="Search and watch anime from Anime Providers.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} search slime\n"
            f"  {APP_NAME} search slime -e 3\n"
            f"  {APP_NAME} search slime -e 3 --dub -q 1080p\n"
            f'  {APP_NAME} search "one piece" -e 1000 -p mpv'
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(search)
    search._positionals.title = "Arguments"
    search.add_argument("query", nargs="*", help="Anime title to search")
    _add_search_options(search)
    return search


def add_provider_subcommands(commands):
    for provider_id, provider in sorted(available_providers().items()):
        provider_parser = commands.add_parser(
            provider_id,
            help="==SUPPRESS==",
            usage=f"{APP_NAME} {provider_id} <command> <query> [options]",
            description=(
                f"Search and watch anime from {provider.name}.\n\n"
                "Commands:\n"
                "  search          Search and stream anime\n"
                "  download        Download anime episodes"
            ),
            epilog=(
                "Examples:\n"
                f"  {APP_NAME} {provider_id} search renegade\n"
                f"  {APP_NAME} {provider_id} download renegade -e 3"
            ),
            add_help=False,
            formatter_class=MinimalHelpFormatter,
        )
        _configure_help_parser(provider_parser)
        provider_parser._positionals.title = "Arguments"
        provider_parser.add_argument(
            "provider_action",
            choices=["search", "download"],
            metavar="<command>",
            help=argparse.SUPPRESS,
        )
        provider_parser.add_argument("query", nargs="*", help="Anime title to search")
        provider_parser._is_provider_parser = True
        _add_search_options(provider_parser)
        provider_parser.set_defaults(provider=provider_id)


def add_download_subcommand(commands):
    download = commands.add_parser(
        "download",
        help="Download episodes",
        usage=f"{APP_NAME} download <query> [options]",
        description="Download anime episodes locally.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} download slime -e 3\n"
            f'  {APP_NAME} download "one piece" -e 1000 -q 720p'
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(download)
    download._positionals.title = "Arguments"
    download.add_argument("query", nargs="*", help="Anime title to search")
    _add_download_options(download)
    return download


def add_downloads_subcommand(commands):
    downloads = commands.add_parser(
        "downloads",
        help="Browse downloaded episodes",
        usage=f"{APP_NAME} downloads [options]",
        description="Browse and play downloaded episodes.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} downloads\n"
            f"  {APP_NAME} downloads -p vlc"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(downloads)
    downloads.set_defaults(downloads=True)
    downloads.add_argument(
        "-d", "--download-dir", "--dir",
        dest="download_dir",
        type=str,
        help="Custom download directory to browse",
    )
    playback = downloads.add_argument_group("Playback options")
    playback.add_argument(
        "-p", "--player",
        choices=["mpv", "rex", "mpvrx", "mpvrex", "vlc", "next"],
        metavar="PLAYER",
        help="Player: mpv, rex, mpvrx, vlc, next",
    )
    output = downloads.add_argument_group("Output options")
    output.add_argument("--cover", dest="cover", action="store_true", default=None, help="Show cover images")
    output.add_argument("--no-cover", dest="cover", action="store_false", help="Do not show cover images")
    global_options = downloads.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return downloads


def add_anilist_subcommand(commands):
    anilist = commands.add_parser(
        "anilist",
        help="Browse or search AniList",
        usage=(
            f"{APP_NAME} anilist [list]\n"
            f"       {APP_NAME} anilist search <query>"
        ),
        description=(
            "Browse your AniList library.\n\n"
            "Lists:\n"
            "  airing         Upcoming episodes from your lists\n"
            "  watching       Currently watching\n"
            "  planning       Planning to watch\n"
            "  completed      Completed\n"
            "  paused         Paused\n"
            "  dropped        Dropped\n"
            "  rewatching     Rewatching"
        ),
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} anilist\n"
            f"  {APP_NAME} anilist watching\n"
            f"  {APP_NAME} anilist airing\n"
            f"  {APP_NAME} anilist completed\n"
            f"  {APP_NAME} anilist search erased"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(anilist)
    anilist._positionals.title = "Arguments"
    anilist.add_argument(
        "target", nargs="?", default="menu", metavar="[list]",
        type=_anilist_target,
        help="List to open. Omit to show the AniList menu.",
    )
    _add_anilist_options(anilist)
    return anilist


def add_history_subcommand(commands):
    history = commands.add_parser(
        "history",
        help="Browse watch history",
        usage=f"{APP_NAME} history [options]",
        description="Browse your local watch history and resume a title.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} history\n"
            f"  {APP_NAME} history --cover\n"
            f"  {APP_NAME} history --incognito"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(history)
    history.set_defaults(history=True)
    _add_resume_options(history)
    return history


def add_continue_subcommand(commands):
    cont = commands.add_parser(
        "continue",
        help="Continue the last watched title",
        usage=f"{APP_NAME} continue [options]",
        description="Continue the most recently watched title.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} continue\n"
            f"  {APP_NAME} continue -q 720p\n"
            f"  {APP_NAME} continue --no-sync"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(cont)
    cont.set_defaults(cont=True)
    _add_resume_options(cont)
    return cont


def add_auth_subcommand(commands):
    auth = commands.add_parser(
        "auth",
        help="Login or logout from AniList",
        usage=f"{APP_NAME} auth <action>",
        description=(
            "Manage AniList authentication.\n\n"
            "Actions:\n"
            "  status         Show AniList authentication and storage status\n"
            "  login          Store an AniList token\n"
            "  logout         Remove the stored AniList token\n"
            "  token          Show the masked stored token\n"
            "  token --raw    Print the complete stored token"
        ),
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} auth status\n"
            f"  {APP_NAME} auth login\n"
            f"  {APP_NAME} auth logout\n"
            f"  {APP_NAME} auth token\n"
            f"  {APP_NAME} auth token --raw"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(auth)
    auth._positionals.title = "Arguments"
    auth.add_argument(
        "action", metavar="<action>", type=_auth_action,
        help="Authentication action to run",
    )
    auth_options = auth.add_argument_group("Auth options")
    auth_options.add_argument(
        "--raw", action="store_true",
        help="With auth token, print the complete stored token",
    )
    global_options = auth.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return auth


def add_providers_subcommand(commands):
    providers = commands.add_parser(
        "providers",
        help="List available providers",
        description="List available streaming providers.",
        add_help=False,
        epilog=(
            "Examples:\n"
            "  You can search a specific provider directly by using its ID as a command:\n"
            f"    {APP_NAME} allanime search frieren\n"
            f"    {APP_NAME} miruro search \"one piece\"\n\n"
            "  Alternatively, you can temporarily switch providers during a normal search:\n"
            f"    {APP_NAME} search frieren --provider miruro"
        ),
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(providers)
    providers.set_defaults(list_providers=True)

    global_options = providers.add_argument_group("Global options")
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    _add_debug_option(global_options)
    _add_plain_option(global_options)
    return providers


def add_completion_subcommand(commands):
    completion = commands.add_parser(
        "completion",
        help="Generate shell completion",
        usage=(
            f"{APP_NAME} completion <shell>\n"
            f"       {APP_NAME} completion install <shell>"
        ),
        description=(
            f"Generate shell completion for {APP_NAME}.\n\n"
            "Shells:\n"
            "  bash          Bash completion script\n"
            "  zsh           Zsh completion script\n"
            "  fish          Fish completion script\n\n"
            "Actions:\n"
            "  install       Install completion to a user-local shell path"
        ),
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} completion bash\n"
            f"  {APP_NAME} completion zsh\n"
            f"  {APP_NAME} completion fish\n"
            f"  {APP_NAME} completion install bash"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(completion)
    completion._positionals.title = "Arguments"
    completion.add_argument(
        "completion_args", nargs="+", metavar="<shell|install>",
        help="Shell to print, or install <shell>",
    )
    global_options = completion.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return completion


def add_config_subcommand(commands):
    config = commands.add_parser(
        "config",
        help="Manage CLI configuration",
        description=(
            "View, edit, or update configuration settings.\n\n"
            f"Running '{APP_NAME} config' without arguments opens config.conf in your editor.\n"
            "Use 'set' to update individual keys directly from the command line."
        ),
        usage=(
            f"{APP_NAME} config [options]\n"
            f"       {APP_NAME} config get <key>\n"
            f"       {APP_NAME} config set <key> <value>"
        ),
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} config\n"
            f"  {APP_NAME} config -e nano\n"
            f"  {APP_NAME} config --editor micro\n"
            f"  {APP_NAME} config get quality\n"
            f"  {APP_NAME} config set download_dir ~/Videos/{APP_NAME}\n"
            f"  {APP_NAME} config set quality 1080p\n"
            f"  {APP_NAME} config set player mpv"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(config)
    config._positionals.title = "Arguments"
    config.add_argument(
        "action",
        nargs="?",
        choices=["set", "get"],
        metavar="<action>",
        help="Action to perform: 'set', 'get'. Omit to open config file in editor.",
    )
    config.add_argument(
        "key",
        nargs="?",
        metavar="<key>",
        help="Configuration key to set (e.g. download_dir, quality, player)",
    )
    config.add_argument(
        "value",
        nargs="?",
        metavar="<value>",
        help="Value to assign to the key",
    )
    config_opts = config.add_argument_group("Config options")
    config_opts.add_argument(
        "-e", "--editor",
        dest="editor",
        metavar="EDITOR",
        help="Editor to open configuration file with (e.g., nano, micro, vim, code)",
    )
    global_options = config.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return config


def register_all_subcommands(commands):
    add_search_subcommand(commands)
    add_provider_subcommands(commands)
    add_download_subcommand(commands)
    add_downloads_subcommand(commands)
    add_anilist_subcommand(commands)
    add_history_subcommand(commands)
    add_continue_subcommand(commands)
    add_auth_subcommand(commands)
    add_providers_subcommand(commands)
    add_completion_subcommand(commands)
    add_config_subcommand(commands)
