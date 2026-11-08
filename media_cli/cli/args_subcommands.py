"""Subcommand builders for parser."""

from ..constants import APP_NAME
from ..provider_manifest import available_provider_metadata
from .args_common import (
    ANILIST_COMMAND_TARGETS,
    MinimalHelpFormatter,
    _add_anilist_options,
    _add_debug_option,
    _add_download_options,
    _add_plain_option,
    _add_mpv_window_option,
    _add_resume_options,
    _add_search_options,
    _anilist_target,
    _auth_action,
    _configure_help_parser,
)


def add_search_subcommand(commands, provider_id=None):
    if provider_id == "animethemes":
        return add_animethemes_subcommand(commands)
    provider = available_provider_metadata().get(provider_id)
    query_usage = f"{provider_id}:<query>" if provider_id else "[provider:]<query>"
    search = commands.add_parser(
        "search",
        help="Search and watch anime",
        usage=f"{APP_NAME} search {query_usage} [options]",
        description=(f"Search and watch anime from {provider['name']}." if provider else
                     "Search using your default provider, or prefix the query with a provider ID."),
        epilog=(
            "Examples:\n"
            f'  {APP_NAME} search {provider_id}:"one piece"\n'
            f'  {APP_NAME} search {provider_id}:"one piece" -e 3 --download'
        ) if provider_id else (
            "Examples:\n"
            f"  {APP_NAME} search slime\n"
            f'  {APP_NAME} search animexin:"renegade immortal"\n'
            f"  {APP_NAME} search animethemes:idol\n"
            f"  {APP_NAME} search animexin: -h\n"
            f"  {APP_NAME} search slime -e 3\n"
            f"  {APP_NAME} search slime -e 3 --dub -q 1080p\n"
            f'  {APP_NAME} search "one piece" -e 1000 -p mpv'
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(search)
    search._positionals.title = "Arguments"
    search.add_argument("query", nargs="*", help="Search query, optionally prefixed with provider:")
    _add_search_options(search)
    if provider_id:
        search.set_defaults(provider=provider_id)
    return search


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
        "--download-dir", "--dir",
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
    _add_mpv_window_option(global_options, suppress_default=True)
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
            "Commands:\n"
            "  search <query> Search the AniList catalogue\n\n"
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
        choices=list(ANILIST_COMMAND_TARGETS),
        help="List to open. Omit to show the AniList menu.",
    )
    _add_anilist_options(anilist)
    return anilist


def add_animethemes_subcommand(commands):
    animethemes = commands.add_parser(
        "search",
        help="Search and play anime opening/ending videos",
        usage=f"{APP_NAME} search animethemes:<query> [options]",
        description="Search AnimeThemes and play anime opening/ending videos.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} search animethemes:idol\n"
            f'  {APP_NAME} search animethemes:"oshi no ko"'
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(animethemes)
    animethemes._positionals.title = "Arguments"
    animethemes.set_defaults(animethemes="search")
    animethemes.add_argument("query", nargs="*", help="Anime, song, or artist to search")
    playback = animethemes.add_argument_group("Playback options")
    playback.add_argument(
        "-p", "--player",
        choices=["mpv", "rex", "mpvrx", "mpvrex", "vlc", "next"],
        metavar="PLAYER",
        help="Player: mpv, rex, mpvrx, vlc, next",
    )
    global_options = animethemes.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return animethemes


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
        choices=["login", "logout", "status", "token"],
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
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return auth


def add_companion_subcommand(commands):
    companion = commands.add_parser(
        "companion",
        help="Pair or check the Android WebView companion",
        usage=f"{APP_NAME} companion <pair|status>",
        description=(
            "Manage the Android WebView companion from Termux.\n\n"
            "Actions:\n"
            "  pair           Connect once using a hidden pairing code\n"
            "  status         Check saved pairing and service status without starting it"
        ),
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} companion pair\n"
            f"  {APP_NAME} companion status"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(companion)
    companion._positionals.title = "Arguments"
    companion.add_argument("companion_action", choices=["pair", "status"], metavar="<action>")
    global_options = companion.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument("-h", "--help", action="help", help="Show this help message and exit")
    return companion


def add_providers_subcommand(commands):
    providers = commands.add_parser(
        "providers",
        help="List available providers",
        description="List available streaming providers.",
        add_help=False,
        epilog=(
            "Examples:\n"
            "  Prefix your query with a provider ID:\n"
            f"    {APP_NAME} search animexin:renegade immortal\n"
            f'    {APP_NAME} search anipm:"one piece"\n'
            f"    {APP_NAME} search animethemes:idol\n\n"
            "  Show provider-specific search help:\n"
            f"    {APP_NAME} search anipm: -h\n\n"
            "  Alternatively, you can temporarily switch providers during a normal search:\n"
            f"    {APP_NAME} search frieren --provider anipm"
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
    _add_mpv_window_option(global_options)
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
    _add_mpv_window_option(global_options, suppress_default=True)
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
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return config


def add_logs_subcommand(commands):
    logs = commands.add_parser(
        "logs",
        help="Print diagnostic log lines",
        usage=f"{APP_NAME} logs [--tail N]",
        description="Print anyplay.log to stdout for piping, or print only recent lines.",
        epilog=(
            "Examples:\n"
            f"  {APP_NAME} logs\n"
            f"  {APP_NAME} logs --tail 80"
        ),
        add_help=False,
        formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(logs)
    logs.set_defaults(logs=True)
    logs.add_argument(
        "--tail",
        dest="logs_tail",
        type=int,
        default=0,
        metavar="N",
        help="Print the last N lines from anyplay.log",
    )
    global_options = logs.add_argument_group("Global options")
    _add_debug_option(global_options, suppress_default=True)
    _add_plain_option(global_options, suppress_default=True)
    _add_mpv_window_option(global_options, suppress_default=True)
    global_options.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )
    return logs


def add_health_subcommand(commands):
    from ..health.profiles import PROFILES
    health = commands.add_parser(
        "health", help="Check providers and manage saved health reports",
        description="Check provider metadata, native catalogues, sources and streams, or manage saved reports. Use health all to check enabled providers sequentially; each keeps its supported depth. --translation both checks Sub and Dub separately. Supported depths depend on the provider. Senshi and other media-enabled adapters default to HTTP checks; --verify extraction stops before media access. --verify mpv silently decodes a short sample without a window or sound. AnimeThemes and Torrents currently offer catalogue/search-only checks; no torrent transfer is started. Visible playback checks are not available yet.\nView with: anyplay health report <id|latest>; q exits the pager.\nDelete one: anyplay health report <id|latest> --delete",
        add_help=False, formatter_class=MinimalHelpFormatter,
    )
    _configure_help_parser(health)
    health.add_argument("health_action", choices=("reports", "report", "all", *PROFILES), help="List/view reports or check a supported provider")
    health.add_argument("health_report_id", nargs="?", metavar="ID", help="Report ID or latest (required with report)")
    health.add_argument("--delete", dest="health_delete", action="store_true", help="Delete the selected report instead of viewing it")
    health.add_argument("--seed", dest="health_seed", type=int, help="Repeat episode sample selection with this nonnegative seed (provider checks)")
    health.add_argument("--verify", dest="health_verify", choices=("catalogue", "extraction", "http", "mpv"), help="Verification level (default depends on provider)")
    health.add_argument("--translation", dest="health_translation", choices=("sub", "dub", "both"), help="Check Sub, Dub, or both independently (default: sub); unsupported translations are not applicable")
    health.add_argument("-h", "--help", action="help", help="Show this help message and exit")
    _add_debug_option(health, suppress_default=True)
    _add_plain_option(health, suppress_default=True)
    return health


def register_all_subcommands(commands, search_provider=None):
    add_search_subcommand(commands, search_provider)
    add_downloads_subcommand(commands)
    add_anilist_subcommand(commands)
    add_history_subcommand(commands)
    add_continue_subcommand(commands)
    add_auth_subcommand(commands)
    add_companion_subcommand(commands)
    add_providers_subcommand(commands)
    add_completion_subcommand(commands)
    add_config_subcommand(commands)
    add_logs_subcommand(commands)
    add_health_subcommand(commands)
